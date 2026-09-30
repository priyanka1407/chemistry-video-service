"""The one place that calls OpenAI directly. Every judge (grounding, teaching
quality, final review) goes through `judge_structured()` -- single wrapper
with retry, timeout, and token accounting, same discipline the rest of the
codebase applies to its LangChain boundary (app/llm/factory.py).

This is deliberately independent of LLM_PROVIDER: generation can run on
Gemini/OpenAI/Anthropic, but judging always runs on OpenAI, because the spec
calls for OpenAI specifically as the LLM-as-judge.
"""
from __future__ import annotations

import logging
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class JudgeUnavailable(RuntimeError):
    """Raised when OPENAI_API_KEY is not configured -- callers must treat a
    missing judge as a SKIP, never as a silent pass."""


_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise JudgeUnavailable("OPENAI_API_KEY is not set -- required for the OpenAI LLM-as-judge.")
        from openai import OpenAI

        _client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_judge_timeout_seconds)
    return _client


def judge_structured(*, system: str, user: str | list[dict[str, Any]], response_model: type[T]) -> tuple[T, dict]:
    """One structured-output call to the judge model, with retry on
    transient failure. Returns (parsed_result, usage) where usage carries
    prompt/completion token counts for cost accounting.

    `user` is normally plain text; app/judge/visual_review.py passes a list
    of OpenAI content parts (text + image_url) instead, for the one judge
    that reviews actual rendered frames rather than script text -- the
    Chat Completions API accepts either shape as message content unchanged."""
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(1, settings.openai_judge_max_retries + 1):
        try:
            completion = client.beta.chat.completions.parse(
                model=settings.openai_judge_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=response_model,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise ValueError("Judge returned no parsed structured output.")
            usage = {
                "prompt_tokens": completion.usage.prompt_tokens if completion.usage else 0,
                "completion_tokens": completion.usage.completion_tokens if completion.usage else 0,
            }
            return parsed, usage
        except Exception as exc:  # noqa: BLE001 - retry any transient provider/parsing failure
            last_exc = exc
            log.warning("OpenAI judge call attempt %d/%d failed: %s", attempt, settings.openai_judge_max_retries, exc)
            if attempt < settings.openai_judge_max_retries:
                time.sleep(min(2 ** attempt, 10))

    assert last_exc is not None
    raise last_exc
