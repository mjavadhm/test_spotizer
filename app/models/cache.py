from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class CachedFile(Base):
    """The heart of the cache: platform_file_id per (content, quality, platform).

    When a client reports a file_id after sending a file, future requests for the
    same content on the same platform are answered instantly without downloading.
    """

    __tablename__ = "cached_files"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "content_type", "quality", "platform", "kind", name="uq_cached_file"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(16))  # track / album / playlist
    quality: Mapped[str] = mapped_column(String(16))
    platform: Mapped[str] = mapped_column(String(32))
    platform_file_id: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(8), default="audio")  # audio / zip
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
