"""Retrieval-then-generate: the real source_material/chemistry_source.pdf is
parsed (no mocking -- this is a local file, fully offline) into per-topic
chunks, and retrieval never crosses topic boundaries."""
from __future__ import annotations

from app.rag import store
from app.rag.loader import get_chunks_for_topic, load_chunks


def test_pdf_is_indexed_into_all_three_topics():
    chunks = load_chunks()
    assert set(chunks) == {"ph_scale", "covalent_bond_formation", "ionic_vs_covalent"}
    for topic_id, topic_chunks in chunks.items():
        assert len(topic_chunks) >= 1
        for chunk in topic_chunks:
            assert chunk.topic_id == topic_id
            assert chunk.text.strip()


def test_chunk_ids_are_stable_and_unique_within_a_topic():
    chunks = get_chunks_for_topic("ph_scale")
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("ph_scale#") for i in ids)


def test_retrieval_never_crosses_topic_boundaries():
    results = store.retrieve("ph_scale", "why do atoms share electrons", k=10)
    assert all(c.topic_id == "ph_scale" for c in results)


def test_retrieval_finds_relevant_chunk_for_a_question():
    results = store.retrieve("covalent_bond_formation", "Why do atoms form covalent bonds?", k=2)
    assert results
    joined = " ".join(c.text.lower() for c in results)
    assert "electron" in joined
