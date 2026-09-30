"""Unit tests for the pure slide-selection logic shared by both video
providers: which whole leading slides fit within a duration budget, decided
before any Veo API call, TTS, or ffmpeg work happens (see
app/video/duration_budget.py, used by both local_provider.py and
veo_provider.py)."""
from __future__ import annotations

from app.video.duration_budget import select_slides_within_budget


def test_all_slides_fit_within_budget():
    assert select_slides_within_budget([5.0, 6.0, 4.0], max_duration=20.0) == 3


def test_stops_before_exceeding_budget_never_cuts_mid_slide():
    # 8 + 9 = 17 (fits), + 7 = 24 (would exceed 20) -> stop at 2, not 3
    assert select_slides_within_budget([8.0, 9.0, 7.0], max_duration=20.0) == 2


def test_always_keeps_at_least_one_slide_even_if_it_alone_exceeds_budget():
    assert select_slides_within_budget([25.0, 5.0], max_duration=20.0) == 1


def test_empty_input_selects_nothing():
    assert select_slides_within_budget([], max_duration=20.0) == 0


def test_single_slide_within_budget():
    assert select_slides_within_budget([10.0], max_duration=20.0) == 1


def test_exact_boundary_is_included():
    # 10 + 10 == 20 exactly -> included, not excluded
    assert select_slides_within_budget([10.0, 10.0], max_duration=20.0) == 2
