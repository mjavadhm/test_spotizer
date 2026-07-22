from datetime import datetime

from pydantic import BaseModel


class DownloadHistoryItem(BaseModel):
    download_id: int
    source_id: str
    content_type: str
    quality: str
    title: str | None = None
    artist: str | None = None
    # 1 = liked, -1 = disliked, None = not rated
    user_rating: int | None = None
    downloaded_at: datetime


class DownloadHistoryResponse(BaseModel):
    items: list[DownloadHistoryItem]
    page: int
    page_size: int
    total: int


class PopularTrackItem(BaseModel):
    source_id: str
    title: str | None = None
    artist: str | None = None
    download_count: int
