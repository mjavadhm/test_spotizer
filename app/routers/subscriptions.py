"""Artist subscriptions (the old bot's Follow / new-release notifications)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_platform
from ..models import ArtistSubscription, User, UserIdentity
from ..schemas.social import (
    IdentityItem,
    NewReleaseNotification,
    SubscribeRequest,
    SubscriptionResponse,
)
from ..services.deezer import deezer_client

router = APIRouter(tags=["subscriptions"], dependencies=[Depends(get_platform)])


def _release_date(album: dict) -> str:
    d = album.get("release_date") or ""
    return d if len(d) >= 10 else ""


def _latest_album(albums: list[dict]) -> dict | None:
    dated = [a for a in albums if _release_date(a)]
    if not dated:
        return albums[0] if albums else None
    return max(dated, key=_release_date)


@router.get(
    "/users/{user_id}/subscriptions", response_model=list[SubscriptionResponse]
)
async def list_subscriptions(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ArtistSubscription)
        .where(ArtistSubscription.user_id == user_id)
        .order_by(ArtistSubscription.subscription_id)
    )
    return [
        SubscriptionResponse(
            artist_id=s.artist_id,
            artist_name=s.artist_name,
            last_release_id=s.last_release_id,
            last_release_date=s.last_release_date,
            created_at=s.created_at,
        )
        for s in result.scalars().all()
    ]


@router.post("/users/{user_id}/subscriptions")
async def subscribe(
    user_id: int, body: SubscribeRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    artist_id = str(body.artist_id)

    existing = (
        await db.execute(
            select(ArtistSubscription).where(
                ArtistSubscription.user_id == user_id,
                ArtistSubscription.artist_id == artist_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "already_subscribed": True, "artist_name": existing.artist_name}

    # Snapshot the artist's latest release so we only notify about future ones
    artist_name = body.artist_name
    last_release_id = None
    last_release_date = None
    try:
        if not artist_name:
            info = await deezer_client.get_artist(artist_id)
            artist_name = info.get("name")
        albums = await deezer_client.get_artist_albums(artist_id, max_items=200)
        latest = _latest_album(albums)
        if latest:
            last_release_id = latest.get("id")
            last_release_date = _release_date(latest) or None
    except Exception:
        pass  # snapshot is best-effort

    db.add(
        ArtistSubscription(
            user_id=user_id,
            artist_id=artist_id,
            artist_name=artist_name,
            last_release_id=last_release_id,
            last_release_date=last_release_date,
        )
    )
    await db.commit()
    return {"ok": True, "already_subscribed": False, "artist_name": artist_name}


@router.delete("/users/{user_id}/subscriptions/{artist_id}")
async def unsubscribe(
    user_id: int, artist_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    existing = (
        await db.execute(
            select(ArtistSubscription).where(
                ArtistSubscription.user_id == user_id,
                ArtistSubscription.artist_id == str(artist_id),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await db.delete(existing)
    await db.commit()
    return {"ok": True}


@router.get("/subscriptions/new-releases", response_model=list[NewReleaseNotification])
async def check_new_releases(db: AsyncSession = Depends(get_db)):
    """Scan all subscriptions for new releases (called periodically by bots).

    Updates each subscription's snapshot and returns one notification per
    (user, artist) that has a release newer than the stored snapshot.
    """
    subs = (await db.execute(select(ArtistSubscription))).scalars().all()
    by_artist: dict[str, list[ArtistSubscription]] = {}
    for s in subs:
        by_artist.setdefault(s.artist_id, []).append(s)

    notifications: list[NewReleaseNotification] = []
    for artist_id, artist_subs in by_artist.items():
        try:
            albums = await deezer_client.get_artist_albums(artist_id, max_items=200)
        except Exception:
            continue
        latest = _latest_album(albums)
        if not latest or not latest.get("id"):
            continue
        latest_date = _release_date(latest)

        for sub in artist_subs:
            is_new = (
                latest["id"] != sub.last_release_id
                and latest_date
                and (not sub.last_release_date or latest_date > sub.last_release_date)
            )
            if is_new:
                identities = (
                    (
                        await db.execute(
                            select(UserIdentity).where(
                                UserIdentity.user_id == sub.user_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                notifications.append(
                    NewReleaseNotification(
                        user_id=sub.user_id,
                        identities=[
                            IdentityItem(
                                platform=i.platform,
                                platform_user_id=i.platform_user_id,
                            )
                            for i in identities
                        ],
                        artist_id=artist_id,
                        artist_name=sub.artist_name,
                        album=latest,
                    )
                )
            sub.last_release_id = latest["id"]
            sub.last_release_date = latest_date or sub.last_release_date

    await db.commit()
    return notifications
