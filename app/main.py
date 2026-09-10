"""FastAPI app: routes + startup lifespan.

Startup does two things, in order, before the app accepts traffic:
  1. Ensure the `video_jobs` table exists (app.db.session.init_engine).
  2. Ensure the 3 seed videos exist (app.pipeline.run_warmup) -- "on
     starting the app, check if there are rows for the 3 required queries;
     if not, generate them."
A seed that fails all its retries/fallbacks is marked FAILED but does not
stop the app from starting -- see /health for degraded topics.
"""
from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import pipeline
from app.config import settings
from app.db import session as db_session
from app.db.repository import get_job, list_jobs, list_seed_status
from app.db.session import get_session, init_engine
from app.logging_setup import configure_logging
from app.schemas import ErrorOut, HealthOut, JobOut, JobStatus, TopicOut, VideoRequest
from app.topics import SEED_TOPIC_IDS, TOPIC_REGISTRY

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_engine()

    if settings.warmup_on_startup:
        db = get_session()
        try:
            results = pipeline.run_warmup(db)
            failed = [t for t, status in results.items() if status == JobStatus.FAILED.value]
            if failed:
                msg = f"Seed topics failed to generate after all fallbacks: {failed}"
                if settings.strict_startup:
                    raise RuntimeError(msg)
                log.error(msg + " -- app is starting anyway (STRICT_STARTUP=false).")
            log.info("Startup warmup complete: %s", results)
        finally:
            db.close()
    else:
        log.info("WARMUP_ON_STARTUP=false -- skipping seed generation.")

    yield


app = FastAPI(
    title="Chemistry Video Request Service",
    description=(
        "Requests a short explainer video for a chemistry concept. Only the 3 "
        "seed concepts generated at startup are ever served; any other query is "
        "matched semantically or rejected -- see /topics."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


def db_dependency() -> Session:
    db = get_session()
    try:
        yield db
    finally:
        db.close()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/generate", response_model=JobOut, responses={429: {"model": ErrorOut}})
def generate_video(payload: VideoRequest, request: Request, db: Session = Depends(db_dependency)):
    job = pipeline.handle_query(db, payload.query, client_key(request))
    if job.status == JobStatus.REJECTED.value and job.error_code == "RATE_LIMITED":
        raise HTTPException(status_code=429, detail=job.error_message)
    return job


@app.get("/jobs/{job_id}", response_model=JobOut, responses={404: {"model": ErrorOut}})
def get_job_status(job_id: str, db: Session = Depends(db_dependency)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id!r}")
    return job


@app.get("/jobs", response_model=list[JobOut])
def list_all_jobs(limit: int = 50, offset: int = 0, db: Session = Depends(db_dependency)):
    return list_jobs(db, limit=limit, offset=offset)


@app.get(
    "/jobs/{job_id}/video",
    responses={
        200: {"content": {"video/mp4": {}}, "description": "The rendered mp4 for this job."},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
    },
)
def download_video(job_id: str, db: Session = Depends(db_dependency)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id!r}")
    if job.status != JobStatus.SUCCESS.value or not job.video_location:
        raise HTTPException(status_code=409, detail=f"Job {job_id} has no video (status={job.status}).")
    filename = f"{job.topic_id or job.id}.mp4"
    return FileResponse(job.video_location, media_type="video/mp4", filename=filename)


@app.get("/topics", response_model=list[TopicOut])
def list_topics():
    return [
        TopicOut(id=t.id, question=t.question, example_phrasings=list(t.phrasings))
        for t in TOPIC_REGISTRY
    ]


@app.get("/health", response_model=HealthOut)
def health(db: Session = Depends(db_dependency)):
    seed_status = list_seed_status(db)
    ready = [tid for tid in SEED_TOPIC_IDS if seed_status.get(tid) and seed_status[tid].status == JobStatus.SUCCESS.value]
    degraded = [tid for tid in SEED_TOPIC_IDS if tid not in ready]

    return HealthOut(
        status="ok" if not degraded else "degraded",
        database=db_session.database_status,
        llm_provider=settings.llm_provider,
        video_provider=settings.video_provider,
        match_method_default="embedding" if settings.google_api_key else "lexical_fallback",
        topics_ready=ready,
        topics_degraded=degraded,
        ffmpeg_available=shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None,
    )
