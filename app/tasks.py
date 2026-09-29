"""The Celery task that does all actual generation work -- retrieve, script,
verify (grounding + teaching-quality), render (both providers), check
(mechanical QC + output review), gate, persist. Everything in this file runs
on a worker process, never inline in a request handler (see app/pipeline.py,
which only ever calls `.delay()` on this task).

State machine this task drives (mirrors BUILD_SPEC Part 1):

    queued -> retrieving -> scripting -> verifying -> rendering -> checking -> complete
                                 ^______________________|
                          (regeneration loop, up to MAX_REGENERATION_ATTEMPTS)

Terminal outcomes: SUCCESS (gate=DELIVER), HOLD_FOR_REVIEW (gate=HOLD_FOR_REVIEW,
still rendered so a reviewer has something to look at), FAILED_VERIFICATION
(gates never passed after every regeneration attempt), FAILED_RENDER
(rendering itself failed after every retry), FAILED_PERMANENT (the Celery
task itself exhausted its retries -- see the dead_letter_jobs table).
"""
from __future__ import annotations

import logging

from app import costs
from app.celery_app import celery_app
from app.config import settings
from app.db import repository
from app.db.session import get_session
from app.judge.claims import extract_claims
from app.judge.grounding import GroundingReport, PreCheckResult, run_grounding_check
from app.judge.output_review import run_output_review
from app.judge.report import GateDecision, build_quality_report
from app.judge.teaching_quality import run_teaching_quality_check
from app.llm.openai_judge_client import JudgeUnavailable
from app.llm.script_writer import Script, generate_script
from app.qc.validator import validate as qc_validate
from app.rag.store import all_chunks_for_topic
from app.schemas import JobStatus
from app.topics import Topic, get_topic
from app.video import factory as video_factory

log = logging.getLogger(__name__)


def _correction_issues(grounding, teaching) -> list[str]:
    issues: list[str] = []
    for c in grounding.contradicted_claims:
        issues.append(f"Claim contradicted the source material: \"{c.claim}\" -- {c.reason}")
    if grounding.faithfulness_score < settings.faithfulness_threshold:
        unsupported = [r.claim for r in grounding.claim_results if r.verdict != "SUPPORTED"]
        for claim in unsupported[:5]:
            issues.append(f"Claim not supported by the source material, remove or fix: \"{claim}\"")
    if teaching.aggregate < settings.teaching_quality_threshold:
        for name, score in teaching.scores.items():
            if score.score <= 2:
                issues.append(f"Weak on '{name}' ({score.justification}) -- improve this dimension")
    return issues


def _run_script_and_gates(topic: Topic, audience_age: int) -> tuple[Script, object, object, int]:
    """Loop scripting + verification up to MAX_REGENERATION_ATTEMPTS+1 times.
    Returns (script, grounding_report, teaching_report, attempts_used)."""
    correction_issues: list[str] | None = None
    attempts_used = 0

    for attempt in range(settings.max_regeneration_attempts + 1):
        attempts_used = attempt
        script = generate_script(topic, audience_age=audience_age, correction_issues=correction_issues)

        try:
            claims = extract_claims(script.narration_text)
            grounding = run_grounding_check(claims=claims, topic_id=topic.id)
        except JudgeUnavailable as exc:
            log.warning("Claim extraction unavailable (%s) -- treating grounding as SKIPPED for job.", exc)
            grounding = GroundingReport(
                faithfulness_score=0.0, total_factual_claims=0, supported=0, not_found=0,
                contradicted_claims=[], claim_results=[], non_factual_claims=[],
                pre_check=PreCheckResult(passed=True), gate_passed=False, skipped=True, skip_reason=str(exc),
            )
        teaching = run_teaching_quality_check(question=topic.question, audience_age=audience_age, script_text=script.narration_text)

        prelim = build_quality_report(
            grounding=grounding, teaching=teaching, output_review=None, qc_details=None,
            threshold_faithfulness=settings.faithfulness_threshold,
            threshold_teaching=settings.teaching_quality_threshold,
            review_hold_margin=settings.review_hold_margin,
        )

        if prelim.gate_decision in (GateDecision.DELIVER.value, GateDecision.HOLD_FOR_REVIEW.value):
            return script, grounding, teaching, attempts_used
        if attempt >= settings.max_regeneration_attempts:
            return script, grounding, teaching, attempts_used

        log.warning(
            "Topic %s script attempt %d failed gates (%s) -- regenerating with correction context.",
            topic.id, attempt + 1, prelim.decision_reason,
        )
        correction_issues = _correction_issues(grounding, teaching)

    return script, grounding, teaching, attempts_used  # pragma: no cover - loop always returns above


