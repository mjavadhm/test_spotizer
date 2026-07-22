from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_platform
from ..models import UserDownload
from ..schemas.history import (
    DownloadHistoryItem,
    DownloadHistoryResponse,
    PopularTrackItem,
)
from ..schemas.social import RatingRequest

router = APIRouter(tags=["history"], dependencies=[Depends(get_platform)])


@router.get("/users/{user_id}/downloads", response_model=DownloadHistoryResponse)
async def get_history(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> DownloadHistoryResponse:
    total = (
        await db.execute(
            select(func.count()).select_from(UserDownload).where(UserDownload.user_id == user_id)
        )
    ).scalar_one()
    result = await db.execute(
        select(UserDownload)
        .where(UserDownload.user_id == user_id)
        .order_by(desc(UserDownload.downloaded_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        DownloadHistoryItem(
            download_id=d.download_id,
            source_id=d.source_id,
            content_type=d.content_type,
            quality=d.quality,
            title=d.title,
            artist=d.artist,
            user_rating=d.user_rating,
            downloaded_at=d.downloaded_at,
        )
        for d in result.scalars().all()
    ]
    return DownloadHistoryResponse(items=items, page=page, page_size=page_size, total=total)


@router.post("/users/{user_id}/downloads/{download_id}/rating")
async def rate_download(
    user_id: int,
    download_id: int,
    body: RatingRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set a like (1) / dislike (-1) rating on a history row; 0 clears it."""
    if body.rating not in (1, -1, 0):
        raise HTTPException(status_code=400, detail="rating must be 1, -1 or 0")
    d = await db.get(UserDownload, download_id)
    if d is None or d.user_id != user_id:
        raise HTTPException(status_code=404, detail="History item not found")
    d.user_rating = body.rating if body.rating != 0 else None
    await db.commit()
    return {"ok": True, "download_id": download_id, "rating": d.user_rating}


@router.delete("/users/{user_id}/downloads/{download_id}")
async def delete_history_item(
    user_id: int,
    download_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        delete(UserDownload).where(
            UserDownload.download_id == download_id, UserDownload.user_id == user_id
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="History item not found")
    return {"ok": True}


@router.get("/tracks/popular", response_model=list[PopularTrackItem])
async def popular_tracks(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[PopularTrackItem]:
    result = await db.execute(
        select(
            UserDownload.source_id,
            func.max(UserDownload.title).label("title"),
            func.max(UserDownload.artist).label("artist"),
            func.count().label("download_count"),
        )
        .where(UserDownload.content_type == "track")
        .group_by(UserDownload.source_id)
        .order_by(desc("download_count"))
        .limit(limit)
    )
    return [
        PopularTrackItem(
            source_id=row.source_id,
            title=row.title,
            artist=row.artist,
            download_count=row.download_count,
        )
        for row in result.all()
    ]
