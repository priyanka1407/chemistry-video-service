"""ffmpeg-based frame extraction -- no LLM involved, just image extraction.
Shared by app/judge/visual_review.py, the only judge that looks at actual
rendered frames rather than script text."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def extract_sample_frames(video_path: Path, count: int, out_dir: Path) -> list[Path]:
    """Extract `count` evenly-spaced JPEG frames from `video_path` into
    `out_dir`. Timestamps avoid the very first/last instants (often a
    fade-in/out or a static title card with little content yet)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = _probe_duration(video_path)

    frame_paths: list[Path] = []
    for i in range(count):
        timestamp = duration * (i + 1) / (count + 1)
        frame_path = out_dir / f"frame_{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(frame_path)],
            capture_output=True, timeout=30, check=True,
        )
        frame_paths.append(frame_path)
    return frame_paths
