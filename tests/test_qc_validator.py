"""Exercises the real ffprobe-based validator (not mocked) against tiny
real video files built with ffmpeg. Requires ffmpeg/ffprobe on PATH -- same
host prerequisite as the rest of the project -- and is skipped otherwise so
the suite still runs somewhere without it.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from app.qc.validator import validate
from app.topics import get_topic

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not found on PATH",
)


def _make_clip(path, *, with_audio: bool, seconds: float = 1.5) -> None:
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono"]
    cmd += ["-t", str(seconds), "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)


def test_valid_video_passes(tmp_path):
    topic = get_topic("ph_scale")
    clip = tmp_path / "valid.mp4"
    _make_clip(clip, with_audio=True)

    narration = "The pH scale measures acid strength using the hydrogen ion concentration."
    result = validate(path=clip, narration_text=narration, topic=topic)

    assert result.passed is True
    assert result.details["checks"]["has_video_stream"] is True
    assert result.details["checks"]["has_audio_stream"] is True
    assert result.details["checks"]["content_on_topic"] is True


def test_missing_audio_stream_fails(tmp_path):
    topic = get_topic("ph_scale")
    clip = tmp_path / "silent.mp4"
    _make_clip(clip, with_audio=False)

    narration = "The pH scale measures acid strength using the hydrogen ion concentration."
    result = validate(path=clip, narration_text=narration, topic=topic)

    assert result.passed is False
    assert result.details["checks"]["has_audio_stream"] is False


def test_off_topic_narration_fails_content_check(tmp_path):
    topic = get_topic("ph_scale")
    clip = tmp_path / "valid2.mp4"
    _make_clip(clip, with_audio=True)

    narration = "This video is about baking a chocolate cake, not chemistry at all."
    result = validate(path=clip, narration_text=narration, topic=topic)

    assert result.passed is False
    assert result.details["checks"]["content_on_topic"] is False
    assert set(result.details["missing_terms"]) == set(topic.must_mention)


def test_missing_file_fails_fast(tmp_path):
    topic = get_topic("ph_scale")
    missing = tmp_path / "does_not_exist.mp4"
    result = validate(path=missing, narration_text="anything", topic=topic)
    assert result.passed is False
    assert result.details["checks"]["file_exists"] is False
