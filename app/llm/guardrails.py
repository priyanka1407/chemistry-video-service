"""Cheap, free, rule-based checks that run before any paid API call.

These exist so a flood of junk/abusive/malformed requests never reaches the
embedding API (cost) or the semantic gate (load) -- the whole point being
"unnecessary prompts... will not cause unnecessary load on the system".
Order matters: rate limit first (cheapest), then input shape checks.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

from app.config import settings

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (all|the|any) (previous|prior|above) instructions",
        r"disregard (all|the|any) (previous|prior|above) instructions",
        r"you are now",
        r"system prompt",
        r"reveal (your|the) (prompt|instructions|api key|system prompt)",
        r"act as (if )?(a|an) (?!chemist)",  # "act as a ..." but chemistry-adjacent role-play is fine
        r"\bDAN\b",
        r"jailbreak",
    ]
]

_GIBBERISH_PATTERN = re.compile(r"^[^a-zA-Z0-9\s]+$")


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str | None = None


def check_input(query: str) -> GuardrailResult:
    text = query.strip()

    if len(text) < settings.min_query_length:
        return GuardrailResult(False, f"Query is too short (min {settings.min_query_length} characters).")
    if len(text) > settings.max_query_length:
        return GuardrailResult(False, f"Query is too long (max {settings.max_query_length} characters).")

    if _GIBBERISH_PATTERN.match(text):
        return GuardrailResult(False, "Query does not contain recognizable text.")

    # repeated-character spam, e.g. "aaaaaaaaaaaaaaaaaaaa"
    if re.search(r"(.)\1{9,}", text):
        return GuardrailResult(False, "Query looks like spam (excessive character repetition).")

    # a query that's almost entirely non-alphanumeric is unlikely to be a real question
    alnum_ratio = sum(c.isalnum() or c.isspace() for c in text) / max(len(text), 1)
    if alnum_ratio < 0.5:
        return GuardrailResult(False, "Query does not look like a well-formed question.")

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(False, "Query was rejected by the prompt-injection guardrail.")

    return GuardrailResult(True)


class RateLimiter:
    """Single-process, in-memory sliding-window limiter, keyed by client IP.

    Adequate for this service's scale; a multi-process deployment would need
    a shared store (e.g. Redis) instead -- noted here rather than pretending
    this generalizes.
    """

    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - 60
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < window_start:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


rate_limiter = RateLimiter(settings.rate_limit_per_minute)
