"""QualityReport: the single object every quality signal rolls up into, and
the only thing the gating decision is made from -- BUILD_SPEC Part 6.

A job is never delivered because rendering succeeded; it's delivered because
this report says DELIVER. Every field here is persisted on the job row
(VideoJob.quality_report) so "how many of the last N videos were factually
grounded" is one query away, not a re-run of the whole pipeline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from app.judge.grounding import GroundingReport
from app.judge.output_review import OutputReviewReport
from app.judge.teaching_quality import TeachingQualityReport
from app.judge.visual_review import VisualReviewReport


class GateDecision(str, Enum):
    DELIVER = "DELIVER"
    REGENERATE = "REGENERATE"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    REJECT = "REJECT"


@dataclass
class QualityReport:
    faithfulness_score: float
    contradicted_claims: list[dict]
    teaching_scores: dict[str, dict]
    teaching_aggregate: float
    output_checks: dict[str, str]  # check_name -> "PASS" | "FAIL" | "SKIP"
    gate_decision: str
    decision_reason: str
    grounding_details: dict = field(default_factory=dict)
    # Per-video-variant scorecard -- {"local": {...}, "veo": {...}}, each with
    # faithfulness_pct, quality_label, and a single score_out_of_10 a human
    # can judge the video by at a glance. See GET /jobs/{id}/report and the
    # table rendered on the progress page ("/").
    video_scores: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _output_checks_from_qc(qc_details: dict | None) -> dict[str, str]:
    if not qc_details:
        return {"mechanical_qc": "SKIP"}
    checks = qc_details.get("checks", {})
    return {name: ("PASS" if ok else "FAIL") for name, ok in checks.items()} or {"mechanical_qc": "SKIP"}


def _qc_pass_rate(checks: dict[str, str]) -> float:
    relevant = [v for v in checks.values() if v != "SKIP"]
    if not relevant:
        return 0.0
    return sum(1 for v in relevant if v == "PASS") / len(relevant)


def _quality_label(qc_checks: dict[str, str], extra_ok: bool = True) -> str:
    if any(v == "FAIL" for v in qc_checks.values()):
        return "Below standard"
    if not extra_ok:
        return "Below standard"
    if all(v == "SKIP" for v in qc_checks.values()):
        return "Unverified"
    return "Meets standard"


def _video_score(*, faithfulness: float, qc_checks: dict[str, str], teaching_aggregate: float | None) -> dict:
    """One row of the judge scorecard for a single delivered video file.
    `teaching_aggregate` is None for the Veo highlight, which isn't graded
    against the full 5-dimension lesson rubric -- only faithfulness and
    mechanical/output QC apply to a short companion clip."""
    qc_rate = _qc_pass_rate(qc_checks)
    if teaching_aggregate is not None:
        score = 10 * (0.4 * faithfulness + 0.4 * (teaching_aggregate / 5) + 0.2 * qc_rate)
        quality_ok = teaching_aggregate >= 3.5 and not any(v == "FAIL" for v in qc_checks.values())
    else:
        score = 10 * (0.7 * faithfulness + 0.3 * qc_rate)
        quality_ok = not any(v == "FAIL" for v in qc_checks.values())

    return {
        "faithfulness_pct": round(faithfulness * 100, 1),
        "teaching_quality": round(teaching_aggregate, 2) if teaching_aggregate is not None else None,
        "quality_label": _quality_label(qc_checks, extra_ok=quality_ok),
        "qc_checks": qc_checks,
        "score_out_of_10": round(max(0.0, min(score, 10.0)), 1),
    }


def build_quality_report(
    *,
    grounding: GroundingReport,
    teaching: TeachingQualityReport,
    output_review: OutputReviewReport | None,
    qc_details: dict | None,
    threshold_faithfulness: float,
    threshold_teaching: float,
    review_hold_margin: float,
    veo_grounding: GroundingReport | None = None,
    veo_qc_details: dict | None = None,
    local_visual_review: VisualReviewReport | None = None,
    veo_visual_review: VisualReviewReport | None = None,
) -> QualityReport:
    output_checks = _output_checks_from_qc(qc_details)
    if output_review is not None:
        output_checks["llm_output_review"] = (
            "SKIP" if output_review.skipped else ("PASS" if output_review.passed else "FAIL")
        )
    if local_visual_review is not None:
        output_checks["visual_review"] = (
            "SKIP" if local_visual_review.skipped else ("PASS" if local_visual_review.passed else "FAIL")
        )

    veo_output_checks = _output_checks_from_qc(veo_qc_details)
    if veo_visual_review is not None:
        veo_output_checks["visual_review"] = (
            "SKIP" if veo_visual_review.skipped else ("PASS" if veo_visual_review.passed else "FAIL")
        )

    contradicted = [
        {"claim": c.claim, "reason": c.reason, "evidence_chunk_ids": c.evidence_chunk_ids}
        for c in grounding.contradicted_claims
    ]
    teaching_scores = {
        name: {"score": s.score, "justification": s.justification} for name, s in teaching.scores.items()
    }

    decision, reason = _decide(
        grounding=grounding,
        teaching=teaching,
        output_checks=output_checks,
        threshold_faithfulness=threshold_faithfulness,
        threshold_teaching=threshold_teaching,
        review_hold_margin=review_hold_margin,
    )

    video_scores: dict[str, dict] = {}
    if qc_details is not None or output_review is not None:
        video_scores["local"] = _video_score(
            faithfulness=grounding.faithfulness_score, qc_checks=output_checks, teaching_aggregate=teaching.aggregate,
        )
    if veo_grounding is not None:
        video_scores["veo"] = _video_score(
            faithfulness=veo_grounding.faithfulness_score,
            qc_checks=veo_output_checks,
            teaching_aggregate=None,
        )

    return QualityReport(
        faithfulness_score=grounding.faithfulness_score,
        contradicted_claims=contradicted,
        teaching_scores=teaching_scores,
        teaching_aggregate=teaching.aggregate,
        output_checks=output_checks,
        gate_decision=decision.value,
        decision_reason=reason,
        grounding_details={
            "total_factual_claims": grounding.total_factual_claims,
            "supported": grounding.supported,
            "not_found": grounding.not_found,
            "pre_check_passed": grounding.pre_check.passed,
            "unmatched_numbers": grounding.pre_check.unmatched_numbers,
            "skipped": grounding.skipped,
            "skip_reason": grounding.skip_reason,
        },
        video_scores=video_scores,
    )


def _decide(
    *,
    grounding: GroundingReport,
    teaching: TeachingQualityReport,
    output_checks: dict[str, str],
    threshold_faithfulness: float,
    threshold_teaching: float,
    review_hold_margin: float,
) -> tuple[GateDecision, str]:
    if grounding.skipped or teaching.skipped:
        reasons = []
        if grounding.skipped:
            reasons.append(f"grounding judge skipped ({grounding.skip_reason})")
        if teaching.skipped:
            reasons.append(f"teaching-quality judge skipped ({teaching.skip_reason})")
        return GateDecision.HOLD_FOR_REVIEW, "Cannot verify automatically: " + "; ".join(reasons)

    if grounding.contradicted_claims:
        claims = ", ".join(c.claim for c in grounding.contradicted_claims[:3])
        return GateDecision.REJECT, f"Hard fail: contradicted claim(s) found: {claims}"

    if any(v == "FAIL" for v in output_checks.values()):
        failed = [k for k, v in output_checks.items() if v == "FAIL"]
        return GateDecision.REGENERATE, f"Mechanical/output QC failed: {failed}"

    faithfulness_ok = grounding.faithfulness_score >= threshold_faithfulness
    teaching_ok = teaching.aggregate >= threshold_teaching

    if faithfulness_ok and teaching_ok:
        return GateDecision.DELIVER, "All gates passed."

    near_faithfulness = grounding.faithfulness_score >= threshold_faithfulness * (1 - review_hold_margin)
    near_teaching = teaching.aggregate >= threshold_teaching * (1 - review_hold_margin)
    if (not faithfulness_ok and near_faithfulness) or (not teaching_ok and near_teaching):
        return (
            GateDecision.HOLD_FOR_REVIEW,
            f"Close to threshold: faithfulness={grounding.faithfulness_score}, "
            f"teaching_aggregate={teaching.aggregate}",
        )

    return (
        GateDecision.REGENERATE,
        f"Below threshold: faithfulness={grounding.faithfulness_score} "
        f"(need {threshold_faithfulness}), teaching_aggregate={teaching.aggregate} (need {threshold_teaching})",
    )
