"""Thin async client for the Spotizer API. The bot never touches the DB or
deemix directly — everything goes through these endpoints."""

import os
from pathlib import Path

import aiohttp


class APIError(Exception):
    def __init__(self, status: int, detail: str | None = None):
        self.status = status
        self.detail = detail or ""
        super().__init__(f"API {status}: {self.detail}")


class SpotizerAPI:
    def __init__(self, base_url: str, client_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.client_key = client_key
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.client_key:
                headers["X-Client-Key"] = self.client_key
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=aiohttp.ClientTimeout(total=300)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        session = await self._get_session()
        async with session.request(method, f"{self.base_url}{path}", **kwargs) as resp:
            if resp.status >= 400:
                try:
                    detail = (await resp.json()).get("detail")
                except Exception:  # noqa: BLE001
                    detail = await resp.text()
                raise APIError(resp.status, str(detail))
            return await resp.json()

    # ---------- users ----------

    async def resolve_user(self, platform_user_id: str, display_name: str | None = None) -> dict:
        return await self._request(
            "POST",
            "/v1/users/resolve",
            json={"platform_user_id": platform_user_id, "display_name": display_name},
        )

    async def get_user_settings(self, user_id: int) -> dict:
        return await self._request("GET", f"/v1/users/{user_id}/settings")

    async def update_user_settings(self, user_id: int, **fields) -> dict:
        return await self._request("PATCH", f"/v1/users/{user_id}/settings", json=fields)

    async def create_link_code(self, user_id: int) -> dict:
        return await self._request("POST", f"/v1/users/{user_id}/link-code")

    # ---------- catalog ----------

    async def search(self, q: str, type_: str = "track", limit: int = 8, offset: int = 0) -> dict:
        return await self._request(
            "GET", "/v1/search", params={"q": q, "type": type_, "limit": limit, "offset": offset}
        )

    async def get_track(self, track_id: str) -> dict:
        return await self._request("GET", f"/v1/tracks/{track_id}")

    async def get_album(self, album_id: str) -> dict:
        return await self._request("GET", f"/v1/albums/{album_id}")

    async def get_playlist(self, playlist_id: str) -> dict:
        return await self._request("GET", f"/v1/playlists/{playlist_id}")

    async def get_artist(self, artist_id: str) -> dict:
        return await self._request("GET", f"/v1/artists/{artist_id}")

    async def get_artist_top(self, artist_id: str, limit: int = 25) -> list[dict]:
        data = await self._request("GET", f"/v1/artists/{artist_id}/top", params={"limit": limit})
        return data.get("tracks", [])

    async def get_artist_albums(self, artist_id: str) -> list[dict]:
        data = await self._request("GET", f"/v1/artists/{artist_id}/albums")
        return data.get("albums", [])

    async def get_artist_related(self, artist_id: str, limit: int = 25) -> list[dict]:
        data = await self._request("GET", f"/v1/artists/{artist_id}/related", params={"limit": limit})
        return data.get("artists", [])

    async def resolve_link(self, url: str) -> dict:
        return await self._request("POST", "/v1/links/resolve", json={"url": url})

    # ---------- history & ratings ----------

    async def get_history(self, user_id: int, page: int = 1, page_size: int = 5) -> dict:
        return await self._request(
            "GET",
            f"/v1/users/{user_id}/downloads",
            params={"page": page, "page_size": page_size},
        )

    async def rate_download(self, user_id: int, download_id: int, rating: int) -> dict:
        return await self._request(
            "POST",
            f"/v1/users/{user_id}/downloads/{download_id}/rating",
            json={"rating": rating},
        )

    # ---------- personal playlists ----------

    async def get_playlists(self, user_id: int) -> list[dict]:
        return await self._request("GET", f"/v1/users/{user_id}/playlists")

    async def create_playlist(self, user_id: int, name: str, track_id: str | None = None) -> dict:
        return await self._request(
            "POST",
            f"/v1/users/{user_id}/playlists",
            json={"name": name, "track_id": track_id},
        )

    async def delete_playlist(self, user_id: int, playlist_id: int) -> dict:
        return await self._request("DELETE", f"/v1/users/{user_id}/playlists/{playlist_id}")

    async def get_playlist_tracks(self, user_id: int, playlist_id: int) -> dict:
        return await self._request("GET", f"/v1/users/{user_id}/playlists/{playlist_id}/tracks")

    async def add_playlist_track(self, user_id: int, playlist_id: int, track_id: str) -> dict:
        return await self._request(
            "POST",
            f"/v1/users/{user_id}/playlists/{playlist_id}/tracks",
            json={"track_id": track_id},
        )

    async def remove_playlist_track(self, user_id: int, playlist_id: int, playlist_track_id: int) -> dict:
        return await self._request(
            "DELETE",
            f"/v1/users/{user_id}/playlists/{playlist_id}/tracks/{playlist_track_id}",
        )

    # ---------- subscriptions ----------

    async def get_subscriptions(self, user_id: int) -> list[dict]:
        return await self._request("GET", f"/v1/users/{user_id}/subscriptions")

    async def subscribe(self, user_id: int, artist_id: str, artist_name: str | None = None) -> dict:
        return await self._request(
            "POST",
            f"/v1/users/{user_id}/subscriptions",
            json={"artist_id": artist_id, "artist_name": artist_name},
        )

    async def unsubscribe(self, user_id: int, artist_id: str) -> dict:
        return await self._request("DELETE", f"/v1/users/{user_id}/subscriptions/{artist_id}")

    async def check_new_releases(self) -> list[dict]:
        return await self._request("GET", "/v1/subscriptions/new-releases")

    # ---------- recommendations ----------

    async def get_recommendations(self, user_id: int) -> dict:
        return await self._request("GET", f"/v1/users/{user_id}/recommendations")

    # ---------- downloads ----------

    async def create_download(
        self,
        user_id: int,
        content_type: str,
        source_id: str,
        quality: str | None = None,
    ) -> dict:
        return await self._request(
            "POST",
            "/v1/downloads",
            json={
                "user_id": user_id,
                "content_type": content_type,
                "source_id": source_id,
                "quality": quality,
            },
        )

    async def get_job(self, job_id: str) -> dict:
        return await self._request("GET", f"/v1/downloads/{job_id}")

    async def report_file(self, **fields) -> dict:
        return await self._request("POST", "/v1/files/report", json=fields)

    async def download_to(self, temp_url: str, dest_dir: Path) -> Path:
        """Fetch a job file (temp_url like /v1/files/{token}) into dest_dir.
        Returns the local path (filename taken from Content-Disposition)."""
        session = await self._get_session()
        async with session.get(f"{self.base_url}{temp_url}") as resp:
            if resp.status >= 400:
                raise APIError(resp.status, await resp.text())
            filename = None
            if resp.content_disposition:
                filename = resp.content_disposition.filename
            if not filename:
                filename = temp_url.rsplit("/", 1)[-1]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / filename
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(1 << 16):
                    fh.write(chunk)
            return dest


api = SpotizerAPI(
    base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
    client_key=os.getenv("CLIENT_KEY") or None,
)
