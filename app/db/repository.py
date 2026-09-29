"""CRUD helpers -- the only module allowed to touch a SQLAlchemy Session
directly. Everything else in the app goes through here."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DeadLetterJob, VideoJob
from app.schemas import JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(db: Session, *, query: str, is_seed: bool = False, idempotency_key: str | None = None) -> VideoJob:
    job = VideoJob(
        query=query, is_seed=is_seed, status=JobStatus.PENDING.value, stage="Started",
        idempotency_key=idempotency_key,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_by_idempotency_key(db: Session, key: str) -> VideoJob | None:
    stmt = select(VideoJob).where(VideoJob.idempotency_key == key)
    return db.scalars(stmt).first()


def update_job(db: Session, job: VideoJob, **fields: Any) -> VideoJob:
    for key, value in fields.items():
        setattr(job, key, value)
    job.updated_at = utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def record_stage(db: Session, job: VideoJob, *, status: JobStatus | None = None, stage: str, progress: int | None = None) -> VideoJob:
    fields: dict[str, Any] = {"stage": stage}
    if progress is not None:
        fields["progress"] = progress
    if status is not None:
        fields["status"] = status.value
        if status == JobStatus.PROCESSING and job.started_at is None:
            fields["started_at"] = utcnow()
        if status.is_terminal:
            fields["finished_at"] = utcnow()
            fields.setdefault("progress", 100)
    return update_job(db, job, **fields)


def get_job(db: Session, job_id: str) -> VideoJob | None:
    return db.get(VideoJob, job_id)


def list_jobs(db: Session, *, limit: int = 100, offset: int = 0) -> list[VideoJob]:
    stmt = select(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_topic_master(db: Session, topic_id: str) -> VideoJob | None:
    """The cached, dual-provider-generated, quality-gated row for a topic --
    created once, on the *first* real request for that topic (never at
    startup, see app/pipeline.py). A later request that matches the same
    topic reuses this row instead of regenerating (determinism)."""
    stmt = (
        select(VideoJob)
        .where(
            VideoJob.is_seed.is_(True),
            VideoJob.topic_id == topic_id,
            VideoJob.status == JobStatus.SUCCESS.value,
        )
        .order_by(VideoJob.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def get_pending_topic_master(db: Session, topic_id: str) -> VideoJob | None:
    """A master row for this topic that's already queued/generating -- lets a
    second concurrent request for a brand-new topic attach to the same
    in-flight generation instead of starting a duplicate one."""
    stmt = (
        select(VideoJob)
        .where(
            VideoJob.is_seed.is_(True),
            VideoJob.topic_id == topic_id,
            VideoJob.status.in_([JobStatus.QUEUED.value, JobStatus.PROCESSING.value]),
        )
        .order_by(VideoJob.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


# Backward-compatible aliases (the pre-existing "seed" vocabulary, from when
# these 3 rows were generated at startup instead of on first request).
get_successful_seed = get_topic_master


def list_seed_status(db: Session) -> dict[str, VideoJob]:
    stmt = select(VideoJob).where(VideoJob.is_seed.is_(True)).order_by(VideoJob.created_at.desc())
    result: dict[str, VideoJob] = {}
    for job in db.scalars(stmt):
        if job.topic_id and job.topic_id not in result:
            result[job.topic_id] = job
    return result


def create_dead_letter(db: Session, *, job_id: str, topic_id: str | None, attempts: int, last_exception: str) -> DeadLetterJob:
    row = DeadLetterJob(job_id=job_id, topic_id=topic_id, attempts=attempts, last_exception=last_exception)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_dead_letters(db: Session, *, limit: int = 100) -> list[DeadLetterJob]:
    stmt = select(DeadLetterJob).order_by(DeadLetterJob.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))
