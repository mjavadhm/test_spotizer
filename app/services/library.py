"""Persistent track library — powers /tracks/{id}/stream and /tracks/{id}/download.

Design (adapted from the old Spotizer `backend/routers/stream.py`):
- If a track is already in the library -> serve it from disk immediately.
- If not -> trigger an on-demand download (deduped: concurrent requests for the
  same (track_id, quality) share ONE download task), store the file permanently
  under MUSIC_DIR, then serve it.
- Regular bot download jobs also archive single tracks into the library, so the
  stream cache grows for free.
"""

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import async_session_maker
from ..models import LibraryTrack
from .deezer import deezer_client, deezer_url_for
from .downloader import downloader

logger = logging.getLogger("spotizer.library")


class TrackNotAvailable(Exception):
    """The track could not be downloaded (bad id, region lock, deemix error...)."""


class Library:
    def __init__(self) -> None:
        self.settings = get_settings()
        # (track_id, quality) -> shared download task, so N clients = 1 download
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(self.settings.MAX_CONCURRENT_DOWNLOADS)

    def music_dir(self) -> Path:
        return Path(self.settings.MUSIC_DIR)

    # ---------- lookup ----------

    async def get_cached(self, track_id: str, quality: str) -> LibraryTrack | None:
        """Return the library row if it exists AND the file is still on disk."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(LibraryTrack).where(
                    LibraryTrack.track_id == str(track_id),
                    LibraryTrack.quality == quality,
                )
            )
            row = result.scalar_one_or_none()
        if row is None:
            return None
        if Path(row.file_path).exists():
            return row
        # stale row (file removed manually) -> drop it so it can be re-downloaded
        async with async_session_maker() as session:
            stale = await session.get(LibraryTrack, row.id)
            if stale is not None:
                await session.delete(stale)
                await session.commit()
        return None

    # ---------- on-demand download ----------

    async def ensure_track(self, track_id: str, quality: str) -> LibraryTrack:
        """Cache hit -> return immediately. Miss -> download once, share the task."""
        cached = await self.get_cached(track_id, quality)
        if cached is not None:
            return cached

        key = (str(track_id), quality)
        task = self._inflight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._download(str(track_id), quality))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        # shield: a client disconnect / timeout must not cancel the shared download
        return await asyncio.shield(task)

    async def _download(self, track_id: str, quality: str) -> LibraryTrack:
        async with self._semaphore:
            tmp_dir = Path(self.settings.TEMP_DIR) / "ondemand" / uuid.uuid4().hex
            try:
                url = deezer_url_for("track", track_id)
                logger.info("on-demand download: track %s (%s)", track_id, quality)
                result = await downloader.download(url, quality, tmp_dir)
                if not result.success:
                    raise TrackNotAvailable(result.error or "Download failed")
                audio = next((f for f in result.files if f.kind == "audio"), None)
                if audio is None:
                    raise TrackNotAvailable("No audio produced")
                lrc = next((f for f in result.files if f.kind == "lrc"), None)
                return await self.store_file(
                    track_id,
                    quality,
                    audio.path,
                    lrc_src=lrc.path if lrc else None,
                    title=audio.title,
                    artist=audio.artist,
                    duration=audio.duration,
                )
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ---------- storage ----------

    async def store_file(
        self,
        track_id: str,
        quality: str,
        src: Path,
        *,
        lrc_src: Path | None = None,
        title: str | None = None,
        artist: str | None = None,
        duration: int | None = None,
        keep_source: bool = False,
    ) -> LibraryTrack:
        """Move (or copy, when the source is a job temp file that must survive)
        an audio file into MUSIC_DIR and upsert the library row."""
        src = Path(src)
        dest_dir = self.music_dir() / str(track_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{track_id}_{quality}{src.suffix.lower()}"

        transfer = shutil.copy2 if keep_source else shutil.move
        await asyncio.to_thread(transfer, str(src), str(dest))

        lrc_dest: Path | None = None
        if lrc_src is not None and Path(lrc_src).exists():
            lrc_dest = dest.with_suffix(".lrc")
            await asyncio.to_thread(transfer, str(lrc_src), str(lrc_dest))

        meta = await self._fetch_meta(track_id)
        size = dest.stat().st_size

        async with async_session_maker() as session:
            result = await session.execute(
                select(LibraryTrack).where(
                    LibraryTrack.track_id == str(track_id),
                    LibraryTrack.quality == quality,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = LibraryTrack(track_id=str(track_id), quality=quality, file_path=str(dest))
                session.add(row)
            row.file_path = str(dest)
            row.format = dest.suffix.lstrip(".").lower()
            if lrc_dest is not None:
                row.lrc_path = str(lrc_dest)
            row.title = title or meta.get("title") or row.title
            row.artist = artist or meta.get("artist") or row.artist
            row.album = meta.get("album") or row.album
            row.cover = meta.get("cover") or row.cover
            row.duration = duration or meta.get("duration") or row.duration
            row.size = size
            await session.commit()
            await session.refresh(row)
        return row

    async def bump_play_count(self, row_id: int) -> None:
        """Best-effort play counter — never breaks streaming."""
        try:
            async with async_session_maker() as session:
                row = await session.get(LibraryTrack, row_id)
                if row is not None:
                    row.play_count = (row.play_count or 0) + 1
                    await session.commit()
        except Exception:  # noqa: BLE001
            logger.debug("bump_play_count failed", exc_info=True)

    # ---------- metadata ----------

    async def _fetch_meta(self, track_id: str) -> dict:
        """Best-effort album/cover/etc from the public Deezer API."""
        try:
            data = await deezer_client.get_track(str(track_id))
        except Exception:  # noqa: BLE001
            return {}
        return {
            "title": data.get("title"),
            "artist": (data.get("artist") or {}).get("name"),
            "album": (data.get("album") or {}).get("title"),
            "cover": (data.get("album") or {}).get("cover_big"),
            "duration": data.get("duration"),
        }


library = Library()
