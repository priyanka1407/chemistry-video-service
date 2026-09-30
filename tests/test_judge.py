"""Unit tests for the verification layer's gating logic (app/judge/report.py)
and the deterministic pre-check (app/judge/grounding.py) -- the parts that
decide whether a video is allowed to ship, independent of any real judge
call."""
from __future__ import annotations

from app.judge.claims import Claim
from app.judge.grounding import ClaimResult, GroundingReport, PreCheckResult, deterministic_pre_check, run_grounding_check
from app.judge.output_review import OutputReviewReport
from app.judge.report import GateDecision, build_quality_report
from app.judge.teaching_quality import DimensionScore, TeachingQualityReport


def _grounding(*, faithfulness=1.0, contradicted=None, skipped=False) -> GroundingReport:
    return GroundingReport(
        faithfulness_score=faithfulness, total_factual_claims=4, supported=int(faithfulness * 4),
        not_found=0, contradicted_claims=contradicted or [], claim_results=[],
        non_factual_claims=[], pre_check=PreCheckResult(passed=True), gate_passed=faithfulness >= 0.8,
        skipped=skipped, skip_reason="no key" if skipped else None,
    )


def _teaching(*, aggregate=4.5, skipped=False) -> TeachingQualityReport:
    scores = {} if skipped else {"objective_coverage": DimensionScore(score=5, justification="Good.")}
    return TeachingQualityReport(scores=scores, aggregate=aggregate, gate_passed=aggregate >= 3.5, skipped=skipped, skip_reason="no key" if skipped else None)


def _report(**kwargs):
    defaults = dict(
        grounding=_grounding(), teaching=_teaching(), output_review=None, qc_details=None,
        threshold_faithfulness=0.8, threshold_teaching=3.5, review_hold_margin=0.1,
    )
    defaults.update(kwargs)
    return build_quality_report(**defaults)


def test_delivers_when_everything_passes():
    report = _report()
    assert report.gate_decision == GateDecision.DELIVER.value


def test_contradicted_claim_is_a_hard_reject_regardless_of_aggregate():
    bad = ClaimResult(claim="pH runs from 0 to 100", verdict="CONTRADICTED", reason="Source says 0-14.", evidence_chunk_ids=["ph_scale#0"])
    report = _report(grounding=_grounding(faithfulness=1.0, contradicted=[bad]))
    assert report.gate_decision == GateDecision.REJECT.value
    assert report.contradicted_claims


def test_low_faithfulness_regenerates():
    report = _report(grounding=_grounding(faithfulness=0.4))
    assert report.gate_decision == GateDecision.REGENERATE.value


def test_faithfulness_just_below_threshold_holds_for_review():
    # threshold 0.8, margin 0.1 -> anything >= 0.72 is "close"
    report = _report(grounding=_grounding(faithfulness=0.75))
    assert report.gate_decision == GateDecision.HOLD_FOR_REVIEW.value


def test_low_teaching_quality_regenerates():
    report = _report(teaching=_teaching(aggregate=2.0))
    assert report.gate_decision == GateDecision.REGENERATE.value


def test_failed_output_qc_triggers_regenerate():
    report = _report(qc_details={"checks": {"has_audio_stream": False, "has_video_stream": True}})
    assert report.gate_decision == GateDecision.REGENERATE.value
    assert report.output_checks["has_audio_stream"] == "FAIL"


def test_skipped_judge_holds_for_review_rather_than_silently_passing():
    report = _report(grounding=_grounding(skipped=True))
    assert report.gate_decision == GateDecision.HOLD_FOR_REVIEW.value
    assert "skipped" in report.decision_reason


def test_video_scores_present_for_local_after_rendering():
    report = _report(qc_details={"checks": {"has_audio_stream": True, "has_video_stream": True}})
    assert "local" in report.video_scores
    row = report.video_scores["local"]
    assert row["teaching_quality"] == 4.5
    assert row["quality_label"] == "Meets standard"
    assert 0 <= row["score_out_of_10"] <= 10


def test_video_scores_omits_local_before_rendering():
    # The preliminary (pre-render) report call passes qc_details=None and no
    # output_review -- nothing to score yet, so no premature "local" row.
    report = _report(qc_details=None, output_review=None)
    assert "local" not in report.video_scores


def test_video_scores_includes_veo_row_with_no_teaching_dimension():
    report = _report(
        qc_details={"checks": {"has_audio_stream": True}},
        veo_grounding=_grounding(faithfulness=0.9),
        veo_qc_details={"checks": {"has_audio_stream": True, "duration_in_range": True}},
    )
    assert "veo" in report.video_scores
    veo_row = report.video_scores["veo"]
    assert veo_row["teaching_quality"] is None
    assert veo_row["faithfulness_pct"] == 90.0
    assert veo_row["quality_label"] == "Meets standard"


def test_video_scores_veo_row_flags_failed_qc_as_below_standard():
    report = _report(
        qc_details={"checks": {"has_audio_stream": True}},
        veo_grounding=_grounding(faithfulness=1.0),
        veo_qc_details={"checks": {"has_audio_stream": False}},
    )
    assert report.video_scores["veo"]["quality_label"] == "Below standard"


def test_deterministic_pre_check_flags_a_number_not_in_the_source():
    result = deterministic_pre_check("The pH scale runs from 0 to 9999.", "ph_scale")
    assert result.passed is False
    assert "9999" in result.unmatched_numbers


def test_deterministic_pre_check_passes_numbers_present_in_source():
    result = deterministic_pre_check("The pH scale runs from 0 to 14.", "ph_scale")
    assert result.passed is True
    assert result.unmatched_numbers == []


def test_run_grounding_check_with_no_factual_claims_trivially_passes():
    claims = [Claim(text="Chemistry is fascinating!", is_factual=False)]
    report = run_grounding_check(claims=claims, topic_id="ph_scale")
    assert report.total_factual_claims == 0
    assert report.gate_passed is True


def test_run_grounding_check_hard_fails_on_a_contradicted_claim(monkeypatch):
    import app.llm.openai_judge_client as openai_judge_client_module
    from app.judge.grounding import ClaimVerdict

    def fake_judge(*, system, user, response_model):
        return ClaimVerdict(verdict="CONTRADICTED", reason="Source disagrees.", evidence_chunk_ids=["ph_scale#0"]), {}

    monkeypatch.setattr(openai_judge_client_module, "judge_structured", fake_judge)

    claims = [Claim(text="The pH scale runs from 0 to 100.", is_factual=True)]
    report = run_grounding_check(claims=claims, topic_id="ph_scale")
    assert report.gate_passed is False
    assert len(report.contradicted_claims) == 1
