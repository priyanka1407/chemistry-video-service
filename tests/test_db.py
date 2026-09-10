"""Table creation is idempotent, and basic CRUD round-trips correctly."""
from __future__ import annotations

from app.db import repository
from app.db.models import Base
from app.schemas import JobStatus


def test_create_all_is_idempotent(db):
    # The autouse _isolated_db fixture already called create_all once;
    # calling it again against the same engine must be a safe no-op.
    Base.metadata.create_all(db.get_bind())
    Base.metadata.create_all(db.get_bind())


def test_create_and_fetch_job(db):
    job = repository.create_job(db, query="How does the pH scale work?", is_seed=True)
    assert job.status == JobStatus.PENDING.value
    assert job.query == "How does the pH scale work?"

    fetched = repository.get_job(db, job.id)
    assert fetched is not None
    assert fetched.id == job.id


def test_record_stage_sets_timestamps(db):
    job = repository.create_job(db, query="test")
    job = repository.record_stage(db, job, status=JobStatus.PROCESSING, stage="Started")
    assert job.started_at is not None
    assert job.finished_at is None

    job = repository.record_stage(db, job, status=JobStatus.SUCCESS, stage="Completed")
    assert job.finished_at is not None


def test_get_successful_seed_only_returns_success(db):
    job = repository.create_job(db, query="q", is_seed=True)
    job = repository.update_job(db, job, topic_id="ph_scale", status=JobStatus.FAILED.value)
    assert repository.get_successful_seed(db, "ph_scale") is None

    job2 = repository.create_job(db, query="q2", is_seed=True)
    repository.update_job(db, job2, topic_id="ph_scale", status=JobStatus.SUCCESS.value)
    found = repository.get_successful_seed(db, "ph_scale")
    assert found is not None
    assert found.id == job2.id
