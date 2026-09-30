"""Shared duration-budget helper, used by both video providers
(app/video/local_provider.py and app/video/veo_provider.py) so a video is
sized correctly from the start rather than generated long and truncated
after the fact -- see either provider's module docstring for the full
reasoning."""
from __future__ import annotations


def select_slides_within_budget(durations: list[float], max_duration: float) -> int:
    """How many leading slides (in order) fit within `max_duration`, given
    each candidate slide's own narration duration. Always keeps at least
    one slide, even if it alone exceeds the budget -- the caller's own
    last-resort trim handles that rare case, not dropping every slide and
    delivering nothing. Pure function, independent of ffmpeg/TTS/Veo, so the
    actual selection decision is unit-testable on its own -- see
    tests/test_duration_budget.py.
    """
    if not durations:
        return 0
    total = durations[0]
    count = 1
    for d in durations[1:]:
        if total + d > max_duration:
            break
        total += d
        count += 1
    return count
