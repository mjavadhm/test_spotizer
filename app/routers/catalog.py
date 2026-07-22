from fastapi import APIRouter, Depends, Query

from ..deps import get_platform
from ..schemas.catalog import LinkResolveRequest, LinkResolveResponse
from ..services.deezer import deezer_client

router = APIRouter(tags=["catalog"], dependencies=[Depends(get_platform)])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    type: str = Query("track"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> dict:
    return await deezer_client.search(q, type, limit, offset)


@router.get("/tracks/{track_id}")
async def get_track(track_id: str) -> dict:
    return await deezer_client.get_track_normalized(track_id)


@router.get("/albums/{album_id}")
async def get_album(album_id: str) -> dict:
    return await deezer_client.get_album(album_id)


@router.get("/playlists/{playlist_id}")
async def get_playlist(playlist_id: str) -> dict:
    return await deezer_client.get_playlist(playlist_id)


@router.get("/artists/{artist_id}")
async def get_artist(artist_id: str) -> dict:
    return await deezer_client.get_artist(artist_id)


@router.get("/artists/{artist_id}/top")
async def get_artist_top(artist_id: str, limit: int = Query(25, ge=1, le=100)) -> dict:
    return {"tracks": await deezer_client.get_artist_top_tracks(artist_id, limit)}


@router.get("/artists/{artist_id}/albums")
async def get_artist_albums(artist_id: str) -> dict:
    """Full discography (albums/singles/EPs) - used for discography download."""
    return {"albums": await deezer_client.get_artist_albums(artist_id)}


@router.get("/artists/{artist_id}/related")
async def get_artist_related(artist_id: str, limit: int = Query(25, ge=1, le=100)) -> dict:
    return {"artists": await deezer_client.get_artist_related(artist_id, limit)}


@router.post("/links/resolve", response_model=LinkResolveResponse)
async def resolve_link(body: LinkResolveRequest) -> LinkResolveResponse:
    info = await deezer_client.resolve_link(body.url)
    return LinkResolveResponse(**info)
