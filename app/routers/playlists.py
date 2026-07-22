"""Custom user playlists (port of the old bot's playlist feature)."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_platform
from ..models import User, UserPlaylist, UserPlaylistTrack
from ..schemas.social import (
    PlaylistCreateRequest,
    PlaylistResponse,
    PlaylistTrackAddRequest,
    PlaylistTrackItem,
    PlaylistTracksResponse,
)
from ..services.deezer import deezer_client

router = APIRouter(tags=["playlists"], dependencies=[Depends(get_platform)])


def _to_response(p: UserPlaylist) -> PlaylistResponse:
    return PlaylistResponse(
        playlist_id=p.playlist_id,
        name=p.name,
        description=p.description,
        track_count=len(p.tracks),
        created_at=p.created_at,
    )


async def _get_owned_playlist(
    db: AsyncSession, user_id: int, playlist_id: int
) -> UserPlaylist:
    playlist = await db.get(UserPlaylist, playlist_id)
    if playlist is None or playlist.user_id != user_id:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


@router.get("/users/{user_id}/playlists", response_model=list[PlaylistResponse])
async def list_playlists(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(UserPlaylist)
        .where(UserPlaylist.user_id == user_id)
        .order_by(UserPlaylist.playlist_id)
    )
    return [_to_response(p) for p in result.scalars().all()]


@router.post("/users/{user_id}/playlists", response_model=PlaylistResponse)
async def create_playlist(
    user_id: int, body: PlaylistCreateRequest, db: AsyncSession = Depends(get_db)
):
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    playlist = UserPlaylist(
        user_id=user_id, name=body.name.strip(), description=body.description
    )
    db.add(playlist)
    await db.flush()
    if body.track_id:
        db.add(
            UserPlaylistTrack(
                playlist_id=playlist.playlist_id,
                track_source_id=str(body.track_id),
            )
        )
    await db.commit()
    await db.refresh(playlist)
    return _to_response(playlist)


@router.delete("/users/{user_id}/playlists/{playlist_id}")
async def delete_playlist(
    user_id: int, playlist_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    playlist = await _get_owned_playlist(db, user_id, playlist_id)
    await db.delete(playlist)
    await db.commit()
    return {"ok": True}


@router.get(
    "/users/{user_id}/playlists/{playlist_id}/tracks",
    response_model=PlaylistTracksResponse,
)
async def get_playlist_tracks(
    user_id: int, playlist_id: int, db: AsyncSession = Depends(get_db)
):
    """Playlist rows enriched with normalized Deezer track metadata."""
    playlist = await _get_owned_playlist(db, user_id, playlist_id)
    rows = sorted(playlist.tracks, key=lambda r: r.playlist_track_id)

    sem = asyncio.Semaphore(5)

    async def _fetch(row: UserPlaylistTrack) -> PlaylistTrackItem:
        async with sem:
            try:
                track = await deezer_client.get_track_normalized(row.track_source_id)
            except Exception:
                track = {"id": row.track_source_id, "title": None, "artist": None}
        return PlaylistTrackItem(
            playlist_track_id=row.playlist_track_id,
            added_at=row.added_at,
            track=track,
        )

    items = list(await asyncio.gather(*[_fetch(r) for r in rows]))
    return PlaylistTracksResponse(
        playlist_id=playlist.playlist_id,
        name=playlist.name,
        description=playlist.description,
        tracks=items,
    )


@router.post("/users/{user_id}/playlists/{playlist_id}/tracks")
async def add_playlist_track(
    user_id: int,
    playlist_id: int,
    body: PlaylistTrackAddRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    playlist = await _get_owned_playlist(db, user_id, playlist_id)
    track_id = str(body.track_id)
    existing = next(
        (t for t in playlist.tracks if t.track_source_id == track_id), None
    )
    if existing:
        return {
            "ok": True,
            "already_exists": True,
            "playlist_track_id": existing.playlist_track_id,
        }
    row = UserPlaylistTrack(playlist_id=playlist.playlist_id, track_source_id=track_id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"ok": True, "already_exists": False, "playlist_track_id": row.playlist_track_id}


@router.delete("/users/{user_id}/playlists/{playlist_id}/tracks/{playlist_track_id}")
async def remove_playlist_track(
    user_id: int,
    playlist_id: int,
    playlist_track_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    playlist = await _get_owned_playlist(db, user_id, playlist_id)
    row = next(
        (t for t in playlist.tracks if t.playlist_track_id == playlist_track_id), None
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Track not found in playlist")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
