"""Deezer public API client + link resolver.

All Deezer metadata logic lives here and ONLY here — clients never talk to Deezer.
"""

import re

import aiohttp
from fastapi import HTTPException

API_BASE = "https://api.deezer.com"

_DEEZER_URL_RE = re.compile(
    r"deezer\.com/(?:[a-z]{2}/)?(track|album|playlist|artist)/(\d+)", re.IGNORECASE
)
_DEEZER_SHORT_RE = re.compile(r"(?:link\.deezer\.com|deezer\.page\.link)/\S+", re.IGNORECASE)
_SPOTIFY_RE = re.compile(r"open\.spotify\.com/(track|album|playlist|artist)/(\w+)", re.IGNORECASE)


# ---------- normalizers (clean, stable shapes for app clients) ----------

def _sid(value) -> str | None:
    return str(value) if value is not None else None


def map_track(t: dict, fallback_album: dict | None = None) -> dict:
    """Deezer track -> flat object with everything a client needs to render it."""
    artist = t.get("artist") or {}
    album = t.get("album") or fallback_album or {}
    return {
        "id": _sid(t.get("id")),
        "title": t.get("title"),
        "artist": artist.get("name"),
        "artist_id": _sid(artist.get("id")),
        "album": album.get("title"),
        "album_id": _sid(album.get("id")),
        "cover_small": album.get("cover_small"),    # 56x56
        "cover_medium": album.get("cover_medium"),  # 250x250
        "cover_big": album.get("cover_big"),        # 500x500
        "cover_xl": album.get("cover_xl"),          # 1000x1000
        "duration": t.get("duration"),              # seconds
        "explicit": bool(t.get("explicit_lyrics")),
        "preview_url": t.get("preview"),            # 30s mp3 preview from Deezer
        "link": t.get("link"),
    }


def map_track_detail(t: dict) -> dict:
    base = map_track(t)
    base.update(
        {
            "release_date": t.get("release_date"),
            "track_position": t.get("track_position"),
            "disk_number": t.get("disk_number"),
            "bpm": t.get("bpm"),
            "isrc": t.get("isrc"),
        }
    )
    return base


def map_album(a: dict) -> dict:
    artist = a.get("artist") or {}
    return {
        "id": _sid(a.get("id")),
        "title": a.get("title"),
        "artist": artist.get("name"),
        "artist_id": _sid(artist.get("id")),
        "cover_small": a.get("cover_small"),
        "cover_medium": a.get("cover_medium"),
        "cover_big": a.get("cover_big"),
        "cover_xl": a.get("cover_xl"),
        "nb_tracks": a.get("nb_tracks"),
        "release_date": a.get("release_date"),
        "record_type": a.get("record_type"),  # album / single / ep
        "explicit": bool(a.get("explicit_lyrics")),
        "link": a.get("link"),
    }


def map_artist(a: dict) -> dict:
    return {
        "id": _sid(a.get("id")),
        "name": a.get("name"),
        "picture_small": a.get("picture_small"),
        "picture_medium": a.get("picture_medium"),
        "picture_big": a.get("picture_big"),
        "picture_xl": a.get("picture_xl"),
        "nb_albums": a.get("nb_album"),
        "nb_fans": a.get("nb_fan"),
        "link": a.get("link"),
    }


def map_playlist(p: dict) -> dict:
    creator = p.get("user") or p.get("creator") or {}
    return {
        "id": _sid(p.get("id")),
        "title": p.get("title"),
        "creator": creator.get("name"),
        "picture_small": p.get("picture_small"),
        "picture_medium": p.get("picture_medium"),
        "picture_big": p.get("picture_big"),
        "picture_xl": p.get("picture_xl"),
        "nb_tracks": p.get("nb_tracks"),
        "description": p.get("description"),
        "link": p.get("link"),
    }


SEARCH_MAPPERS = {
    "track": map_track,
    "album": map_album,
    "artist": map_artist,
    "playlist": map_playlist,
}


class DeezerClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        session = await self._get_session()
        async with session.get(f"{API_BASE}{path}", params=params or {}) as resp:
            data = await resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise HTTPException(status_code=404, detail=str(data["error"].get("message", "Deezer error")))
        return data

    # ---------- search & metadata ----------

    async def search(self, q: str, type_: str = "track", limit: int = 10, offset: int = 0) -> dict:
        if type_ not in ("track", "album", "playlist", "artist"):
            raise HTTPException(status_code=400, detail="type must be track/album/playlist/artist")
        data = await self._get(f"/search/{type_}", {"q": q, "limit": limit, "index": offset})
        mapper = SEARCH_MAPPERS[type_]
        return {
            "type": type_,
            "results": [mapper(item) for item in data.get("data", [])],
            "total": data.get("total", 0),
            "limit": limit,
            "offset": offset,
        }

    async def get_track(self, track_id: str) -> dict:
        """Raw Deezer track (used internally, e.g. library metadata)."""
        return await self._get(f"/track/{track_id}")

    async def get_track_normalized(self, track_id: str) -> dict:
        return map_track_detail(await self.get_track(track_id))

    async def get_album(self, album_id: str) -> dict:
        """Normalized album detail incl. track list (each track render-ready)."""
        raw = await self._get(f"/album/{album_id}")
        album = map_album(raw)
        album["tracks"] = [
            map_track(t, fallback_album=raw)
            for t in (raw.get("tracks") or {}).get("data", [])
        ]
        return album

    async def get_playlist(self, playlist_id: str) -> dict:
        """Normalized playlist detail incl. track list."""
        raw = await self._get(f"/playlist/{playlist_id}")
        playlist = map_playlist(raw)
        playlist["tracks"] = [
            map_track(t) for t in (raw.get("tracks") or {}).get("data", [])
        ]
        return playlist

    async def get_artist(self, artist_id: str) -> dict:
        """Normalized artist detail incl. top tracks + albums + related artists."""
        raw = await self._get(f"/artist/{artist_id}")
        top = await self._get(f"/artist/{artist_id}/top", {"limit": 10})
        albums = await self._get(f"/artist/{artist_id}/albums", {"limit": 25})
        related = await self._get(f"/artist/{artist_id}/related", {"limit": 10})
        artist = map_artist(raw)
        artist["top_tracks"] = [map_track(t) for t in top.get("data", [])]
        artist["albums"] = [map_album(a) for a in albums.get("data", [])]
        artist["related_artists"] = [map_artist(a) for a in related.get("data", [])]
        return artist

    async def get_artist_top_tracks(self, artist_id: str, limit: int = 25) -> list[dict]:
        """Artist's top tracks (normalized)."""
        data = await self._get(f"/artist/{artist_id}/top", {"limit": limit})
        return [map_track(t) for t in data.get("data", [])]

    async def get_artist_albums(self, artist_id: str, max_items: int = 400) -> list[dict]:
        """Artist's full discography (albums/singles/EPs), paginated internally."""
        albums: list[dict] = []
        index = 0
        while True:
            data = await self._get(
                f"/artist/{artist_id}/albums", {"limit": 100, "index": index}
            )
            chunk = data.get("data", [])
            albums.extend(map_album(a) for a in chunk)
            if not chunk or not data.get("next") or len(albums) >= max_items:
                break
            index += len(chunk)
        return albums

    async def get_artist_related(self, artist_id: str, limit: int = 25) -> list[dict]:
        """Artists related to this artist (normalized)."""
        data = await self._get(f"/artist/{artist_id}/related", {"limit": limit})
        return [map_artist(a) for a in data.get("data", [])]

    # ---------- link resolving ----------

    async def resolve_link(self, url: str) -> dict:
        """URL -> {content_type, source_id, title, artist, cover, track_count}."""
        if _SPOTIFY_RE.search(url):
            # TODO: Spotify -> Deezer conversion (later phase)
            raise HTTPException(status_code=400, detail="Spotify links are not supported yet")

        # Short/share links: follow the redirect to get the canonical URL
        if _DEEZER_SHORT_RE.search(url):
            session = await self._get_session()
            async with session.get(url, allow_redirects=True) as resp:
                url = str(resp.url)

        match = _DEEZER_URL_RE.search(url)
        if not match:
            raise HTTPException(status_code=400, detail="Unsupported or invalid link")

        content_type, source_id = match.group(1).lower(), match.group(2)
        info = await self._get(f"/{content_type}/{source_id}")

        title = info.get("title") or info.get("name")
        artist = None
        if isinstance(info.get("artist"), dict):
            artist = info["artist"].get("name")
        elif isinstance(info.get("creator"), dict):
            artist = info["creator"].get("name")

        cover = (
            info.get("cover_big")
            or info.get("picture_big")
            or (info.get("album") or {}).get("cover_big")
        )

        track_count = info.get("nb_tracks")

        return {
            "content_type": content_type,
            "source_id": source_id,
            "title": title,
            "artist": artist,
            "cover": cover,
            "track_count": track_count,
        }


# module-level singleton, initialised in main.py lifespan
deezer_client = DeezerClient()


def deezer_url_for(content_type: str, source_id: str) -> str:
    return "https://www.deezer.com/" + content_type + "/" + source_id
