from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class UserDownload(Base):
    """Per-user download history (the /history feature)."""

    __tablename__ = "user_downloads"

    download_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(16))
    quality: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 1 = liked, -1 = disliked, NULL = not rated (used by /recommend)
    user_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
