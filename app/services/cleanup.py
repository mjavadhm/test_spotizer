"""Periodic cleanup of expired temp files."""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import async_session_maker
from ..models import JobFile

logger = logging.getLogger("spotizer.cleanup")

CLEANUP_INTERVAL_SECONDS = 60


async def _cleanup_once() -> None:
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        result = await session.execute(select(JobFile).where(JobFile.expires_at < now))
        expired = result.scalars().all()
        job_dirs: set[Path] = set()
        for jf in expired:
            path = Path(jf.temp_path)
            job_dirs.add(path.parent)
            path.unlink(missing_ok=True)
            await session.delete(jf)
        await session.commit()

    # remove now-empty job dirs
    for d in job_dirs:
        try:
            if d.exists() and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


async def cleanup_loop() -> None:
    settings = get_settings()
    Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
    while True:
        try:
            await _cleanup_once()
        except Exception:  # noqa: BLE001
            logger.exception("cleanup iteration failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
