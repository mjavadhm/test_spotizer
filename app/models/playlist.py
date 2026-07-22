from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class UserPlaylist(Base):
    """Custom user playlists (the /playlists feature)."""

    __tablename__ = "user_playlists"

    playlist_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tracks: Mapped[list["UserPlaylistTrack"]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan", lazy="selectin"
    )


class UserPlaylistTrack(Base):
    """A Deezer track saved inside a user playlist."""

    __tablename__ = "user_playlist_tracks"

    playlist_track_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("user_playlists.playlist_id", ondelete="CASCADE"), index=True
    )
    track_source_id: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    playlist: Mapped[UserPlaylist] = relationship(back_populates="tracks")
