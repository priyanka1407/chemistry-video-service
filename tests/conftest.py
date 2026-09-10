"""Shared test fixtures.

Everything external is faked here so the suite runs fully offline: no real
Postgres (SQLite instead, same SQLAlchemy models), no real embeddings/chat
model calls (deterministic hash-based fakes), no real video rendering (a
stub file + a bypassed QC gate). app/qc/validator.py's real ffprobe-based
logic is exercised separately and directly in test_qc_validator.py, which
does need ffmpeg on PATH -- consistent with it being a host prerequisite for
the whole project, not a pip dependency.
"""
from __future__ import annotations

import hashlib
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import pipeline as pipeline_module
from app.db import session as db_session
from app.db.models import Base
from app.llm import script_writer as script_writer_module
from app.llm import semantic_gate
from app.llm.script_writer import GeneratedScript, GeneratedSlide
from app.qc.validator import ValidationResult
from app.video.base import RenderResult


# ---------------------------------------------------------------------------
# Database: isolated SQLite per test, wired into app.db.session's globals
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_jobs.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_SessionLocal", test_session_factory)
    monkeypatch.setattr(db_session, "database_status", "ok (sqlite test)")
    yield test_session_factory


@pytest.fixture
def db(_isolated_db):
    session = _isolated_db()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Semantic gate cache: reset between tests so fixtures don't leak state
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_semantic_cache():
    semantic_gate._topic_vectors = None
    yield
    semantic_gate._topic_vectors = None


# ---------------------------------------------------------------------------
# Fake embeddings: deterministic hashed bag-of-words vectors. Paraphrases of
# the same topic share most tokens (high cosine); unrelated queries don't.
# ---------------------------------------------------------------------------
class FakeEmbeddings:
    dim = 64

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(semantic_gate, "get_embeddings", lambda: fake)
    yield fake


# ---------------------------------------------------------------------------
# Fake chat model: builds a schema-valid script that always mentions every
# required term (parsed out of the real prompt template), so the real
# validation/retry logic in script_writer.py is still exercised for real.
# ---------------------------------------------------------------------------
class _FakeStructuredChat:
    def invoke(self, prompt: str) -> GeneratedScript:
        match = re.search(r"MUST naturally use these terms at least once each: (.+)", prompt)
        terms = [t.strip().rstrip(".") for t in match.group(1).split(",")] if match else ["concept"]
        narration = "This explains " + ", ".join(terms) + " clearly for a learner."
        slide_1 = GeneratedSlide(heading="Overview", bullets=["Key point one", "Key point two"], narration=narration)
        slide_2 = GeneratedSlide(heading="Summary", bullets=["Recap"], narration="That covers the core idea.")
        return GeneratedScript(title="Test Video", slides=[slide_1, slide_2])


class FakeChatModel:
    def with_structured_output(self, _model_cls):
        return _FakeStructuredChat()


@pytest.fixture(autouse=True)
def fake_chat_model(monkeypatch):
    monkeypatch.setattr(script_writer_module, "get_chat_model", lambda: FakeChatModel())
    yield


# ---------------------------------------------------------------------------
# Fake video rendering + QC bypass: writes a stub file instead of actually
# invoking ffmpeg/TTS, so the pipeline/API tests need no host binaries.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fake_video_pipeline(monkeypatch, tmp_path):
    def fake_render(topic, script, job_id):
        out_path = tmp_path / f"{topic.id}_{job_id}.mp4"
        out_path.write_bytes(b"FAKE-MP4-BYTES-FOR-TESTS")
        return RenderResult(
            path=out_path,
            duration_seconds=42.0,
            size_bytes=out_path.stat().st_size,
            provider="local",
            narration_text=script.narration_text,
        )

    def fake_validate(*, path, narration_text, topic):
        return ValidationResult(True, {"checks": {"mocked": True}, "note": "real validator covered separately"})

    monkeypatch.setattr(pipeline_module.video_factory, "render_with_fallback", fake_render)
    monkeypatch.setattr(pipeline_module, "qc_validate", fake_validate)
    yield
