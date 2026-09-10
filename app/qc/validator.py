"""Validates a rendered video before it is ever allowed to reach a learner.

This is the answer to "ensure proper validation of the videos generated":
non-determinism in the LLM/media pipeline is expected, so nothing produced
by it is trusted until it passes here. Two independent checks:

  1. Media check (ffprobe): the file actually has a video stream and an
     audio stream, non-trivial duration, non-zero size, sane resolution.
  2. Content check: the narration that was actually spoken mentions every
     `must_mention` keyword for the topic -- catches an off-topic or
     truncated script even if the LLM-side check in script_writer.py was
     somehow bypassed (e.g. a future provider change).

Both must pass for `validation_passed=True`. Every individual check result
is recorded in `validation_details` for audit, not just the pass/fail bit.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.topics import Topic

log = logging.getLogger(__name__)

MIN_FILE_SIZE_BYTES = 5_000
MIN_DURATION_SECONDS = 1.0
MAX_DURATION_SECONDS = 300.0
MIN_DIMENSION_PX = 100


@dataclass
class ValidationResult:
    passed: bool
    details: dict


def _ffprobe_streams(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def validate(*, path: Path, narration_text: str, topic: Topic) -> ValidationResult:
    checks: dict[str, bool] = {}
    detail: dict = {"file": str(path)}

    # --- file existence / size --------------------------------------------
    exists = path.exists()
    checks["file_exists"] = exists
    if not exists:
        detail["error"] = "output file does not exist"
        return ValidationResult(False, {"checks": checks, **detail})

    size_bytes = path.stat().st_size
    detail["size_bytes"] = size_bytes
    checks["min_file_size"] = size_bytes >= MIN_FILE_SIZE_BYTES

    # --- media streams -----------------------------------------------------
    try:
        probe = _ffprobe_streams(path)
        streams = probe.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        duration = float(probe.get("format", {}).get("duration", 0.0))

        checks["has_video_stream"] = len(video_streams) >= 1
        checks["has_audio_stream"] = len(audio_streams) >= 1
        checks["duration_in_range"] = MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS

        if video_streams:
            width = int(video_streams[0].get("width", 0))
            height = int(video_streams[0].get("height", 0))
            checks["resolution_sane"] = width >= MIN_DIMENSION_PX and height >= MIN_DIMENSION_PX
            detail["resolution"] = f"{width}x{height}"
        else:
            checks["resolution_sane"] = False

        detail["duration_seconds"] = duration
        detail["video_streams"] = len(video_streams)
        detail["audio_streams"] = len(audio_streams)
    except Exception as exc:  # noqa: BLE001
        log.exception("ffprobe validation failed for %s", path)
        checks["has_video_stream"] = False
        checks["has_audio_stream"] = False
        checks["duration_in_range"] = False
        checks["resolution_sane"] = False
        detail["ffprobe_error"] = str(exc)

    # --- content check -------------------------------------------------------
    narration_lower = narration_text.lower()
    missing_terms = [term for term in topic.must_mention if term.lower() not in narration_lower]
    checks["content_on_topic"] = len(missing_terms) == 0
    detail["missing_terms"] = missing_terms
    detail["narration_word_count"] = len(narration_text.split())

    passed = all(checks.values())
    detail["checks"] = checks
    return ValidationResult(passed, detail)
