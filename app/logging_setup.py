"""Logging config. Deliberately does not log request bodies or config dumps
wholesale, so an API key pasted into .env can't end up in a log line.

Every process (the API and the Celery worker) logs to both the console AND
its own rotating file under logs/, so a traceback -- e.g. why a Veo render
failed -- survives after the terminal that printed it is closed or scrolled
past. `component` picks the filename: `logs/api.log` for uvicorn,
`logs/celery.log` for the worker (wired up in app/celery_app.py via the
`setup_logging` signal, which is how Celery lets you replace its own
CLI-driven logging setup with your own).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import settings

_SECRET_FIELD_NAMES = {"api_key", "google_api_key", "openai_api_key", "anthropic_api_key", "database_url"}

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(component: str = "app") -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    log_file = settings.logs_path / f"{component}.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=settings.log_max_bytes, backupCount=settings.log_backup_count, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()  # avoid duplicate lines if configure_logging runs more than once in a process
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Quiet noisy third-party loggers unless the app itself is at DEBUG.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging to console and %s", log_file)
