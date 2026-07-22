"""LLM-based recommendations from the user's rated download history.

Port of the old bot's recommendation_service.py:
- liked   = last 50 track downloads rated 1 or unrated
- disliked = last 20 track downloads rated -1
Each LLM suggestion is resolved back to a real Deezer track via search.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_platform
from ..models import UserDownload
from ..schemas.social import RecommendationsResponse, RecommendedTrack
from ..services.deezer import deezer_client
from ..services.recommender import generate_recommendations

router = APIRouter(tags=["recommendations"], dependencies=[Depends(get_platform)])


@router.get(
    "/users/{user_id}/recommendations", response_model=RecommendationsResponse
)
async def get_recommendations(
    user_id: int, db: AsyncSession = Depends(get_db)
) -> RecommendationsResponse:
    liked_rows = (
        await db.execute(
            select(UserDownload.artist, UserDownload.title)
            .where(
                UserDownload.user_id == user_id,
                UserDownload.content_type == "track",
                or_(
                    UserDownload.user_rating == 1,
                    UserDownload.user_rating.is_(None),
                ),
            )
            .order_by(desc(UserDownload.downloaded_at))
            .limit(50)
        )
    ).all()
    disliked_rows = (
        await db.execute(
            select(UserDownload.artist, UserDownload.title)
            .where(
                UserDownload.user_id == user_id,
                UserDownload.content_type == "track",
                UserDownload.user_rating == -1,
            )
            .order_by(desc(UserDownload.downloaded_at))
            .limit(20)
        )
    ).all()

    liked = [f"{r.artist} - {r.title}" for r in liked_rows if r.artist and r.title]
    disliked = [
        f"{r.artist} - {r.title}" for r in disliked_rows if r.artist and r.title
    ]

    if not liked:
        return RecommendationsResponse(has_history=False, tracks=[], not_found=[])

    recs = await generate_recommendations(liked, disliked)

    tracks: list[RecommendedTrack] = []
    not_found: list[str] = []
    for rec in recs:
        label = f"{rec.get('artist', '')} - {rec.get('title', '')}".strip(" -")
        query = f"{rec.get('artist', '')} {rec.get('title', '')}".strip()
        results: list[dict] = []
        try:
            data = await deezer_client.search(query, "track", limit=1)
            results = data.get("results", [])
        except Exception:
            results = []
        if results and results[0].get("id"):
            r = results[0]
            tracks.append(
                RecommendedTrack(id=r["id"], title=r.get("title"), artist=r.get("artist"))
            )
        else:
            not_found.append(label)

    return RecommendationsResponse(has_history=True, tracks=tracks, not_found=not_found)
