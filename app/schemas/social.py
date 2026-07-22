"""Schemas for user playlists, artist subscriptions, ratings and recommendations."""
from datetime import datetime

from pydantic import BaseModel, Field


# ---------- playlists ----------

class PlaylistCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    # Optionally add a first track right away (the bot's "new_and_add" flow)
    track_id: str | None = None


class PlaylistResponse(BaseModel):
    playlist_id: int
    name: str
    description: str | None = None
    track_count: int
    created_at: datetime


class PlaylistTrackAddRequest(BaseModel):
    track_id: str


class PlaylistTrackItem(BaseModel):
    playlist_track_id: int
    added_at: datetime
    # Normalized Deezer track object (same shape as /v1/tracks/{id})
    track: dict


class PlaylistTracksResponse(BaseModel):
    playlist_id: int
    name: str
    description: str | None = None
    tracks: list[PlaylistTrackItem]


# ---------- ratings ----------

class RatingRequest(BaseModel):
    # 1 = like, -1 = dislike, 0 = clear rating
    rating: int


# ---------- subscriptions ----------

class SubscribeRequest(BaseModel):
    artist_id: str
    artist_name: str | None = None


class SubscriptionResponse(BaseModel):
    artist_id: str
    artist_name: str | None = None
    last_release_id: str | None = None
    last_release_date: str | None = None
    created_at: datetime


class IdentityItem(BaseModel):
    platform: str
    platform_user_id: str


class NewReleaseNotification(BaseModel):
    user_id: int
    identities: list[IdentityItem]
    artist_id: str
    artist_name: str | None = None
    # Normalized Deezer album object
    album: dict


# ---------- recommendations ----------

class RecommendedTrack(BaseModel):
    id: str
    title: str | None = None
    artist: str | None = None


class RecommendationsResponse(BaseModel):
    # False when the user has no download history yet
    has_history: bool
    tracks: list[RecommendedTrack]
    not_found: list[str]
