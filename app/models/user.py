from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class User(Base):
    """Unified user identity — no platform-specific fields here."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserIdentity(Base):
    """Maps (platform, platform_user_id) -> user_id. One user can have many identities."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_platform_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    platform_user_id: Mapped[str] = mapped_column(String(64))


class LinkCode(Base):
    """Short-lived code for linking a new client (e.g. the Android app) to an
    existing user. The bot requests a code, the user types it into the app,
    and the app's device identity gets attached to the same user_id."""

    __tablename__ = "link_codes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    quality: Mapped[str] = mapped_column(String(16), default="MP3_320")
    make_zip: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(8), default="fa")
