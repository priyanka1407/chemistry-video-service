"""Does a learner query map onto one of the 3 supported topics?

Every topic carries several phrasings (app/topics.py); all are embedded once
at startup and cached in memory, and the best-matching phrasing wins, which
makes the gate far more tolerant of wording than comparing against a single
canonical sentence.

If the embedding call fails (no key, network, quota) this degrades to a
deterministic lexical (token-overlap) matcher instead of erroring the
request -- see MatchResult.method, which is always reported back to the
caller so degraded matching is never silent.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass

from app import topics as topics_module
from app.config import settings
from app.llm.factory import ProviderNotConfigured, get_embeddings

log = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "the", "is", "are", "do", "does", "did", "how", "what", "why",
    "when", "which", "who", "and", "or", "of", "to", "in", "on", "for", "with",
    "between", "about", "explain", "tell", "me", "can", "you", "please", "it",
    "its", "that", "this", "these", "those", "be", "was", "were", "as", "at",
    "by", "from", "into", "work", "works", "difference", "different",
}

_lock = threading.Lock()
_topic_vectors: dict[str, list[tuple[str, list[float]]]] | None = None


@dataclass
class MatchResult:
    topic_id: str | None
    matched_question: str | None
    score: float
    method: str  # "embedding" | "lexical_fallback"
    query_embedding: list[float] | None
    embedding_model: str | None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _lexical_score(query: str, phrasing: str) -> float:
    q, p = _tokenize(query), _tokenize(phrasing)
    if not q or not p:
        return 0.0
    overlap = q & p
    return len(overlap) / len(q | p)  # Jaccard


def warm_embeddings() -> bool:
    """Embed every topic phrasing once and cache it. Returns True if the
    embedding backend is usable, False if the caller should expect every
    subsequent match to run through the lexical fallback."""
    global _topic_vectors
    with _lock:
        if _topic_vectors is not None:
            return bool(_topic_vectors)
        try:
            embedder = get_embeddings()
            vectors: dict[str, list[tuple[str, list[float]]]] = {}
            for topic in topics_module.TOPIC_REGISTRY:
                phrasing_vectors = []
                for phrasing in topic.all_phrasings:
                    vec = embedder.embed_query(phrasing)
                    phrasing_vectors.append((phrasing, vec))
                vectors[topic.id] = phrasing_vectors
            _topic_vectors = vectors
            log.info("Embedded %d topics' phrasings for the semantic gate.", len(vectors))
            return True
        except ProviderNotConfigured as exc:
            log.warning("Embeddings unavailable (%s) -- semantic gate will use the lexical fallback.", exc)
            _topic_vectors = {}
            return False
        except Exception:  # noqa: BLE001 - any provider/network failure degrades, doesn't crash
            log.exception("Embedding warmup failed -- semantic gate will use the lexical fallback.")
            _topic_vectors = {}
            return False


def match(query: str) -> MatchResult:
    warm_embeddings()
    assert _topic_vectors is not None

    if _topic_vectors:
        try:
            embedder = get_embeddings()
            query_vec = embedder.embed_query(query)
            best_topic_id, best_phrasing, best_score = None, None, -1.0
            for topic_id, phrasing_vectors in _topic_vectors.items():
                for phrasing, vec in phrasing_vectors:
                    score = _cosine(query_vec, vec)
                    if score > best_score:
                        best_topic_id, best_phrasing, best_score = topic_id, phrasing, score

            if best_score >= settings.semantic_similarity_threshold:
                topic = topics_module.get_topic(best_topic_id) if best_topic_id else None
                return MatchResult(
                    topic_id=best_topic_id,
                    matched_question=topic.question if topic else best_phrasing,
                    score=best_score,
                    method="embedding",
                    query_embedding=query_vec,
                    embedding_model=settings.embedding_model,
                )
            return MatchResult(
                topic_id=None,
                matched_question=None,
                score=best_score,
                method="embedding",
                query_embedding=query_vec,
                embedding_model=settings.embedding_model,
            )
        except Exception:  # noqa: BLE001 - fall through to lexical on any per-request failure
            log.exception("Embedding call failed for a query -- falling back to lexical matching for this request.")

    # --- lexical fallback -------------------------------------------------
    best_topic_id, best_phrasing, best_score = None, None, -1.0
    for topic in topics_module.TOPIC_REGISTRY:
        for phrasing in topic.all_phrasings:
            score = _lexical_score(query, phrasing)
            if score > best_score:
                best_topic_id, best_phrasing, best_score = topic.id, phrasing, score

    if best_score >= settings.lexical_similarity_threshold:
        topic = topics_module.get_topic(best_topic_id) if best_topic_id else None
        return MatchResult(
            topic_id=best_topic_id,
            matched_question=topic.question if topic else best_phrasing,
            score=best_score,
            method="lexical_fallback",
            query_embedding=None,
            embedding_model=None,
        )
    return MatchResult(
        topic_id=None,
        matched_question=None,
        score=max(best_score, 0.0),
        method="lexical_fallback",
        query_embedding=None,
        embedding_model=None,
    )
