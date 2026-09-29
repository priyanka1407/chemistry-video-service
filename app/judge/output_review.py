"""Final output-quality review -- the OpenAI-judge half of BUILD_SPEC Part 5.

The mechanical checks (duration sanity, audio presence, frame sampling,
file integrity) run with no LLM involved at all, in app/qc/validator.py --
that is deliberate, per spec ("No LLM involved in the required checks").

This module is the *optional*, LLM-based complement: given the script that
was supposed to be spoken, the narration that was actually produced, and the
mechanical QC facts, ask the judge whether the finished artifact plausibly
delivers a coherent, on-topic educational video for the stated question. It
is a cheap proxy for the "send sampled video frames to a vision model" spot
check the spec calls out as optional -- no frames are inspected here, which
is the known limitation this check cannot cover (garbled on-screen text or
wrong visuals would not be caught; see README "Known limitations").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are doing a final sanity check on a finished educational video before it \
ships to a learner. You do not see the video itself -- only the question it was meant to \
answer, the narration script that was recorded, and the mechanical QC facts about the file. \
Judge whether this narration, as written, would deliver a coherent, on-topic educational video \
for the stated question if spoken over matching visuals. Flag anything that reads as \
incoherent, truncated mid-thought, or off-topic."""


class OutputReviewVerdict(BaseModel):
    coherent_and_on_topic: bool = Field(description="Would this narration deliver a coherent, on-topic video?")
    concerns: list[str] = Field(default_factory=list, description="Specific issues found, if any")
    justification: str


@dataclass
class OutputReviewReport:
    passed: bool
    concerns: list[str]
    justification: str
    skipped: bool = False
    skip_reason: str | None = None


def run_output_review(*, question: str, narration_text: str, qc_facts: dict) -> OutputReviewReport:
    from app.llm.openai_judge_client import JudgeUnavailable, judge_structured

    user_prompt = (
        f"Question the video must answer: {question!r}\n\n"
        f"Narration actually recorded:\n{narration_text}\n\n"
        f"Mechanical QC facts: {qc_facts}"
    )
    try:
        result, _usage = judge_structured(
            system=_SYSTEM_PROMPT, user=user_prompt, response_model=OutputReviewVerdict,
        )
    except JudgeUnavailable as exc:
        log.warning("Output-review judge unavailable (%s) -- SKIPPING the final review gate.", exc)
        return OutputReviewReport(passed=False, concerns=[], justification="", skipped=True, skip_reason=str(exc))

    return OutputReviewReport(
        passed=result.coherent_and_on_topic,
        concerns=result.concerns,
        justification=result.justification,
    )
