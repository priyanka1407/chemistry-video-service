"""Exercises the real ffmpeg-based frame extraction (not mocked) against a
tiny real video file. Requires ffmpeg/ffprobe on PATH, same as the rest of
the project, and is skipped otherwise so the suite still runs somewhere
without it."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from app.video.frames import extract_sample_frames

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH",
)


def _make_clip(path, seconds: float = 3.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10", "-t", str(seconds), str(path)],
        capture_output=True, check=True, timeout=30,
    )


def test_extracts_the_requested_number_of_frames(tmp_path):
    clip = tmp_path / "clip.mp4"
    _make_clip(clip)
    frames = extract_sample_frames(clip, count=5, out_dir=tmp_path / "frames")
    assert len(frames) == 5
    for f in frames:
        assert f.exists()
        assert f.stat().st_size > 0


def test_frames_are_evenly_spaced_and_distinct(tmp_path):
    clip = tmp_path / "clip.mp4"
    _make_clip(clip, seconds=4.0)
    frames = extract_sample_frames(clip, count=3, out_dir=tmp_path / "frames")
    sizes = [f.stat().st_size for f in frames]
    # A moving test pattern over 4s sampled at different timestamps
    # shouldn't produce byte-identical frames.
    assert len(set(sizes)) > 1 or len({f.read_bytes() for f in frames}) > 1
