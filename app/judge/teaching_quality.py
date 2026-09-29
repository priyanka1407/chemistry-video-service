"""Teaching-quality rubric judge -- BUILD_SPEC Part 4.

Runs on the script, before rendering, exactly like the grounding check but
scoring pedagogy rather than factual accuracy: does this script actually
teach the concept well to its target audience? The rubric itself lives in
rubrics/teaching_quality.yaml as a version-controlled file, not an inline
prompt string, so a prompt change is a reviewable diff.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, create_model

from app.config import settings

log = logging.getLogger(__name__)

RUBRIC_PATH = Path(__file__).resolve().parent.parent.parent / "rubrics" / "teaching_quality.yaml"


@lru_cache(maxsize=1)
def load_rubric() -> dict:
    with open(RUBRIC_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_prompt(rubric: dict, question: str, audience_age: int, script_text: str) -> str:
    lines = [
        f"You are grading a short educational video SCRIPT against a fixed rubric.",
        f"Learning objective (the question the script must teach): {question!r}",
        f"Target audience age: {audience_age}",
        "",
        "Score each dimension 1-5 using ONLY the band descriptors below -- do not invent your "
        "own criteria. Give a one-line justification per dimension citing what you observed.",
        "",
    ]
    for dim_name, dim in rubric["dimensions"].items():
        lines.append(f"## {dim_name}: {dim['description']}")
        for band, desc in sorted(dim["bands"].items(), reverse=True):
            lines.append(f"  {band}: {desc}")
        lines.append("")

    lines.append("## Calibration examples")
    for ex in rubric.get("few_shot_examples", []):
        lines.append(f"- {ex['label']}: {ex['note']}")
    lines.append("")
    lines.append(f"## Script to grade\n{script_text}")
    return "\n".join(lines)


class DimensionScore(BaseModel):
    score: int = Field(ge=1, le=5)
    justification: str


def _response_model(rubric: dict) -> type[BaseModel]:
    fields = {name: (DimensionScore, ...) for name in rubric["dimensions"]}
    return create_model("TeachingQualityScores", **fields)  # type: ignore[call-overload]


@dataclass
class TeachingQualityReport:
    scores: dict[str, DimensionScore]
    aggregate: float
    gate_passed: bool
    skipped: bool = False
    skip_reason: str | None = None


def run_teaching_quality_check(*, question: str, audience_age: int, script_text: str) -> TeachingQualityReport:
    from app.llm.openai_judge_client import JudgeUnavailable, judge_structured

    rubric = load_rubric()
    response_model = _response_model(rubric)
    prompt = _build_prompt(rubric, question, audience_age, script_text)

    try:
        result, _usage = judge_structured(
            system="You are a rigorous, consistent instructional-design evaluator.",
            user=prompt,
            response_model=response_model,
        )
    except JudgeUnavailable as exc:
        log.warning("Teaching-quality judge unavailable (%s) -- SKIPPING the teaching-quality gate.", exc)
        return TeachingQualityReport(scores={}, aggregate=0.0, gate_passed=False, skipped=True, skip_reason=str(exc))

    scores: dict[str, DimensionScore] = {name: getattr(result, name) for name in rubric["dimensions"]}
    weights = rubric["weights"]
    aggregate = sum(scores[name].score * weights[name] for name in scores)

    return TeachingQualityReport(
        scores=scores,
        aggregate=round(aggregate, 3),
        gate_passed=aggregate >= settings.teaching_quality_threshold,
    )
