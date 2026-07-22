import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .config import get_settings
from .db import close_db, init_db
from .routers import (
    catalog,
    downloads,
    history,
    playlists,
    recommendations,
    subscriptions,
    tracks,
    users,
)
from .services.cleanup import cleanup_loop
from .services.deezer import deezer_client
from .services.queue import download_queue, requeue_stale_jobs

logging.basicConfig(level=logging.INFO)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    await init_db()
    await download_queue.start()
    await requeue_stale_jobs(download_queue)
    cleanup_task = asyncio.create_task(cleanup_loop())
    yield
    # shutdown
    cleanup_task.cancel()
    await download_queue.stop()
    await deezer_client.close()
    await close_db()


app = FastAPI(
    title="Spotizer API",
    description="Multi-client backend for Spotizer bots (Telegram, Bale, ...)",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(users.router, prefix=settings.API_V1_PREFIX)
app.include_router(catalog.router, prefix=settings.API_V1_PREFIX)
app.include_router(downloads.router, prefix=settings.API_V1_PREFIX)
app.include_router(tracks.router, prefix=settings.API_V1_PREFIX)
app.include_router(history.router, prefix=settings.API_V1_PREFIX)
app.include_router(playlists.router, prefix=settings.API_V1_PREFIX)
app.include_router(subscriptions.router, prefix=settings.API_V1_PREFIX)
app.include_router(recommendations.router, prefix=settings.API_V1_PREFIX)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
