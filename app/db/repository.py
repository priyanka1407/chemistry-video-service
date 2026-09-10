"""CRUD helpers -- the only module allowed to touch a SQLAlchemy Session
directly. Everything else in the app goes through here."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import VideoJob
from app.schemas import JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(db: Session, *, query: str, is_seed: bool = False) -> VideoJob:
    job = VideoJob(query=query, is_seed=is_seed, status=JobStatus.PENDING.value, stage="Started")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_job(db: Session, job: VideoJob, **fields: Any) -> VideoJob:
    for key, value in fields.items():
        setattr(job, key, value)
    job.updated_at = utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def record_stage(db: Session, job: VideoJob, *, status: JobStatus | None = None, stage: str) -> VideoJob:
    fields: dict[str, Any] = {"stage": stage}
    if status is not None:
        fields["status"] = status.value
        if status == JobStatus.PROCESSING and job.started_at is None:
            fields["started_at"] = utcnow()
        if status.is_terminal:
            fields["finished_at"] = utcnow()
    return update_job(db, job, **fields)


def get_job(db: Session, job_id: str) -> VideoJob | None:
    return db.get(VideoJob, job_id)


def list_jobs(db: Session, *, limit: int = 100, offset: int = 0) -> list[VideoJob]:
    stmt = select(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_successful_seed(db: Session, topic_id: str) -> VideoJob | None:
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


def list_seed_status(db: Session) -> dict[str, VideoJob]:
    stmt = select(VideoJob).where(VideoJob.is_seed.is_(True)).order_by(VideoJob.created_at.desc())
    result: dict[str, VideoJob] = {}
    for job in db.scalars(stmt):
        if job.topic_id and job.topic_id not in result:
            result[job.topic_id] = job
    return result
