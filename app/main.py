"""FastAPI app: routes + startup lifespan.

Startup does exactly one thing: ensure the DB tables exist
(app.db.session.init_engine). It deliberately does NOT generate any video --
the 3 supported concepts are generated on demand, the first time each is
actually requested, by a Celery worker (see app/tasks.py). This is the
"videos are not generated during startup" requirement: the app is ready the
instant the process starts, and nothing expensive happens until a learner
asks a supported question.
"""
from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app import pipeline
from app.celery_app import celery_app
from app.config import settings
from app.db import session as db_session
from app.db.models import VideoJob
from app.db.repository import get_job, get_topic_master, list_jobs, list_seed_status
from app.db.session import get_session, init_engine
from app.logging_setup import configure_logging
from app.schemas import ErrorOut, HealthOut, JobOut, JobStatus, QualityReportOut, TopicOut, VideoRequest
from app.topics import SEED_TOPIC_IDS, TOPIC_REGISTRY

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(component="api")
    init_engine()
    log.info("Startup complete -- no videos generated at startup; generation happens on first request per topic.")
    yield


app = FastAPI(
    title="Chemistry Video Request Service",
    description=(
        "Requests a short explainer video for a chemistry concept. Only the 3 supported "
        "concepts are ever generated, and only on demand -- see /topics. Any other query is "
        "matched semantically or rejected. Generation runs asynchronously (Celery); poll "
        "GET /jobs/{id} for progress and GET /jobs/{id}/report for the full quality report."
    ),
    version="2.0.0",
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


def _resolve_waiting_job(db: Session, job: VideoJob) -> VideoJob:
    """A job created while another request was already generating the same
    (brand-new) topic doesn't get pushed updates -- resolve it lazily on
    read instead: if that topic's master has since completed, copy its
    result over now."""
    if job.status == JobStatus.PROCESSING.value and not job.is_seed and job.topic_id and job.video_location is None:
        master = get_topic_master(db, job.topic_id)
        if master is not None:
            job = pipeline.copy_master_fields(db, job, master)
            from app.db import repository

            job = repository.record_stage(db, job, status=JobStatus.SUCCESS, stage="Completed (cache hit)", progress=100)
    return job


@app.post("/generate", response_model=JobOut, responses={429: {"model": ErrorOut}})
def generate_video(
    payload: VideoRequest,
    request: Request,
    response: Response,
    db: Session = Depends(db_dependency),
):
    idempotency_key = request.headers.get("Idempotency-Key")
    job = pipeline.handle_query(
        db, payload.query, client_key(request), audience_age=payload.audience_age, idempotency_key=idempotency_key,
    )
    if job.status == JobStatus.REJECTED.value and job.error_code == "RATE_LIMITED":
        raise HTTPException(status_code=429, detail=job.error_message)

    response.status_code = 200 if JobStatus(job.status).is_terminal else 202
    return JobOut.from_job(job)


@app.get("/jobs/{job_id}", response_model=JobOut, responses={404: {"model": ErrorOut}})
def get_job_status(job_id: str, db: Session = Depends(db_dependency)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id!r}")
    job = _resolve_waiting_job(db, job)
    return JobOut.from_job(job)


@app.get("/jobs", response_model=list[JobOut])
def list_all_jobs(limit: int = 50, offset: int = 0, db: Session = Depends(db_dependency)):
    return [JobOut.from_job(j) for j in list_jobs(db, limit=limit, offset=offset)]


@app.delete("/jobs/{job_id}", response_model=JobOut, responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}})
def cancel_job(job_id: str, db: Session = Depends(db_dependency)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id!r}")
    if JobStatus(job.status).is_terminal:
        raise HTTPException(status_code=409, detail=f"Job {job_id} has already finished (status={job.status}).")

    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=False)

    from app.db import repository

    job = repository.update_job(db, job, error_code="CANCELLED", error_message="Cancelled by request.")
    job = repository.record_stage(db, job, status=JobStatus.REJECTED, stage="Cancelled")
    return JobOut.from_job(job)


@app.get("/jobs/{job_id}/report", response_model=QualityReportOut, responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}})
def get_job_report(job_id: str, db: Session = Depends(db_dependency)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id!r}")
    job = _resolve_waiting_job(db, job)
    if not job.quality_report:
        raise HTTPException(status_code=409, detail=f"Job {job_id} has no quality report yet (status={job.status}).")
    return QualityReportOut(job_id=job.id, source_chunk_ids=job.source_chunk_ids or [], **job.quality_report)


@app.get(
    "/jobs/{job_id}/video",
    responses={
        200: {"content": {"video/mp4": {}}, "description": "The rendered mp4 for this job."},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
    },
)
def download_video(job_id: str, variant: str | None = None, db: Session = Depends(db_dependency)):
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id!r}")
    job = _resolve_waiting_job(db, job)
    if job.status != JobStatus.SUCCESS.value:
        raise HTTPException(status_code=409, detail=f"Job {job_id} has no deliverable video (status={job.status}).")

    chosen_variant = (variant or settings.default_video_delivery_provider).lower()
    location = {"local": job.local_video_location, "veo": job.veo_video_location}.get(chosen_variant)
    if not location:
        raise HTTPException(status_code=409, detail=f"Job {job_id} has no {chosen_variant!r} video available.")

    filename = f"{job.topic_id or job.id}_{chosen_variant}.mp4"
    return FileResponse(location, media_type="video/mp4", filename=filename)


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
        status="ok",  # "degraded" no longer applies at startup -- topics are simply "not generated yet"
        database=db_session.database_status,
        llm_provider=settings.llm_provider,
        video_provider=settings.video_provider,
        match_method_default="embedding" if settings.google_api_key else "lexical_fallback",
        topics_ready=ready,
        topics_degraded=degraded,
        ffmpeg_available=shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None,
    )


