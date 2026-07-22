import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import get_platform
from ..models import CachedFile, DownloadJob, JobFile, User, UserDownload, UserSettings
from ..schemas.downloads import (
    DownloadCreatedResponse,
    DownloadRequest,
    FileInfo,
    FileReportRequest,
    JobStatusResponse,
)
from ..services.queue import download_queue

router = APIRouter(tags=["downloads"])


async def _log_history(
    db: AsyncSession,
    user_id: int,
    source_id: str,
    content_type: str,
    quality: str,
    title: str | None,
    artist: str | None,
) -> int:
    """Upsert a history row (dedup per user+source+type+quality). Returns download_id."""
    existing = (
        await db.execute(
            select(UserDownload).where(
                UserDownload.user_id == user_id,
                UserDownload.source_id == source_id,
                UserDownload.content_type == content_type,
                UserDownload.quality == quality,
            )
        )
    ).scalars().first()
    if existing:
        existing.downloaded_at = func.now()
        if title:
            existing.title = title
        if artist:
            existing.artist = artist
        await db.flush()
        return existing.download_id
    row = UserDownload(
        user_id=user_id,
        source_id=source_id,
        content_type=content_type,
        quality=quality,
        title=title,
        artist=artist,
    )
    db.add(row)
    await db.flush()
    return row.download_id


def _job_files_to_infos(files: list[JobFile]) -> list[FileInfo]:
    prefix = get_settings().API_V1_PREFIX
    return [
        FileInfo(
            kind=f.kind,
            title=f.title,
            artist=f.artist,
            duration=f.duration,
            size=f.size,
            platform_file_id=None,
            temp_url=f"{prefix}/files/{f.token}",
        )
        for f in files
    ]


@router.post("/downloads", response_model=DownloadCreatedResponse)
async def create_download(
    body: DownloadRequest,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> DownloadCreatedResponse:
    if body.content_type not in ("track", "album", "playlist"):
        raise HTTPException(status_code=400, detail="content_type must be track/album/playlist")
    if await db.get(User, body.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    # fall back to user settings for quality / as_zip
    settings_row = await db.get(UserSettings, body.user_id)
    quality = body.quality or (settings_row.quality if settings_row else "MP3_320")
    as_zip = body.as_zip if body.as_zip is not None else (settings_row.make_zip if settings_row else True)

    # 1) cache hit? -> answer instantly with platform_file_id
    result = await db.execute(
        select(CachedFile).where(
            CachedFile.source_id == body.source_id,
            CachedFile.content_type == body.content_type,
            CachedFile.quality == quality,
            CachedFile.platform == platform,
        )
    )
    cached = result.scalars().all()
    if cached:
        # log history for cache hits too (dedup + returns id for the rating UI)
        download_id = await _log_history(
            db,
            user_id=body.user_id,
            source_id=body.source_id,
            content_type=body.content_type,
            quality=quality,
            title=cached[0].title,
            artist=cached[0].artist,
        )
        await db.commit()
        return DownloadCreatedResponse(
            cached=True,
            download_id=download_id,
            job_id=None,
            files=[
                FileInfo(
                    kind=c.kind,
                    title=c.title,
                    artist=c.artist,
                    duration=c.duration,
                    platform_file_id=c.platform_file_id,
                    temp_url=None,
                )
                for c in cached
            ],
        )

    # 2) no cache -> create job and enqueue
    job_id = f"j_{uuid.uuid4().hex[:12]}"
    job = DownloadJob(
        job_id=job_id,
        user_id=body.user_id,
        platform=platform,
        content_type=body.content_type,
        source_id=body.source_id,
        quality=quality,
        as_zip=as_zip,
        status="queued",
        callback_url=body.callback_url,  # stored, webhook delivery comes later
    )
    db.add(job)
    await db.commit()
    await download_queue.enqueue(job_id)
    return DownloadCreatedResponse(cached=False, job_id=job_id, status="queued")


@router.get("/downloads/{job_id}", response_model=JobStatusResponse)
async def get_download(
    job_id: str,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    files = _job_files_to_infos(job.files) if job.status == "ready" else None
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
        error=job.error,
        files=files,
    )


@router.delete("/downloads/{job_id}", response_model=JobStatusResponse)
async def cancel_download(
    job_id: str,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("queued", "processing"):
        download_queue.cancel(job_id)
        if job.status == "queued":
            job.status = "cancelled"
            await db.commit()
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
        error=job.error,
        files=None,
    )


@router.get("/files/{token}")
async def get_file(token: str, platform: str = Depends(get_platform), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(JobFile).where(JobFile.token == token))
    jf = result.scalar_one_or_none()
    if jf is None:
        raise HTTPException(status_code=404, detail="File not found or expired")
    path = Path(jf.temp_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="File expired")
    return FileResponse(path, filename=path.name)


@router.post("/files/report")
async def report_file(
    body: FileReportRequest,
    platform: str = Depends(get_platform),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Close the cache loop: store platform_file_id and log user history."""
    result = await db.execute(
        select(CachedFile).where(
            CachedFile.source_id == body.source_id,
            CachedFile.content_type == body.content_type,
            CachedFile.quality == body.quality,
            CachedFile.platform == platform,
            CachedFile.kind == body.kind,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.platform_file_id = body.platform_file_id
    else:
        db.add(
            CachedFile(
                source_id=body.source_id,
                content_type=body.content_type,
                quality=body.quality,
                platform=platform,
                platform_file_id=body.platform_file_id,
                kind=body.kind,
                title=body.title,
                artist=body.artist,
                duration=body.duration,
            )
        )
    download_id = await _log_history(
        db,
        user_id=body.user_id,
        source_id=body.source_id,
        content_type=body.content_type,
        quality=body.quality,
        title=body.title,
        artist=body.artist,
    )
    await db.commit()
    return {"ok": True, "download_id": download_id}
