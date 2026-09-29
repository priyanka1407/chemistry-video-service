"""Step 1 of the grounding check: decompose a script's narration into atomic
factual claims. Opinion/framing/transition sentences are excluded from
scoring but still logged, so the exclusion is auditable (BUILD_SPEC Part 3).
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a fact-checking assistant. You decompose a short educational \
script's narration into individual claims, one assertion per claim.

For each claim, decide whether it is FACTUAL (a checkable statement about chemistry, e.g. \
"the pH scale runs from 0 to 14") or NON_FACTUAL (opinion, framing, a transition sentence, or \
a call to action, e.g. "let's dive in" or "isn't that fascinating"). Only factual claims are \
scored for accuracy later -- non-factual ones are excluded but must still be listed.

Return every claim you find. Do not merge unrelated facts into one claim."""


class Claim(BaseModel):
    text: str = Field(description="The claim, restated as a single self-contained sentence")
    is_factual: bool = Field(description="True if this is a checkable factual assertion")


class ClaimList(BaseModel):
    claims: list[Claim]


def extract_claims(narration_text: str) -> list[Claim]:
    from app.llm.openai_judge_client import judge_structured

    result, _usage = judge_structured(
        system=_SYSTEM_PROMPT,
        user=f"Narration:\n\n{narration_text}",
        response_model=ClaimList,
    )
    return result.claims
