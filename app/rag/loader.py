"""Extracts text from source_material/*.pdf and splits it into small,
retrievable chunks, one set per topic.

The PDF is the only allowed source: `app/topics.py` no longer hands the
script writer free rein, it hands it chunks pulled straight out of this file
at request time, and the same chunks are what the faithfulness judge checks
every claim against afterwards (app/judge/grounding.py). Re-parsing the PDF
on every cold start (rather than embedding a hardcoded copy of the text in
Python) is deliberate: it's what makes "faithful to the source text"
verifiable against something a human editor can open and change.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# Matches the `## <topic_id>` markers written by scripts/generate_source_material.py.
_SECTION_MARKER = re.compile(r"##\s*([a-z0-9_]+)\s*")

# Rough sentence boundary: end punctuation followed by whitespace and a
# capital/number. Good enough for well-formed prose like this source file.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

_MAX_CHUNK_CHARS = 500


@dataclass(frozen=True)
class Chunk:
    id: str
    topic_id: str
    text: str


def _extract_pdf_text(path: Path) -> str:
    import PyPDF2

    reader = PyPDF2.PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _split_into_sections(full_text: str) -> dict[str, str]:
    """Split on `## topic_id` markers -> {topic_id: raw section text}."""
    pieces = _SECTION_MARKER.split(full_text)
    # re.split with one capture group yields [pre, topic_id, body, topic_id, body, ...]
    sections: dict[str, str] = {}
    for i in range(1, len(pieces), 2):
        topic_id = pieces[i].strip()
        body = pieces[i + 1] if i + 1 < len(pieces) else ""
        sections[topic_id] = body
    return sections


def _chunk_section(topic_id: str, body: str) -> list[Chunk]:
    # Normalize whitespace introduced by PDF line-wrapping before splitting
    # into sentences, so a chunk never contains a mid-word PDF line-break.
    normalized = re.sub(r"\s+", " ", body).strip()
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(normalized) if s.strip()]

    chunks: list[Chunk] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        if current and current_len + len(sentence) > _MAX_CHUNK_CHARS:
            chunks.append(Chunk(id=f"{topic_id}#{len(chunks)}", topic_id=topic_id, text=" ".join(current)))
            current, current_len = [], 0
        current.append(sentence)
        current_len += len(sentence) + 1
    if current:
        chunks.append(Chunk(id=f"{topic_id}#{len(chunks)}", topic_id=topic_id, text=" ".join(current)))
    return chunks


_lock = threading.Lock()
_chunks_by_topic: dict[str, list[Chunk]] | None = None


def load_chunks() -> dict[str, list[Chunk]]:
    """Parse the source PDF once and cache the resulting chunks in memory."""
    global _chunks_by_topic
    with _lock:
        if _chunks_by_topic is not None:
            return _chunks_by_topic

        path = settings.source_material_path
        if not path.exists():
            raise FileNotFoundError(
                f"Source material PDF not found at {path}. Run "
                "`python scripts/generate_source_material.py` to generate it."
            )

        full_text = _extract_pdf_text(path)
        sections = _split_into_sections(full_text)

        result: dict[str, list[Chunk]] = {}
        for topic_id, body in sections.items():
            result[topic_id] = _chunk_section(topic_id, body)
            log.info("Indexed %d chunks for topic %s from %s", len(result[topic_id]), topic_id, path.name)

        _chunks_by_topic = result
        return result


def get_chunks_for_topic(topic_id: str) -> list[Chunk]:
    chunks = load_chunks()
    return chunks.get(topic_id, [])


def reset_cache() -> None:
    """Test hook: forget the cached chunks so a fixture-swapped PDF path is re-read."""
    global _chunks_by_topic
    with _lock:
        _chunks_by_topic = None
