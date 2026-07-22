"""In-process download queue.

- plain asyncio.Queue — no Redis/Celery
- limited concurrency (MAX_CONCURRENT_DOWNLOADS workers)
- cancellation via a flag checked between steps
"""

import asyncio
import logging
import shutil
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import async_session_maker
from ..models import DownloadJob, JobFile
from .deezer import deezer_url_for
from .downloader import downloader
from .library import library

logger = logging.getLogger("spotizer.queue")


class DownloadQueue:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.cancelled: set[str] = set()
        self.workers: list[asyncio.Task] = []

    # ---------- lifecycle ----------

    async def start(self) -> None:
        for i in range(self.settings.MAX_CONCURRENT_DOWNLOADS):
            self.workers.append(asyncio.create_task(self._worker(i)))
        logger.info("download queue started with %d workers", len(self.workers))

    async def stop(self) -> None:
        for task in self.workers:
            task.cancel()
        self.workers.clear()

    # ---------- public API ----------

    async def enqueue(self, job_id: str) -> None:
        await self.queue.put(job_id)

    def cancel(self, job_id: str) -> None:
        self.cancelled.add(job_id)

    # ---------- internals ----------

    async def _worker(self, index: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("worker %d: job %s crashed", index, job_id)
                await self._update_job(job_id, status="failed", error="Internal error")
            finally:
                self.queue.task_done()
                self.cancelled.discard(job_id)

    async def _update_job(self, job_id: str, **values) -> None:
        async with async_session_maker() as session:
            job = await session.get(DownloadJob, job_id)
            if job is None:
                return
            for key, value in values.items():
                setattr(job, key, value)
            await session.commit()

    async def _process(self, job_id: str) -> None:
        async with async_session_maker() as session:
            job = await session.get(DownloadJob, job_id)
        if job is None or job.status != "queued":
            return
        if job_id in self.cancelled:
            await self._update_job(job_id, status="cancelled", finished_at=datetime.now(timezone.utc))
            return

        await self._update_job(job_id, status="processing", progress=5, current_step="Starting download")

        job_dir = Path(self.settings.TEMP_DIR) / "jobs" / job_id
        url = deezer_url_for(job.content_type, job.source_id)

        await self._update_job(job_id, progress=15, current_step="Downloading from Deezer")
        result = await downloader.download(url, job.quality, job_dir)

        if job_id in self.cancelled:
            shutil.rmtree(job_dir, ignore_errors=True)
            await self._update_job(job_id, status="cancelled", finished_at=datetime.now(timezone.utc))
            return

        if not result.success:
            shutil.rmtree(job_dir, ignore_errors=True)
            await self._update_job(
                job_id,
                status="failed",
                error=result.error or "Download failed",
                finished_at=datetime.now(timezone.utc),
            )
            return

        await self._update_job(job_id, progress=80, current_step="Preparing files")

        audio_files = [f for f in result.files if f.kind == "audio"]

        # archive single tracks into the permanent library so future
        # /tracks/{id}/stream and /tracks/{id}/download requests are instant
        if job.content_type == "track" and audio_files:
            try:
                lrc_files = [f for f in result.files if f.kind == "lrc"]
                await library.store_file(
                    job.source_id,
                    job.quality,
                    audio_files[0].path,
                    lrc_src=lrc_files[0].path if lrc_files else None,
                    title=audio_files[0].title,
                    artist=audio_files[0].artist,
                    duration=audio_files[0].duration,
                    keep_source=True,  # job temp file must stay for temp_url
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to archive track %s into library", job.source_id)

        extra_files = [f for f in result.files if f.kind != "audio"]
        produced = list(result.files)

        # zip albums/playlists when requested
        if job.as_zip and job.content_type in ("album", "playlist") and len(audio_files) > 1:
            await self._update_job(job_id, progress=90, current_step="Creating zip")
            zip_path = job_dir / f"{job.content_type}_{job.source_id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                for f in audio_files + extra_files:
                    zf.write(f.path, arcname=f.path.name)
            from .downloader import DownloadedFile

            produced = [
                DownloadedFile(
                    path=zip_path,
                    kind="zip",
                    title=audio_files[0].artist or zip_path.stem,
                    size=zip_path.stat().st_size,
                )
            ]

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.settings.FILE_TTL_MINUTES)
        async with async_session_maker() as session:
            for f in produced:
                session.add(
                    JobFile(
                        job_id=job_id,
                        kind=f.kind,
                        title=f.title,
                        artist=f.artist,
                        duration=f.duration,
                        size=f.size,
                        temp_path=str(f.path),
                        token=uuid.uuid4().hex,
                        expires_at=expires_at,
                    )
                )
            await session.commit()

        await self._update_job(
            job_id,
            status="ready",
            progress=100,
            current_step=None,
            finished_at=datetime.now(timezone.utc),
        )


async def requeue_stale_jobs(queue: "DownloadQueue") -> None:
    """On startup, re-enqueue jobs that were queued when the server stopped."""
    async with async_session_maker() as session:
        result = await session.execute(select(DownloadJob.job_id).where(DownloadJob.status == "queued"))
        for (job_id,) in result.all():
            await queue.enqueue(job_id)


download_queue = DownloadQueue()
