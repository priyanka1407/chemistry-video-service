"""Shared test fixtures.

Everything external is faked here so the suite runs fully offline: no real
Postgres (SQLite instead, same SQLAlchemy models), no real embeddings/chat
model calls (deterministic hash-based fakes), no real OpenAI judge calls (a
deterministic fake that always grades a well-formed fake script as
DELIVER-worthy, exercising the real gating/aggregation logic in
app/judge/report.py), no real video rendering (a stub file + a bypassed QC
gate), and Celery runs in `task_always_eager` mode so the whole async
pipeline executes synchronously in-process -- no Redis broker, no separate
worker needed.

app/qc/validator.py's real ffprobe-based logic is exercised separately and
directly in test_qc_validator.py, which does need ffmpeg on PATH.
"""
from __future__ import annotations

import hashlib
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import tasks as tasks_module
from app.celery_app import celery_app
from app.db import session as db_session
from app.db.models import Base
from app.judge.claims import Claim, ClaimList
from app.judge.grounding import ClaimVerdict
from app.judge.output_review import OutputReviewVerdict
from app.llm import script_writer as script_writer_module
from app.llm import semantic_gate
from app.llm.script_writer import GeneratedScript, GeneratedSlide
from app.qc.validator import ValidationResult
from app.rag import loader as rag_loader
from app.rag import store as rag_store
from app.video.base import RenderResult


# ---------------------------------------------------------------------------
# Celery: force eager (synchronous, in-process) execution -- no broker needed.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _celery_eager():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield


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
# Semantic gate + RAG chunk cache: reset between tests so fixtures don't leak
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_caches():
    semantic_gate._topic_vectors = None
    rag_loader.reset_cache()
    yield
    semantic_gate._topic_vectors = None
    rag_loader.reset_cache()


# ---------------------------------------------------------------------------
# Fake embeddings: deterministic hashed bag-of-words vectors. Paraphrases of
# the same topic share most tokens (high cosine); unrelated queries don't.
# Used by both the semantic gate and RAG retrieval (app/rag/store.py).
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
    monkeypatch.setattr(rag_store, "get_embeddings", lambda: fake)
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
# Fake OpenAI judge: one function services every structured-output call
# (claim extraction, per-claim grounding verdict, teaching-quality rubric,
# final output review) by inspecting the requested response_model. Always
# grades the fake script above as fully grounded and high quality, so the
# real gating/aggregation code in app/judge/report.py runs against a
# deterministic, DELIVER-worthy input -- see test_grounding.py and
# test_teaching_quality.py for unit tests that exercise the FAIL paths by
# swapping this fake out for a narrower one.
# ---------------------------------------------------------------------------
class FakeJudge:
    def __call__(self, *, system: str, user: str, response_model):
        name = response_model.__name__
        usage = {"prompt_tokens": 10, "completion_tokens": 10}

        if name == "ClaimList":
            narration = user.split("Narration:", 1)[-1].strip()
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
            return ClaimList(claims=[Claim(text=s, is_factual=True) for s in sentences]), usage

        if name == "ClaimVerdict":
            return ClaimVerdict(verdict="SUPPORTED", reason="Matches the source material.", evidence_chunk_ids=[]), usage

        if name == "OutputReviewVerdict":
            return OutputReviewVerdict(coherent_and_on_topic=True, concerns=[], justification="Coherent and on-topic."), usage

        if name == "TeachingQualityScores":
            fields = {f: {"score": 5, "justification": "Meets the top band."} for f in response_model.model_fields}
            return response_model(**fields), usage

        raise AssertionError(f"FakeJudge got an unexpected response_model: {name}")


@pytest.fixture(autouse=True)
def fake_openai_judge(monkeypatch):
    import app.llm.openai_judge_client as openai_judge_client_module

    fake = FakeJudge()
    monkeypatch.setattr(openai_judge_client_module, "judge_structured", fake)
    yield fake


# ---------------------------------------------------------------------------
# Fake video rendering + QC bypass: writes a stub file instead of actually
# invoking ffmpeg/TTS, so the pipeline/API tests need no host binaries.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fake_video_pipeline(monkeypatch, tmp_path):
    def _stub_result(topic, script, job_id, provider, duration=42.0):
        out_path = tmp_path / f"{topic.id}_{job_id}_{provider}.mp4"
        out_path.write_bytes(f"FAKE-MP4-BYTES-{provider}".encode())
        return RenderResult(
            path=out_path, duration_seconds=duration, size_bytes=out_path.stat().st_size,
            provider=provider, narration_text=script.narration_text,
        )

    def fake_render_with_fallback(topic, script, job_id):
        return _stub_result(topic, script, job_id, "local")

    def fake_render_both(topic, script, job_id):
        return _stub_result(topic, script, job_id, "local"), _stub_result(topic, script, job_id, "veo", duration=16.0), None

    def fake_validate(*, path, narration_text, topic):
        return ValidationResult(True, {"checks": {"mocked": True}, "note": "real validator covered separately"})

    monkeypatch.setattr(tasks_module.video_factory, "render_with_fallback", fake_render_with_fallback)
    monkeypatch.setattr(tasks_module.video_factory, "render_both", fake_render_both)
    monkeypatch.setattr(tasks_module, "qc_validate", fake_validate)
    yield
