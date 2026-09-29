"""Content-accuracy (grounding/faithfulness) check -- BUILD_SPEC Part 3.

Runs on the generated script, before rendering. Five steps:
  1. (claims.py) claim extraction -- done by the caller, passed in here.
  2. Cheap deterministic pre-checks: numbers/units mentioned in the script
     must appear in the source material. Free, catches the worst
     hallucinations before spending a single judge token.
  3. Evidence retrieval: top-k source chunks per claim (app/rag/store).
  4. Judge: one structured-output call per claim, bounded concurrency.
  5. Aggregate + gate: faithfulness = supported / total_factual_claims; any
     CONTRADICTED is a hard fail regardless of the aggregate.

Known limitation (documented per spec, also in README): this measures
faithfulness to source_material/chemistry_source.pdf, not objective truth.
If that PDF were wrong, a script faithful to it would still score 1.0. That
is the correct trade-off for this system -- the PDF is the one artifact a
human can audit and correct -- but it must be stated, not assumed.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Semaphore

from pydantic import BaseModel, Field

from app.config import settings
from app.judge.claims import Claim
from app.rag import store as rag_store
from app.rag.loader import get_chunks_for_topic

log = logging.getLogger(__name__)

_MAX_CONCURRENT_JUDGE_CALLS = 4

_SYSTEM_PROMPT = """You are a strict fact-checker for an educational chemistry video script. \
You are given ONE claim and a set of source passages. Decide:

- SUPPORTED: the source passages state or directly entail this claim.
- CONTRADICTED: the source passages state something that conflicts with this claim.
- NOT_FOUND: the source passages neither support nor contradict this claim (not mentioned).

Be strict: a claim is only SUPPORTED if the source material actually says it, not merely if it \
sounds like plausible chemistry. Cite the ids of the evidence passages you relied on."""

_NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")


class ClaimVerdict(BaseModel):
    verdict: str = Field(description="One of: SUPPORTED, CONTRADICTED, NOT_FOUND")
    reason: str = Field(description="One or two sentences justifying the verdict")
    evidence_chunk_ids: list[str] = Field(default_factory=list)


@dataclass
class ClaimResult:
    claim: str
    verdict: str
    reason: str
    evidence_chunk_ids: list[str]


@dataclass
class PreCheckResult:
    passed: bool
    checked_numbers: list[str] = field(default_factory=list)
    unmatched_numbers: list[str] = field(default_factory=list)


@dataclass
class GroundingReport:
    faithfulness_score: float
    total_factual_claims: int
    supported: int
    not_found: int
    contradicted_claims: list[ClaimResult]
    claim_results: list[ClaimResult]
    non_factual_claims: list[str]
    pre_check: PreCheckResult
    gate_passed: bool
    skipped: bool = False
    skip_reason: str | None = None


def deterministic_pre_check(narration_text: str, topic_id: str) -> PreCheckResult:
    """Numbers/units in the narration must appear somewhere in the source
    material for this topic. Cheap, exact, catches the most damaging class of
    hallucination (a wrong number) before any paid judge call."""
    source_text = " ".join(c.text for c in get_chunks_for_topic(topic_id))
    numbers_in_script = sorted(set(_NUMBER_PATTERN.findall(narration_text)))
    unmatched = [n for n in numbers_in_script if n not in source_text]
    return PreCheckResult(passed=not unmatched, checked_numbers=numbers_in_script, unmatched_numbers=unmatched)


def _judge_one_claim(claim_text: str, topic_id: str) -> ClaimResult:
    from app.llm.openai_judge_client import judge_structured

    evidence = rag_store.retrieve(topic_id, claim_text, k=settings.retrieval_top_k)
    evidence_block = "\n".join(f"[{c.id}] {c.text}" for c in evidence) or "(no evidence retrieved)"
    result, _usage = judge_structured(
        system=_SYSTEM_PROMPT,
        user=f"Claim: {claim_text}\n\nSource passages:\n{evidence_block}",
        response_model=ClaimVerdict,
    )
    return ClaimResult(
        claim=claim_text,
        verdict=result.verdict.strip().upper(),
        reason=result.reason,
        evidence_chunk_ids=result.evidence_chunk_ids,
    )


def run_grounding_check(*, claims: list[Claim], topic_id: str) -> GroundingReport:
    factual = [c for c in claims if c.is_factual]
    non_factual = [c.text for c in claims if not c.is_factual]
    pre_check = deterministic_pre_check(" ".join(c.text for c in factual), topic_id)

    if not factual:
        return GroundingReport(
            faithfulness_score=1.0, total_factual_claims=0, supported=0, not_found=0,
            contradicted_claims=[], claim_results=[], non_factual_claims=non_factual,
            pre_check=pre_check, gate_passed=pre_check.passed,
        )

    from app.llm.openai_judge_client import JudgeUnavailable

    semaphore = Semaphore(_MAX_CONCURRENT_JUDGE_CALLS)

    def bounded(claim: Claim) -> ClaimResult:
        with semaphore:
            return _judge_one_claim(claim.text, topic_id)

    try:
        with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_JUDGE_CALLS) as pool:
            results = list(pool.map(bounded, factual))
    except JudgeUnavailable as exc:
        log.warning("Grounding judge unavailable (%s) -- SKIPPING the faithfulness gate.", exc)
        return GroundingReport(
            faithfulness_score=0.0, total_factual_claims=len(factual), supported=0, not_found=0,
            contradicted_claims=[], claim_results=[], non_factual_claims=non_factual,
            pre_check=pre_check, gate_passed=False, skipped=True, skip_reason=str(exc),
        )

    supported = [r for r in results if r.verdict == "SUPPORTED"]
    contradicted = [r for r in results if r.verdict == "CONTRADICTED"]
    not_found = [r for r in results if r.verdict == "NOT_FOUND"]
    faithfulness = len(supported) / len(factual)

    gate_passed = pre_check.passed and not contradicted and faithfulness >= settings.faithfulness_threshold

    return GroundingReport(
        faithfulness_score=round(faithfulness, 4),
        total_factual_claims=len(factual),
        supported=len(supported),
        not_found=len(not_found),
        contradicted_claims=contradicted,
        claim_results=results,
        non_factual_claims=non_factual,
        pre_check=pre_check,
        gate_passed=gate_passed,
    )
