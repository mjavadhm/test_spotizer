"""Track endpoints for the Android app: status / stream / download / lyrics.

Streaming behaviour is ported from the old Spotizer `backend/routers/stream.py`:
- HTTP Range support (206 Partial Content) so players can seek
- on-demand download when the track is not cached yet (listen first, then save)
The difference: files are served from the local library (MUSIC_DIR) instead of
Telegram/Telethon, so no bot round-trip is needed.
"""

import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..deps import get_platform
from ..models import LibraryTrack, User, UserDownload
from ..schemas.tracks import TrackStatusResponse
from ..services.library import TrackNotAvailable, library

router = APIRouter(prefix="/tracks", tags=["tracks"], dependencies=[Depends(get_platform)])
logger = logging.getLogger("spotizer.tracks")

VALID_QUALITIES = ("MP3_128", "MP3_320", "FLAC")
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}
CHUNK_SIZE = 256 * 1024


# ---------- helpers ----------

def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse a Range header -> (start, end). Supports bytes=a-b, bytes=a-, bytes=-n."""
    if not range_header or not range_header.startswith("bytes="):
        return 0, file_size - 1
    spec = range_header[6:].split(",")[0].strip()  # multi-range: take the first
    try:
        if spec.startswith("-"):
            suffix = int(spec[1:])
            start, end = max(0, file_size - suffix), file_size - 1
        elif spec.endswith("-"):
            start, end = int(spec[:-1]), file_size - 1
        else:
            first, last = spec.split("-", 1)
            start = int(first)
            end = int(last) if last else file_size - 1
    except ValueError:
        return 0, file_size - 1
    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))
    return start, end


def _iter_file(path: Path, start: int, length: int):
    """Plain (sync) generator — Starlette streams it from a threadpool."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _safe_filename(track: LibraryTrack, path: Path) -> str:
    base = " - ".join(x for x in (track.artist, track.title) if x) or f"track_{track.track_id}"
    base = re.sub(r'[\\/:*?"<>|]+', "_", base).strip()
    return f"{base}{path.suffix.lower()}"


def _check_quality(quality: str) -> str:
    if quality not in VALID_QUALITIES:
        raise HTTPException(status_code=400, detail=f"quality must be one of {'/'.join(VALID_QUALITIES)}")
    return quality


async def _ensure(track_id: str, quality: str) -> LibraryTrack:
    """Cache hit -> instant. Miss -> on-demand download with a hard timeout."""
    settings = get_settings()
    try:
        return await asyncio.wait_for(
            library.ensure_track(track_id, quality),
            timeout=settings.ON_DEMAND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # the shared download keeps running (shielded); the client can retry
        raise HTTPException(
            status_code=504,
            detail="Track is still being prepared, retry in a few seconds",
        )
    except TrackNotAvailable as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _range_response(
    track: LibraryTrack,
    range_header: str | None,
    *,
    as_attachment: bool = False,
) -> StreamingResponse:
    path = Path(track.file_path)
    file_size = path.stat().st_size
    media_type = _media_type(path)

    extra: dict[str, str] = {}
    if as_attachment:
        filename = _safe_filename(track, path)
        ascii_name = filename.encode("ascii", "replace").decode()
        from urllib.parse import quote

        extra["Content-Disposition"] = (
            f'attachment; filename="{ascii_name}"; ' f"filename*=UTF-8''{quote(filename)}"
        )

    if range_header:
        start, end = _parse_range_header(range_header, file_size)
        length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            **extra,
        }
        return StreamingResponse(
            _iter_file(path, start, length),
            status_code=206,
            media_type=media_type,
            headers=headers,
        )

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), **extra}
    return StreamingResponse(_iter_file(path, 0, file_size), media_type=media_type, headers=headers)


# ---------- endpoints ----------

@router.get("/{track_id}/status", response_model=TrackStatusResponse)
async def track_status(track_id: str, quality: str = Query("MP3_320")) -> TrackStatusResponse:
    """Is this track ready on the server? (lets the app show 'instant' vs 'preparing')."""
    quality = _check_quality(quality)
    track = await library.get_cached(track_id, quality)
    if track is None:
        return TrackStatusResponse(track_id=str(track_id), quality=quality, cached=False)
    return TrackStatusResponse(
        track_id=track.track_id,
        quality=track.quality,
        cached=True,
        title=track.title,
        artist=track.artist,
        album=track.album,
        cover=track.cover,
        duration=track.duration,
        size=track.size,
        format=track.format,
        has_lyrics=bool(track.lrc_path and Path(track.lrc_path).exists()),
    )


@router.get("/{track_id}/stream")
async def stream_track(
    track_id: str,
    quality: str = Query("MP3_320"),
    range_header: str | None = Header(None, alias="Range"),
):
    """Stream a track with seeking support (listen BEFORE downloading).

    Cache miss triggers an on-demand download; concurrent listeners share it.
    Use with ExoPlayer/Media3: just point the player at this URL.
    """
    quality = _check_quality(quality)
    track = await _ensure(track_id, quality)
    # count a play only when playback starts (no Range or range starting at 0)
    if not range_header or range_header.startswith("bytes=0-"):
        asyncio.get_running_loop().create_task(library.bump_play_count(track.id))
    return _range_response(track, range_header)


@router.get("/{track_id}/download")
async def download_track(
    track_id: str,
    quality: str = Query("MP3_320"),
    user_id: int | None = Query(None, description="optional: log into this user's history"),
    range_header: str | None = Header(None, alias="Range"),
    db: AsyncSession = Depends(get_db),
):
    """Download the track as a file (Content-Disposition: attachment).

    Range is supported too, so Android DownloadManager can resume.
    """
    quality = _check_quality(quality)
    track = await _ensure(track_id, quality)

    if user_id is not None and await db.get(User, user_id) is not None:
        db.add(
            UserDownload(
                user_id=user_id,
                source_id=str(track_id),
                content_type="track",
                quality=quality,
                title=track.title,
                artist=track.artist,
            )
        )
        await db.commit()

    return _range_response(track, range_header, as_attachment=True)


@router.get("/{track_id}/lyrics", response_class=PlainTextResponse)
async def track_lyrics(track_id: str, quality: str = Query("MP3_320")) -> str:
    """Synced .lrc lyrics if available (only for tracks already in the library)."""
    quality = _check_quality(quality)
    track = await library.get_cached(track_id, quality)
    if track is None or not track.lrc_path:
        raise HTTPException(status_code=404, detail="Lyrics not available")
    path = Path(track.lrc_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Lyrics not available")
    return path.read_text(encoding="utf-8")
