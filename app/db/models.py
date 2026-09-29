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
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, unique=True)

    # --- lifecycle --------------------------------------------------------
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="Started")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

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

    # --- output (the "delivered" variant -- see default_video_delivery_provider) ---
    video_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- dual-provider generation: both are rendered, stored, and gated so a
    # repeat request for the same topic is retrieved from here rather than
    # regenerated (determinism) -------------------------------------------
    local_video_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    local_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    local_validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    local_validation_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    veo_video_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    veo_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    veo_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    veo_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    veo_validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    veo_validation_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    veo_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- QC (mechanical, on the delivered variant) --------------------------
    validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    validation_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- retrieve-then-generate / verification layer ------------------------
    source_chunk_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    narration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    faithfulness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    teaching_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    gate_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    regeneration_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- accounting / diagnostics -------------------------------------------
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- timestamps -------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeadLetterJob(Base):
    """Jobs that exhausted every retry -- BUILD_SPEC Part 1's dead-letter
    table. A job landing here is never silently dropped: it still has a
    `video_jobs` row (status=failed_permanent) plus this row recording
    exactly which exception ended it and how many attempts were made."""

    __tablename__ = "dead_letter_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_exception: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
