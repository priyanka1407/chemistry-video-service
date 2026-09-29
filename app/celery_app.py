"""The async job pipeline's broker/worker configuration.

`POST /generate` never blocks on generation: it does the (fast, cheap)
guardrail + semantic-match work inline, then either serves a cached result
or hands off to this Celery app and returns immediately. Run a worker with:

    celery -A app.celery_app worker --loglevel=info

`task_always_eager` is forced True by the test suite (see tests/conftest.py)
so the whole pipeline runs synchronously, in-process, with no Redis broker
and no separate worker needed -- the same "fully offline" philosophy the
rest of the test suite already follows for Postgres/LLM/video.
"""
from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging, worker_shutting_down

from app.config import settings

celery_app = Celery(
    "chemistry_video_service",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_track_started=True,
    task_acks_late=True,  # a worker that dies mid-task returns it to the queue, not vanishes
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
)

celery_app.autodiscover_tasks(["app"], related_name="tasks")


@setup_logging.connect
def _on_setup_logging(**kwargs):
    # Connecting anything to this signal tells Celery to skip its own
    # CLI-driven logging setup entirely and use ours instead -- so a worker
    # started with `--loglevel=info` still logs to the console AND to
    # logs/celery.log (see app/logging_setup.py), instead of console-only
    # output that vanishes once the terminal's scrollback is gone.
    from app.logging_setup import configure_logging

    configure_logging(component="celery")


@worker_shutting_down.connect
def _on_worker_shutting_down(**kwargs):  # pragma: no cover - exercised by real worker shutdown, not tests
    # task_acks_late + this signal is the "graceful shutdown" half of the
    # spec's requirement: an in-flight task is not acked until it finishes,
    # so Celery's own warm/cold shutdown redelivers it to another worker
    # rather than losing it. Nothing extra to do here beyond letting Celery's
    # default warm-shutdown behavior (finish current task, stop consuming
    # new ones) run; this hook exists so that behavior is visible in the
    # code, not just in Celery's defaults.
    import logging

    logging.getLogger(__name__).warning("Worker shutting down -- in-flight tasks will be redelivered (task_acks_late).")
