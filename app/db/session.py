"""Engine/session setup, with the Postgres -> SQLite startup fallback.

Sync SQLAlchemy throughout: FastAPI runs sync `def` route handlers in a
threadpool, so this never blocks the event loop, and it avoids pulling in an
async driver just for a service this size.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

log = logging.getLogger(__name__)

_engine = None
_SessionLocal: sessionmaker | None = None
database_status = "unknown"


def init_engine():
    """Try Postgres first; fall back to SQLite if it's unreachable and the
    fallback is allowed. Called once at startup. Idempotent thereafter."""
    global _engine, _SessionLocal, database_status

    if _engine is not None:
        return _engine

    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect():
            pass
        Base.metadata.create_all(engine)
        _engine = engine
        database_status = "ok (postgres)"
        log.info("Connected to configured database and ensured video_jobs table exists.")
    except OperationalError as exc:
        if not settings.db_allow_sqlite_fallback:
            log.error("Database unreachable and DB_ALLOW_SQLITE_FALLBACK=false: %s", exc)
            raise
        log.warning(
            "Configured DATABASE_URL unreachable (%s). Falling back to SQLite at %s.",
            exc.__class__.__name__,
            settings.db_fallback_sqlite_path,
        )
        engine = create_engine(f"sqlite:///{settings.db_fallback_sqlite_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        _engine = engine
        database_status = "degraded (sqlite fallback)"

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()