@celery_app.task(bind=True, max_retries=settings.max_task_retries, name="app.tasks.generate_topic_video_task")
def generate_topic_video_task(self, job_id: str, audience_age: int = 15) -> str:
    db = get_session()
    try:
        job = repository.get_job(db, job_id)
        if job is None:
            log.error("generate_topic_video_task: job %s no longer exists.", job_id)
            return "missing"

        topic = get_topic(job.topic_id) if job.topic_id else None
        if topic is None:
            job = repository.update_job(db, job, error_code="UNKNOWN_TOPIC", error_message=f"No topic {job.topic_id!r}.")
            repository.record_stage(db, job, status=JobStatus.FAILED, stage="Failed - unknown topic", progress=100)
            return "failed"

        try:
            return _run_pipeline(db, job, topic, audience_age)
        except Exception as exc:  # noqa: BLE001 - transient failure -> retry with backoff, per spec
            log.exception("generate_topic_video_task failed for job %s (topic %s).", job_id, topic.id)
            try:
                raise self.retry(exc=exc, countdown=settings.task_retry_backoff_seconds * (2 ** self.request.retries))
            except self.MaxRetriesExceededError:
                repository.create_dead_letter(
                    db, job_id=job_id, topic_id=topic.id, attempts=self.request.retries + 1, last_exception=str(exc),
                )
                job = repository.get_job(db, job_id)
                repository.update_job(db, job, error_code="RETRIES_EXHAUSTED", error_message=str(exc))
                repository.record_stage(db, job, status=JobStatus.FAILED_PERMANENT, stage="Failed permanently", progress=100)
                return "failed_permanent"
    finally:
        db.close()


