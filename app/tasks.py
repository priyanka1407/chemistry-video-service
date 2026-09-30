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
import shutil

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
from app.judge.visual_review import VisualReviewReport, run_visual_review
from app.llm.openai_judge_client import JudgeUnavailable
from app.llm.script_writer import Script, generate_highlight_script, generate_script
from app.qc.validator import validate as qc_validate
from app.rag.store import all_chunks_for_topic
from app.schemas import JobStatus
from app.topics import Topic, get_topic
from app.video import factory as video_factory
from app.video.frames import extract_sample_frames

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
        grounding = _extract_and_ground(script.narration_text, topic.id)
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


def _extract_and_ground(narration_text: str, topic_id: str) -> GroundingReport:
    """Shared claim-extraction + grounding-check call, with the same
    JudgeUnavailable -> SKIPPED handling used for the main script."""
    try:
        claims = extract_claims(narration_text)
        return run_grounding_check(claims=claims, topic_id=topic_id)
    except JudgeUnavailable as exc:
        log.warning("Claim extraction unavailable (%s) -- treating grounding as SKIPPED.", exc)
        return GroundingReport(
            faithfulness_score=0.0, total_factual_claims=0, supported=0, not_found=0,
            contradicted_claims=[], claim_results=[], non_factual_claims=[],
            pre_check=PreCheckResult(passed=True), gate_passed=False, skipped=True, skip_reason=str(exc),
        )


def _generate_and_verify_highlight(topic: Topic, audience_age: int, fallback_script: Script) -> tuple[Script, GroundingReport]:
    """A short, purpose-built script for the Veo video (see
    app/llm/script_writer.py::generate_highlight_script) -- sized to fit
    VEO_MAX_DURATION_SECONDS from the moment it's written, not the full
    lesson script trimmed after the fact. Independently verified against the
    same source material via the same grounding pipeline as the main script.

    If every regeneration attempt still fails grounding, falls back to the
    main script's own first slide -- already verified as part of a script
    that passed the full faithfulness gate, so it's a safe, grounded choice
    even though its own faithfulness is re-checked in isolation here."""
    correction_issues: list[str] | None = None
    highlight_script = fallback_script
    grounding: GroundingReport

    for attempt in range(settings.max_regeneration_attempts + 1):
        highlight_script = generate_highlight_script(topic, audience_age=audience_age, correction_issues=correction_issues)
        grounding = _extract_and_ground(highlight_script.narration_text, topic.id)

        if grounding.gate_passed or grounding.skipped or attempt >= settings.max_regeneration_attempts:
            break

        log.warning("Veo highlight script attempt %d for topic %s failed grounding -- regenerating.", attempt + 1, topic.id)
        correction_issues = [
            f"Claim not supported by the source material, remove or fix: \"{r.claim}\""
            for r in grounding.claim_results if r.verdict != "SUPPORTED"
        ] or [f"Claim contradicted the source: \"{c.claim}\" -- {c.reason}" for c in grounding.contradicted_claims]

    if not (grounding.gate_passed or grounding.skipped):
        log.warning(
            "All highlight attempts failed grounding for topic %s -- falling back to the verified main script's first slide.",
            topic.id,
        )
        highlight_script = Script(
            title=fallback_script.title, slides=fallback_script.slides[:1],
            source=fallback_script.source, model=fallback_script.model,
            source_chunk_ids=fallback_script.source_chunk_ids,
        )
        grounding = _extract_and_ground(highlight_script.narration_text, topic.id)

    return highlight_script, grounding


def _run_visual_reviews(topic: Topic, job_id: str, local_result, veo_result) -> tuple[VisualReviewReport | None, VisualReviewReport | None]:
    """Sample frames from each rendered video and run the vision judge on
    them -- the only check that looks at actual pixels rather than script
    text, so it's the only thing that can catch a generative video model
    hallucinating wrong visuals or garbled on-screen text. No-ops (returns
    None, None) if ENABLE_VISUAL_REVIEW is off; frame extraction failing
    (e.g. a corrupt render) is treated as a skip, not a task-crashing error,
    same "no check may be skipped silently" discipline as every other judge."""
    if not settings.enable_visual_review:
        return None, None

    frame_dir = settings.artifacts_path / f"_frames_{job_id}"
    try:
        local_visual = _visual_review_for(topic, local_result, frame_dir / "local")
        veo_visual = _visual_review_for(topic, veo_result, frame_dir / "veo") if veo_result is not None else None
        return local_visual, veo_visual
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)


def _visual_review_for(topic: Topic, render_result, frame_dir) -> VisualReviewReport:
    try:
        frames = extract_sample_frames(render_result.path, settings.visual_review_frame_count, frame_dir)
    except Exception as exc:  # noqa: BLE001 - a broken extraction shouldn't crash the whole job
        log.exception("Frame extraction failed for %s -- SKIPPING the visual review check.", render_result.path)
        return VisualReviewReport(passed=False, concerns=[], justification="", skipped=True, skip_reason=str(exc))
    return run_visual_review(question=topic.question, narration_text=render_result.narration_text, frame_paths=frames)


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

    # --- a separate, deliberately short script for Veo, sized to fit the
    # duration cap from the moment it's written (see
    # app/llm/script_writer.py::generate_highlight_script and
    # app/video/veo_provider.py) -- verified against the source material
    # independently of the main (local-video) script above.
    highlight_script: Script | None = None
    highlight_grounding: GroundingReport | None = None
    if settings.enable_dual_video_generation:
        repository.record_stage(db, job, stage="Scripting (Veo highlight)", progress=50)
        highlight_script, highlight_grounding = _generate_and_verify_highlight(topic, audience_age, script)

    # --- render both providers --------------------------------------------
    repository.record_stage(db, job, stage="Rendering (local)", progress=60)
    cost = 0.0
    for attempt in range(1, settings.max_render_attempts + 1):
        repository.update_job(db, job, render_attempts=attempt)
        try:
            if settings.enable_dual_video_generation:
                local_result, veo_result, veo_render_error = video_factory.render_both(topic, script, job.id, veo_script=highlight_script)
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
        veo_cost = costs.veo_cost(veo_result.billed_seconds if veo_result.billed_seconds is not None else veo_result.duration_seconds)
        cost += veo_cost
        veo_fields = dict(
            veo_video_location=str(veo_result.path), veo_duration_seconds=veo_result.duration_seconds,
            veo_size_bytes=veo_result.size_bytes, veo_cost_usd=round(veo_cost, 6),
            veo_validation_passed=veo_qc.passed, veo_validation_details=veo_qc.details,
            veo_faithfulness_score=highlight_grounding.faithfulness_score if highlight_grounding else None,
        )
    else:
        veo_fields = dict(veo_error=veo_render_error)

    output_review = run_output_review(
        question=topic.question, narration_text=script.narration_text,
        qc_facts={"local": local_qc.details, "veo": veo_qc.details if veo_qc else None},
    )

    local_visual, veo_visual = _run_visual_reviews(topic, job.id, local_result, veo_result)

    final_report = build_quality_report(
        grounding=grounding, teaching=teaching, output_review=output_review, qc_details=local_qc.details,
        threshold_faithfulness=settings.faithfulness_threshold,
        threshold_teaching=settings.teaching_quality_threshold,
        review_hold_margin=settings.review_hold_margin,
        veo_grounding=highlight_grounding if veo_result is not None else None,
        veo_qc_details=veo_qc.details if veo_qc else None,
        local_visual_review=local_visual,
        veo_visual_review=veo_visual,
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
