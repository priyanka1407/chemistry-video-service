"""Unit tests for the vision-based post-render judge (app/judge/visual_review.py)
-- the only check that looks at actual rendered frames rather than script
text, so it's the only thing that can catch a generative video model
hallucinating wrong visuals or garbled on-screen text."""
from __future__ import annotations

from pathlib import Path

from app.judge.visual_review import VisualReviewVerdict, run_visual_review


def test_no_frames_is_a_skip_not_a_crash(tmp_path):
    report = run_visual_review(question="How does the pH scale work?", narration_text="text", frame_paths=[])
    assert report.skipped is True
    assert report.passed is False


def test_passes_when_the_judge_says_coherent(tmp_path):
    frame = tmp_path / "frame_0.jpg"
    frame.write_bytes(b"FAKE-JPEG-BYTES")
    # The autouse FakeJudge fixture (conftest.py) always returns a coherent,
    # non-garbled verdict for VisualReviewVerdict.
    report = run_visual_review(question="How does the pH scale work?", narration_text="The pH scale...", frame_paths=[frame])
    assert report.passed is True
    assert report.skipped is False


def test_fails_when_the_judge_flags_garbled_text(monkeypatch, tmp_path):
    import app.llm.openai_judge_client as openai_judge_client_module

    def fake_judge(*, system, user, response_model):
        return VisualReviewVerdict(
            coherent_and_on_topic=True, garbled_or_incorrect_on_screen_text=True,
            concerns=["Garbled text overlay in frame 2"], justification="Text is unreadable.",
        ), {}

    monkeypatch.setattr(openai_judge_client_module, "judge_structured", fake_judge)

    frame = tmp_path / "frame_0.jpg"
    frame.write_bytes(b"FAKE-JPEG-BYTES")
    report = run_visual_review(question="q", narration_text="n", frame_paths=[frame])
    assert report.passed is False
    assert report.concerns


def test_fails_when_the_judge_says_off_topic(monkeypatch, tmp_path):
    import app.llm.openai_judge_client as openai_judge_client_module

    def fake_judge(*, system, user, response_model):
        return VisualReviewVerdict(
            coherent_and_on_topic=False, garbled_or_incorrect_on_screen_text=False,
            concerns=["Frames show unrelated content"], justification="Does not match the topic.",
        ), {}

    monkeypatch.setattr(openai_judge_client_module, "judge_structured", fake_judge)

    frame = tmp_path / "frame_0.jpg"
    frame.write_bytes(b"FAKE-JPEG-BYTES")
    report = run_visual_review(question="q", narration_text="n", frame_paths=[frame])
    assert report.passed is False
