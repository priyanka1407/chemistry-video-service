"""Orchestration layer: the only module that calls both the LLM boundary and
the video-generation boundary and writes the result to the DB. Route
handlers in app/main.py call into this, never into llm/video/db directly.

Two entry points:
  * `run_warmup` -- startup: generate any missing seed videos.
  * `handle_query` -- POST /generate: guardrail -> match -> cache-hit/reject.
    Never renders. Every request, accepted or not, gets a persisted row.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import costs
from app.db import repository
from app.db.models import VideoJob
from app.llm import guardrails, semantic_gate
from app.llm.script_writer import generate_script
from app.schemas import JobStatus
from app.topics import SEED_TOPIC_IDS, Topic, get_topic
from app.video import factory as video_factory
from app.video.base import RenderResult
from app.qc.validator import validate as qc_validate

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup warmup
# ---------------------------------------------------------------------------
def run_warmup(db: Session) -> dict[str, str]:
    """Ensure every seed topic has a SUCCESS row. Returns {topic_id: status}
    for the startup log / /health endpoint."""
    semantic_gate.warm_embeddings()

    existing = repository.list_seed_status(db)
    results: dict[str, str] = {}

    for topic_id in SEED_TOPIC_IDS:
        topic = get_topic(topic_id)
        assert topic is not None

        current = existing.get(topic_id)
        if current is not None and current.status == JobStatus.SUCCESS.value:
            log.info("Seed topic %s already has a successful video (%s) -- skipping.", topic_id, current.id)
            results[topic_id] = "already_present"
            continue

        job = _generate_seed(db, topic)
        results[topic_id] = job.status

    return results


def _generate_seed(db: Session, topic: Topic) -> VideoJob:
    job = repository.create_job(db, query=topic.question, is_seed=True)
    job = repository.update_job(db, job, topic_id=topic.id, matched_question=topic.question, match_method="seed")
    job = repository.record_stage(db, job, status=JobStatus.PROCESSING, stage="Scripting")

    script = generate_script(topic)
    job = repository.update_job(
        db, job,
        llm_model=script.model,
        script_source=script.source,
        embedding_model=None,
    )

    render_result: RenderResult | None = None
    qc_details: dict | None = None
    qc_passed = False
    last_error = ""

    from app.config import settings

    for attempt in range(1, settings.max_render_attempts + 1):
        job = repository.record_stage(db, job, stage=f"Rendering (attempt {attempt})")
        job = repository.update_job(db, job, render_attempts=attempt)
        try:
            render_result = video_factory.render_with_fallback(topic, script, job.id)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            log.exception("Render attempt %d for seed topic %s failed.", attempt, topic.id)
            continue

        job = repository.record_stage(db, job, stage="Validating")
        qc = qc_validate(path=render_result.path, narration_text=render_result.narration_text, topic=topic)
        qc_details = qc.details
        qc_passed = qc.passed
        if qc_passed:
            break
        last_error = f"QC validation failed: {qc.details.get('checks')}"
        log.warning("Seed topic %s render attempt %d failed QC: %s", topic.id, attempt, last_error)

    if render_result is None or not qc_passed:
        job = repository.update_job(
            db, job,
            status=JobStatus.FAILED.value,
            stage="Failed",
            validation_passed=False,
            validation_details=qc_details,
            error_code="GENERATION_FAILED",
            error_message=last_error or "Video generation failed for an unknown reason.",
        )
        job = repository.record_stage(db, job, status=JobStatus.FAILED, stage="Failed")
        return job

    # Embedding cost only applies if warm_embeddings() actually reached the
    # embedding backend (semantic_gate falls back to lexical, no API call
    # made, when no key/backend is configured).
    cost = costs.embedding_cost(topic.question) if semantic_gate.warm_embeddings() else 0.0
    if script.source == "llm":
        cost += costs.script_cost(prompt_chars=len(topic.question) * 4, completion_chars=len(script.narration_text))
    if render_result.provider == "veo":
        cost += costs.veo_cost(render_result.duration_seconds)
    else:
        cost += costs.local_render_cost(len(render_result.narration_text))

    job = repository.update_job(
        db, job,
        provider=render_result.provider,
        video_location=str(render_result.path),
        duration_seconds=render_result.duration_seconds,
        size_bytes=render_result.size_bytes,
        validation_passed=True,
        validation_details=qc_details,
        cost_usd=round(cost, 6),
        similarity_score=1.0,
    )
    job = repository.record_stage(db, job, status=JobStatus.SUCCESS, stage="Completed")
    return job


# ---------------------------------------------------------------------------
# User-facing request flow
# ---------------------------------------------------------------------------
def handle_query(db: Session, query: str, client_key: str) -> VideoJob:
    job = repository.create_job(db, query=query, is_seed=False)
    job = repository.record_stage(db, job, status=JobStatus.PROCESSING, stage="Started")

    if not guardrails.rate_limiter.allow(client_key):
        job = repository.update_job(db, job, error_code="RATE_LIMITED", error_message="Too many requests -- slow down.")
        job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Rejected - rate limited")
        return job

    guard = guardrails.check_input(query)
    if not guard.allowed:
        job = repository.update_job(db, job, error_code="GUARDRAIL_REJECTED", error_message=guard.reason)
        job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Rejected - guardrail")
        return job

    job = repository.record_stage(db, job, stage="Matching")
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
        job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Rejected - out of scope")
        return job

    seed = repository.get_successful_seed(db, match_result.topic_id)
    if seed is None:
        job = repository.update_job(
            db, job,
            topic_id=match_result.topic_id,
            matched_question=match_result.matched_question,
            error_code="SEED_UNAVAILABLE",
            error_message=f"Topic '{match_result.topic_id}' matched but has no successful video yet.",
        )
        job = repository.record_stage(db, job, status=JobStatus.FAILED, stage="Failed - seed unavailable")
        return job

    job = repository.update_job(
        db, job,
        topic_id=seed.topic_id,
        matched_question=match_result.matched_question,
        cache_hit=True,
        provider=seed.provider,
        video_location=seed.video_location,
        duration_seconds=seed.duration_seconds,
        size_bytes=seed.size_bytes,
        validation_passed=seed.validation_passed,
    )
    job = repository.record_stage(db, job, status=JobStatus.SUCCESS, stage="Completed (cache hit)")
    return job
