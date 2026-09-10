"""Cost attribution, from the env-configured rates in app/config.py.

A cache-hit job costs only an embedding call. A seed-generation job costs an
embedding call (to warm the gate) plus a script call plus TTS plus (for the
veo provider) per-second video generation.
"""
from __future__ import annotations

from app.config import settings


def embedding_cost(text: str) -> float:
    approx_tokens = max(len(text) // 4, 1)  # rough chars-per-token heuristic
    return (approx_tokens / 1000) * settings.cost_embedding_per_1k_tokens


def script_cost(prompt_chars: int, completion_chars: int) -> float:
    input_tokens = max(prompt_chars // 4, 1)
    output_tokens = max(completion_chars // 4, 1)
    return (
        (input_tokens / 1_000_000) * settings.cost_script_input_per_1m_tokens
        + (output_tokens / 1_000_000) * settings.cost_script_output_per_1m_tokens
    )


def tts_cost(narration_chars: int) -> float:
    return (narration_chars / 1000) * settings.cost_tts_per_1k_chars


def veo_cost(duration_seconds: float) -> float:
    return duration_seconds * settings.cost_veo_per_second


def local_render_cost(narration_chars: int) -> float:
    return tts_cost(narration_chars)
