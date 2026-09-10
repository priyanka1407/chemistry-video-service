"""Logging config. Deliberately does not log request bodies or config dumps
wholesale, so an API key pasted into .env can't end up in a log line."""
from __future__ import annotations

import logging

from app.config import settings

_SECRET_FIELD_NAMES = {"api_key", "google_api_key", "openai_api_key", "anthropic_api_key", "database_url"}


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # Quiet noisy third-party loggers unless the app itself is at DEBUG.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
