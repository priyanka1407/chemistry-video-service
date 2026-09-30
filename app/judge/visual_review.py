"""Post-render visual spot-check -- the vision-based half of BUILD_SPEC
Part 5's optional VLM check, and the only judge in this codebase that looks
at actual rendered video frames rather than script text.

Every other check verifies the SCRIPT (grounding, teaching-quality) or
mechanical facts about the file (app/qc/validator.py) or the narration TEXT
(app/judge/output_review.py) -- none of them can catch a generative video
model (Veo) hallucinating wrong visuals, inventing an incorrect diagram, or
rendering garbled on-screen text, because none of them ever look at a pixel.
This does.

Cheap by design: a handful of frames at low image-detail is enough for a
coherence spot-check, not a frame-by-frame audit. Frame extraction itself
(app/video/frames.py) is pure ffmpeg -- free, no LLM involved; only the one
judge call per video costs anything, and at VISUAL_REVIEW_FRAME_COUNT=5,
low detail, gpt-4o-mini, that's a small fraction of a cent per video (see
README's cost breakdown) -- independent of video length, since a fixed
small number of frames is sampled regardless of duration.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are doing a visual spot-check on frames sampled evenly across a finished \
educational video -- the one check that can catch what text-only review cannot: wrong or garbled \
visuals, incorrect on-screen text, or imagery that doesn't match the topic. You are given a few \
sampled frames, the topic the video is meant to teach, and the narration that plays over it. Judge \
only what you can actually see in the frames -- do not assume the visuals are correct just because \
the narration is."""


class VisualReviewVerdict(BaseModel):
    coherent_and_on_topic: bool = Field(description="Do the frames depict something coherent and relevant to the topic?")
    garbled_or_incorrect_on_screen_text: bool = Field(
        description="Is there any garbled, nonsensical, or factually wrong on-screen text visible in any frame?"
    )
    concerns: list[str] = Field(default_factory=list, description="Specific issues found, if any")
    justification: str


@dataclass
class VisualReviewReport:
    passed: bool
    concerns: list[str]
    justification: str
    skipped: bool = False
    skip_reason: str | None = None


def _encode_frame(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def run_visual_review(*, question: str, narration_text: str, frame_paths: list[Path]) -> VisualReviewReport:
    from app.config import settings
    from app.llm.openai_judge_client import JudgeUnavailable, judge_structured

    if not frame_paths:
        return VisualReviewReport(passed=False, concerns=[], justification="", skipped=True, skip_reason="No frames were extracted to review.")

    content: list[dict] = [
        {"type": "text", "text": f"Topic the video must teach: {question!r}\n\nNarration spoken over these frames:\n{narration_text}"},
    ]
    for path in frame_paths:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{_encode_frame(path)}", "detail": settings.visual_review_detail},
        })

    try:
        result, _usage = judge_structured(system=_SYSTEM_PROMPT, user=content, response_model=VisualReviewVerdict)
    except JudgeUnavailable as exc:
        log.warning("Visual review judge unavailable (%s) -- SKIPPING the visual review check.", exc)
        return VisualReviewReport(passed=False, concerns=[], justification="", skipped=True, skip_reason=str(exc))

    passed = result.coherent_and_on_topic and not result.garbled_or_incorrect_on_screen_text
    return VisualReviewReport(passed=passed, concerns=result.concerns, justification=result.justification)
