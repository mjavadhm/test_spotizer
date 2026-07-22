from pydantic import BaseModel


class LinkResolveRequest(BaseModel):
    url: str


class LinkResolveResponse(BaseModel):
    content_type: str  # track / album / playlist / artist
    source_id: str
    title: str | None = None
    artist: str | None = None
    cover: str | None = None
    track_count: int | None = None
