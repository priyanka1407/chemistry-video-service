"""Cosine similarity matching against the 3 seed topics, using the
deterministic FakeEmbeddings from conftest."""
from __future__ import annotations

from app.config import settings
from app.llm import semantic_gate


def test_paraphrase_matches_ph_topic():
    result = semantic_gate.match("explain PH")
    assert result.method == "embedding"
    assert result.topic_id == "ph_scale"
    assert result.score >= settings.semantic_similarity_threshold


def test_exact_seed_question_matches_itself():
    result = semantic_gate.match("Why do atoms form covalent bonds?")
    assert result.topic_id == "covalent_bond_formation"


def test_unrelated_query_is_rejected():
    result = semantic_gate.match("who is the winner of world cup 2026?")
    assert result.topic_id is None
    assert result.score < settings.semantic_similarity_threshold


def test_lexical_fallback_used_when_embeddings_unavailable(monkeypatch):
    def _raise():
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr(semantic_gate, "get_embeddings", _raise)
    result = semantic_gate.match("How does the pH scale work?")
    assert result.method == "lexical_fallback"
    assert result.topic_id == "ph_scale"
