from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ArtistSubscription(Base):
    """Artists a user follows (new-release notifications)."""

    __tablename__ = "artist_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "artist_id", name="uq_sub_user_artist"),)

    subscription_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    artist_id: Mapped[str] = mapped_column(String(64), index=True)
    artist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_release_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_release_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
