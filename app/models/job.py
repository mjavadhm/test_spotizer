from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # e.g. "j_8f3a..."
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(16))  # track / album / playlist
    source_id: Mapped[str] = mapped_column(String(64))
    quality: Mapped[str] = mapped_column(String(16))
    as_zip: Mapped[bool] = mapped_column(Boolean, default=False)

    # queued / processing / ready / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # webhook support is deferred; field exists so the schema is future-proof
    callback_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[list["JobFile"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )


class JobFile(Base):
    """A temp file produced by a job. Deleted from disk after expires_at."""

    __tablename__ = "job_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("download_jobs.job_id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # audio / zip / lrc / m3u
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_path: Mapped[str] = mapped_column(String(1024))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    job: Mapped[DownloadJob] = relationship(back_populates="files")
