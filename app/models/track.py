from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LibraryTrack(Base):
    """A track kept permanently on disk (MUSIC_DIR) for streaming / direct download.

    This is the server-side library used by the Android app:
    - stream endpoint serves these files with HTTP Range support (seek)
    - download endpoint serves them as attachments
    Unlike JobFile temp files, library tracks are NOT deleted by the TTL cleanup.
    """

    __tablename__ = "library_tracks"
    __table_args__ = (UniqueConstraint("track_id", "quality", name="uq_library_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[str] = mapped_column(String(64), index=True)  # Deezer track id
    quality: Mapped[str] = mapped_column(String(16))  # MP3_128 / MP3_320 / FLAC
    format: Mapped[str | None] = mapped_column(String(8), nullable=True)  # mp3 / flac / ...
    file_path: Mapped[str] = mapped_column(String(1024))
    lrc_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    album: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cover: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    play_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
