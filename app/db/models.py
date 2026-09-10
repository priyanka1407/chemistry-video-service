"""The persistence-side mirror of the job lifecycle -- one row per request.

This is the entire persistence/artifact boundary: a row here never stores
video bytes, only `video_location` (a filesystem path today; swapping to a
signed URL for S3/GCS later is a one-column change, nothing else in the
pipeline touches artifact bytes directly).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # --- what was asked -------------------------------------------------
    query: Mapped[str] = mapped_column(Text, nullable=False)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- lifecycle --------------------------------------------------------
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="Started")

    # --- semantic gate ------------------------------------------------------
    topic_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --- generation ---------------------------------------------------------
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    script_source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # llm | curated
    script_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    render_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- output ----------------------------------------------------------
    video_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- QC ----------------------------------------------------------------
    validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    validation_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- accounting / diagnostics -------------------------------------------
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- timestamps -------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