_PROGRESS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Chemistry Video Request Service</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 3rem auto; padding: 0 1rem; color: #0f172a; }
  h1 { font-size: 1.4rem; }
  select, button { font-size: 1rem; padding: 0.5rem; margin-top: 0.5rem; }
  .bar-track { background: #e2e8f0; border-radius: 8px; height: 18px; margin-top: 1rem; overflow: hidden; }
  .bar-fill { background: #38bdf8; height: 100%; width: 0%; transition: width 0.4s ease; }
  .status { margin-top: 0.75rem; font-size: 0.95rem; }
  video { margin-top: 1rem; max-width: 100%; border-radius: 8px; }
  .error { color: #dc2626; }
  table.scorecard { border-collapse: collapse; margin-top: 1rem; width: 100%; font-size: 0.9rem; display: none; }
  table.scorecard th, table.scorecard td { border: 1px solid #cbd5e1; padding: 0.5rem 0.6rem; text-align: left; }
  table.scorecard th { background: #f1f5f9; }
  .label-ok { color: #16a34a; font-weight: 600; }
  .label-bad { color: #dc2626; font-weight: 600; }
  .label-unverified { color: #ca8a04; font-weight: 600; }
</style>
</head>
<body>
  <h1>Chemistry Video Request Service</h1>
  <p>Only 3 concepts are supported. Pick one and submit -- the first request for a concept
  generates it (retrieval, scripting, grounding + teaching-quality gates, dual rendering);
  every later request for the same concept is served instantly from the database.</p>
  <select id="query">
    <option>How does the pH scale work?</option>
    <option>Why do atoms form covalent bonds?</option>
    <option>What is the difference between ionic and covalent bonding?</option>
  </select>
  <button id="submit">Generate</button>
  <div class="bar-track"><div class="bar-fill" id="bar"></div></div>
  <div class="status" id="status">Idle.</div>
  <video id="video" controls style="display:none"></video>
  <table class="scorecard" id="scorecard">
    <thead>
      <tr><th>Video</th><th>Faithfulness to Source</th><th>Quality Standard</th><th>Score (1-10)</th></tr>
    </thead>
    <tbody id="scorecard-body"></tbody>
  </table>

<script>
let timer = null;
const VIDEO_LABELS = { local: 'Local (gTTS)', veo: 'Veo' };
const LABEL_CLASS = { 'Meets standard': 'label-ok', 'Below standard': 'label-bad', 'Unverified': 'label-unverified' };

function renderScorecard(videoScores) {
  const table = document.getElementById('scorecard');
  const body = document.getElementById('scorecard-body');
  const rows = Object.entries(videoScores || {});
  if (!rows.length) { table.style.display = 'none'; return; }
  body.innerHTML = rows.map(([variant, row]) => {
    const cls = LABEL_CLASS[row.quality_label] || '';
    return `<tr>
      <td>${VIDEO_LABELS[variant] || variant}</td>
      <td>${row.faithfulness_pct}%</td>
      <td class="${cls}">${row.quality_label}</td>
      <td>${row.score_out_of_10} / 10</td>
    </tr>`;
  }).join('');
  table.style.display = 'table';
}

async function poll(jobId) {
  const res = await fetch(`/jobs/${jobId}`);
  const job = await res.json();
  document.getElementById('bar').style.width = job.progress + '%';
  document.getElementById('status').textContent = `${job.status} -- ${job.stage} (${job.progress}%)`;
  const terminal = !['PENDING', 'QUEUED', 'PROCESSING'].includes(job.status);
  if (terminal) {
    clearInterval(timer);
    if (job.status === 'SUCCESS' || job.status === 'HOLD_FOR_REVIEW') {
      if (job.status === 'SUCCESS') {
        const v = document.getElementById('video');
        v.src = `/jobs/${jobId}/video`;
        v.style.display = 'block';
      }
      const reportRes = await fetch(`/jobs/${jobId}/report`);
      if (reportRes.ok) {
        const report = await reportRes.json();
        renderScorecard(report.video_scores);
      }
    } else if (job.error_message) {
      document.getElementById('status').textContent += ` -- ${job.error_message}`;
      document.getElementById('status').classList.add('error');
    }
  }
}

document.getElementById('submit').addEventListener('click', async () => {
  document.getElementById('video').style.display = 'none';
  document.getElementById('scorecard').style.display = 'none';
  document.getElementById('status').classList.remove('error');
  const query = document.getElementById('query').value;
  const res = await fetch('/generate', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query}),
  });
  const job = await res.json();
  if (timer) clearInterval(timer);
  poll(job.id);
  timer = setInterval(() => poll(job.id), 1500);
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def progress_page():
    return _PROGRESS_PAGE
