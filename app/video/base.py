"""The video-generation boundary. Both providers implement this so
app/pipeline.py never branches on which one is active -- app/video/factory.py
is the only place that decides."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.llm.script_writer import Script
from app.topics import Topic


@dataclass
class RenderResult:
    path: Path
    duration_seconds: float
    size_bytes: int
    provider: str
    narration_text: str
    # Seconds actually billed by the provider's API, when that differs from
    # the delivered clip's duration (e.g. Veo bills per second of raw
    # generation, not per second of the final looped/trimmed output). None
    # means "use duration_seconds for cost accounting" (true for the local
    # provider, where there's no such distinction).
    billed_seconds: float | None = None


class VideoProvider(Protocol):
    name: str

    def render(self, topic: Topic, script: Script, job_id: str) -> RenderResult:
        """Render `script` for `topic` and return the finished mp4 on disk.
        Must raise on failure -- callers handle retries/fallbacks."""
        ...
