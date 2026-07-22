from pydantic import BaseModel


class TrackStatusResponse(BaseModel):
    track_id: str
    quality: str
    cached: bool  # true -> stream/download will respond instantly
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    cover: str | None = None
    duration: int | None = None
    size: int | None = None
    format: str | None = None
    has_lyrics: bool = False
