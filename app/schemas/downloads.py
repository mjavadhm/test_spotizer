from pydantic import BaseModel


class DownloadRequest(BaseModel):
    user_id: int
    content_type: str  # track / album / playlist
    source_id: str
    quality: str | None = None  # falls back to user settings
    as_zip: bool | None = None  # falls back to user settings
    callback_url: str | None = None  # webhook — deferred, polling only for now


class FileInfo(BaseModel):
    kind: str  # audio / zip / lrc / m3u
    title: str | None = None
    artist: str | None = None
    duration: int | None = None
    size: int | None = None
    platform_file_id: str | None = None  # if set: client sends this directly
    temp_url: str | None = None  # else: client fetches bytes from here


class DownloadCreatedResponse(BaseModel):
    cached: bool
    job_id: str | None = None
    status: str | None = None  # queued when a new job was created
    files: list[FileInfo] | None = None  # filled when cached=true
    # history row id (filled when cached=true) - use it for the rating feature
    download_id: int | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # queued / processing / ready / failed / cancelled
    progress: int
    current_step: str | None = None
    error: str | None = None
    files: list[FileInfo] | None = None  # filled when status=ready


class FileReportRequest(BaseModel):
    """Client reports the platform file_id after sending the file, to close the cache loop."""

    user_id: int
    job_id: str | None = None
    source_id: str
    content_type: str
    quality: str
    platform_file_id: str
    kind: str = "audio"
    title: str | None = None
    artist: str | None = None
    duration: int | None = None
