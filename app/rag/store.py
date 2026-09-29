"""Chunk retrieval: given a topic and a query (the topic's own question, when
writing a script; a claim, when grounding one), return the top-k most
relevant chunks.

Reuses the same embedding boundary as the semantic gate
(app/llm/semantic_gate.py) so there is exactly one place in the codebase that
knows how to call an embeddings model, and the same lexical fallback shape
when no embedding backend is configured -- retrieval degrades gracefully
instead of erroring, same philosophy as the rest of the app.
"""
from __future__ import annotations

import logging
import math
import re

from app.llm.factory import ProviderNotConfigured, get_embeddings
from app.rag.loader import Chunk, get_chunks_for_topic

log = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "the", "is", "are", "do", "does", "did", "how", "what", "why",
    "when", "which", "who", "and", "or", "of", "to", "in", "on", "for", "with",
    "between", "about", "explain", "tell", "me", "can", "you", "please", "it",
    "its", "that", "this", "these", "those", "be", "was", "were", "as", "at",
}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return 0.0 if norm_a == 0 or norm_b == 0 else dot / (norm_a * norm_b)


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def _lexical_score(query: str, text: str) -> float:
    q, t = _tokenize(query), _tokenize(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


def retrieve(topic_id: str, query: str, k: int = 4) -> list[Chunk]:
    """Top-k chunks for `topic_id` most relevant to `query`. Never returns
    chunks from a different topic -- retrieval is always scoped to the
    matched topic, since that's the only material that question's script is
    allowed to be grounded in."""
    chunks = get_chunks_for_topic(topic_id)
    if not chunks:
        return []

    try:
        embedder = get_embeddings()
        query_vec = embedder.embed_query(query)
        scored = [(_cosine(query_vec, embedder.embed_query(c.text)), c) for c in chunks]
    except ProviderNotConfigured:
        scored = [(_lexical_score(query, c.text), c) for c in chunks]
    except Exception:  # noqa: BLE001 - degrade to lexical rather than fail retrieval
        log.exception("Embedding-based retrieval failed -- falling back to lexical scoring.")
        scored = [(_lexical_score(query, c.text), c) for c in chunks]

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in scored[:k]]


def all_chunks_for_topic(topic_id: str) -> list[Chunk]:
    """The full indexed section for a topic -- used when generation should
    see the whole grounding text rather than a top-k slice (the corpus per
    topic is small: a handful of chunks)."""
    return get_chunks_for_topic(topic_id)