def _run_pipeline(db, job, topic: Topic, audience_age: int) -> str:
    repository.record_stage(db, job, status=JobStatus.PROCESSING, stage="Retrieving", progress=10)
    chunks = all_chunks_for_topic(topic.id)
    if not chunks:
        repository.update_job(db, job, error_code="NO_SOURCE_MATERIAL", error_message=f"No indexed source material for topic {topic.id!r}.")
        repository.record_stage(db, job, status=JobStatus.FAILED, stage="Failed - no source material", progress=100)
        return "failed"

    repository.record_stage(db, job, stage="Scripting", progress=25)
    script, grounding, teaching, attempts_used = _run_script_and_gates(topic, audience_age)
    repository.update_job(
        db, job,
        llm_model=script.model, script_source=script.source, script_attempts=attempts_used + 1,
        regeneration_attempts=attempts_used, source_chunk_ids=list(script.source_chunk_ids),
        narration_text=script.narration_text,
        faithfulness_score=grounding.faithfulness_score, teaching_quality_score=teaching.aggregate,
    )
    repository.record_stage(db, job, stage="Verifying", progress=40)

    prelim = build_quality_report(
        grounding=grounding, teaching=teaching, output_review=None, qc_details=None,
        threshold_faithfulness=settings.faithfulness_threshold,
        threshold_teaching=settings.teaching_quality_threshold,
        review_hold_margin=settings.review_hold_margin,
    )
    if prelim.gate_decision not in (GateDecision.DELIVER.value, GateDecision.HOLD_FOR_REVIEW.value):
        repository.update_job(db, job, quality_report=prelim.to_dict(), gate_decision=prelim.gate_decision)
        repository.record_stage(db, job, status=JobStatus.FAILED_VERIFICATION, stage="Failed verification", progress=100)
        return "failed_verification"

    # --- render both providers --------------------------------------------
    repository.record_stage(db, job, stage="Rendering (local)", progress=60)
    cost = 0.0
    for attempt in range(1, settings.max_render_attempts + 1):
        repository.update_job(db, job, render_attempts=attempt)
        try:
            if settings.enable_dual_video_generation:
                local_result, veo_result, veo_render_error = video_factory.render_both(topic, script, job.id)
            else:
                local_result, veo_result, veo_render_error = video_factory.render_with_fallback(topic, script, job.id), None, "Dual video generation is disabled (ENABLE_DUAL_VIDEO_GENERATION=false)."
            break
        except Exception as exc:  # noqa: BLE001
            log.exception("Render attempt %d for topic %s failed.", attempt, topic.id)
            if attempt == settings.max_render_attempts:
                repository.update_job(db, job, error_code="RENDER_FAILED", error_message=str(exc))
                repository.record_stage(db, job, status=JobStatus.FAILED_RENDER, stage="Failed render", progress=100)
                return "failed_render"

    repository.record_stage(db, job, stage="Checking", progress=85)

    local_qc = qc_validate(path=local_result.path, narration_text=local_result.narration_text, topic=topic)
    cost += costs.local_render_cost(len(local_result.narration_text))

    veo_qc = None
    veo_fields: dict = {}
    if veo_result is not None:
        veo_qc = qc_validate(path=veo_result.path, narration_text=veo_result.narration_text, topic=topic)
        veo_cost = costs.veo_cost(veo_result.duration_seconds)
        cost += veo_cost
        veo_fields = dict(
            veo_video_location=str(veo_result.path), veo_duration_seconds=veo_result.duration_seconds,
            veo_size_bytes=veo_result.size_bytes, veo_cost_usd=round(veo_cost, 6),
            veo_validation_passed=veo_qc.passed, veo_validation_details=veo_qc.details,
        )
    else:
        veo_fields = dict(veo_error=veo_render_error)

    output_review = run_output_review(
        question=topic.question, narration_text=script.narration_text,
        qc_facts={"local": local_qc.details, "veo": veo_qc.details if veo_qc else None},
    )

    final_report = build_quality_report(
        grounding=grounding, teaching=teaching, output_review=output_review, qc_details=local_qc.details,
        threshold_faithfulness=settings.faithfulness_threshold,
        threshold_teaching=settings.teaching_quality_threshold,
        review_hold_margin=settings.review_hold_margin,
    )

    delivered = local_result if settings.default_video_delivery_provider != "veo" or veo_result is None else veo_result
    delivered_qc = local_qc if delivered is local_result else veo_qc

    script_cost = costs.script_cost(prompt_chars=len(topic.question) * 4, completion_chars=len(script.narration_text)) if script.source == "llm" else 0.0
    cost += script_cost

    final_status = {
        GateDecision.DELIVER.value: JobStatus.SUCCESS,
        GateDecision.HOLD_FOR_REVIEW.value: JobStatus.HOLD_FOR_REVIEW,
    }.get(final_report.gate_decision, JobStatus.FAILED_VERIFICATION)

    repository.update_job(
        db, job,
        provider=delivered.provider,
        video_location=str(delivered.path),
        duration_seconds=delivered.duration_seconds,
        size_bytes=delivered.size_bytes,
        validation_passed=delivered_qc.passed if delivered_qc else None,
        validation_details=delivered_qc.details if delivered_qc else None,
        local_video_location=str(local_result.path), local_duration_seconds=local_result.duration_seconds,
        local_size_bytes=local_result.size_bytes, local_cost_usd=round(costs.local_render_cost(len(local_result.narration_text)), 6),
        local_validation_passed=local_qc.passed, local_validation_details=local_qc.details,
        cost_usd=round(cost, 6),
        quality_report=final_report.to_dict(), gate_decision=final_report.gate_decision,
        faithfulness_score=final_report.faithfulness_score, teaching_quality_score=final_report.teaching_aggregate,
        similarity_score=1.0,
        **veo_fields,
    )
    repository.record_stage(db, job, status=final_status, stage=f"Completed ({final_report.gate_decision})", progress=100)
    return final_status.value
