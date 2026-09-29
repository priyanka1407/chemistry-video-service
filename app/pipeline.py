"""Orchestration layer for the request-serving path. Route handlers in
app/main.py call into this, never into llm/video/db/celery directly.

No video is ever generated here, and none is ever generated at startup --
`handle_query` either serves an already-generated topic master from the DB
(deterministic cache hit), rejects a query that doesn't match one of the 3
supported concepts, or hands off to Celery (app/tasks.py) and returns
immediately. All actual generation work -- retrieval, scripting, grounding,
rendering, QC -- happens in the Celery task, on a worker, never inline in a
request.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import costs
from app.db import repository
from app.db.models import VideoJob
from app.llm import guardrails, semantic_gate
from app.schemas import JobStatus
from app.tasks import generate_topic_video_task

log = logging.getLogger(__name__)


def copy_master_fields(db: Session, job: VideoJob, master: VideoJob) -> VideoJob:
    return repository.update_job(
        db, job,
        topic_id=master.topic_id,
        matched_question=master.matched_question or master.query,
        cache_hit=True,
        provider=master.provider,
        video_location=master.video_location,
        duration_seconds=master.duration_seconds,
        size_bytes=master.size_bytes,
        local_video_location=master.local_video_location,
        local_duration_seconds=master.local_duration_seconds,
        local_size_bytes=master.local_size_bytes,
        veo_video_location=master.veo_video_location,
        veo_duration_seconds=master.veo_duration_seconds,
        veo_size_bytes=master.veo_size_bytes,
        validation_passed=master.validation_passed,
        validation_details=master.validation_details,
        source_chunk_ids=master.source_chunk_ids,
        narration_text=master.narration_text,
        faithfulness_score=master.faithfulness_score,
        teaching_quality_score=master.teaching_quality_score,
        gate_decision=master.gate_decision,
        quality_report=master.quality_report,
    )


def handle_query(db: Session, query: str, client_key: str, *, audience_age: int = 15, idempotency_key: str | None = None) -> VideoJob:
    if idempotency_key:
        existing = repository.get_by_idempotency_key(db, idempotency_key)
        if existing is not None:
            log.info("Idempotency-Key %s matched existing job %s -- returning it unchanged.", idempotency_key, existing.id)
            return existing

    job = repository.create_job(db, query=query, is_seed=False, idempotency_key=idempotency_key)
    job = repository.record_stage(db, job, status=JobStatus.PROCESSING, stage="Started", progress=1)

    if not guardrails.rate_limiter.allow(client_key):
        job = repository.update_job(db, job, error_code="RATE_LIMITED", error_message="Too many requests -- slow down.")
        job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Rejected - rate limited")
        return job

    guard = guardrails.check_input(query)
    if not guard.allowed:
        job = repository.update_job(db, job, error_code="GUARDRAIL_REJECTED", error_message=guard.reason)
        job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Rejected - guardrail")
        return job

    job = repository.record_stage(db, job, stage="Matching", progress=5)
    match_result = semantic_gate.match(query)
    cost = costs.embedding_cost(query) if match_result.method == "embedding" else 0.0

    job = repository.update_job(
        db, job,
        similarity_score=round(match_result.score, 6),
        match_method=match_result.method,
        embedding=match_result.query_embedding,
        embedding_model=match_result.embedding_model,
        cost_usd=round(cost, 6),
    )

    if match_result.topic_id is None:
        job = repository.update_job(
            db, job,
            error_code="OUT_OF_SCOPE",
            error_message=(
                "This service only supports 3 chemistry concepts: how the pH scale works, why "
                "atoms form covalent bonds, and the difference between ionic and covalent "
                "bonding. See GET /topics."
            ),
        )
        job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Rejected - out of scope")
        return job

    job = repository.update_job(db, job, topic_id=match_result.topic_id, matched_question=match_result.matched_question)

    # --- deterministic cache: an already-generated, gated master for this topic ---
    master = repository.get_topic_master(db, match_result.topic_id)
    if master is not None:
        job = copy_master_fields(db, job, master)
        job = repository.record_stage(db, job, status=JobStatus.SUCCESS, stage="Completed (cache hit)", progress=100)
        return job

    # --- another request for this same brand-new topic is already generating ---
    pending_master = repository.get_pending_topic_master(db, match_result.topic_id)
    if pending_master is not None:
        job = repository.update_job(db, job, celery_task_id=pending_master.celery_task_id)
        job = repository.record_stage(db, job, status=JobStatus.PROCESSING, stage="Waiting on in-flight generation for this topic", progress=10)
        return job

    # --- first-ever request for this topic: this job becomes its cached master ---
    job = repository.update_job(db, job, is_seed=True)
    job = repository.record_stage(db, job, status=JobStatus.QUEUED, stage="Queued", progress=2)
    async_result = generate_topic_video_task.delay(job.id, audience_age)
    job = repository.update_job(db, job, celery_task_id=async_result.id)
    return job
