"""The cheap default renderer: Pillow slide images + TTS narration + ffmpeg
mux, one segment per slide, concatenated into a single mp4.

Cost is effectively just the (usually free) TTS call -- no per-second
generative-video billing -- which is why this is the default VIDEO_PROVIDER.

Capped at LOCAL_MAX_DURATION_SECONDS the same way Veo is capped, and for the
same reason: each candidate slide's narration is measured via TTS BEFORE any
image is drawn or ffmpeg mux runs, and select_slides_within_budget decides
how many whole leading slides fit -- a slide is dropped whole or not at all,
never cut mid-sentence. The main script itself
(app/llm/script_writer.py::generate_script) is now word-budgeted to target
this duration from the moment it's written, so this selection should rarely
need to drop anything in practice; it's a safety net, not the primary
sizing mechanism, mirroring the same "not first created and then truncated"
design as the Veo highlight script.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.llm.script_writer import Script
from app.topics import Topic
from app.video import tts
from app.video.base import RenderResult
from app.video.duration_budget import select_slides_within_budget

log = logging.getLogger(__name__)

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_FONT_CANDIDATES_REGULAR = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _draw_slide(heading: str, bullets: tuple[str, ...], accent: str, out_path: Path) -> None:
    w, h = settings.video_width, settings.video_height
    img = Image.new("RGB", (w, h), color="#0F172A")
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, w, 12], fill=accent)

    heading_font = _load_font(_FONT_CANDIDATES, size=int(h * 0.09))
    bullet_font = _load_font(_FONT_CANDIDATES_REGULAR, size=int(h * 0.05))

    margin_x = int(w * 0.08)
    y = int(h * 0.22)

    for line in textwrap.wrap(heading, width=28):
        draw.text((margin_x, y), line, font=heading_font, fill=accent)
        y += int(h * 0.12)

    y += int(h * 0.05)
    for bullet in bullets:
        for i, line in enumerate(textwrap.wrap(bullet, width=48)):
            prefix = "•  " if i == 0 else "   "
            draw.text((margin_x, y), prefix + line, font=bullet_font, fill="#E2E8F0")
            y += int(h * 0.07)
        y += int(h * 0.02)

    img.save(out_path)


def _mux_segment(image_path: Path, audio_path: Path, out_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-c:v", "libx264", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "160k",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.video_fps),
            "-shortest",
            str(out_path),
        ],
        capture_output=True, timeout=120, check=True,
    )


def _concat_segments(segment_paths: list[Path], out_path: Path, work_dir: Path) -> None:
    list_file = work_dir / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.name}'" for p in segment_paths), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
        capture_output=True, timeout=120, check=True, cwd=str(work_dir),
    )


class LocalProvider:
    name = "local"

    def render(self, topic: Topic, script: Script, job_id: str) -> RenderResult:
        if not ffmpeg_available():
            raise RuntimeError("ffmpeg/ffprobe not found on PATH -- required for the local video provider.")

        work_dir = settings.artifacts_path / f"_work_{job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Synthesize every slide's narration first (cheap) so which
            # slides fit LOCAL_MAX_DURATION_SECONDS is decided before doing
            # any image rendering or ffmpeg work -- a slide is only ever
            # dropped whole, never cut mid-sentence.
            audio_paths: list[Path] = []
            durations: list[float] = []
            for i, slide in enumerate(script.slides):
                audio_path = work_dir / f"audio_{i}.mp3"
                tts.synthesize(slide.narration, audio_path)
                audio_paths.append(audio_path)
                durations.append(self._probe_duration(audio_path))

            keep_count = select_slides_within_budget(durations, settings.local_max_duration_seconds)
            kept_slides = script.slides[:keep_count]

            segment_paths: list[Path] = []
            for i, slide in enumerate(kept_slides):
                image_path = work_dir / f"slide_{i}.png"
                segment_path = work_dir / f"segment_{i}.mp4"
                _draw_slide(slide.heading, slide.bullets, topic.accent, image_path)
                _mux_segment(image_path, audio_paths[i], segment_path)
                segment_paths.append(segment_path)

            concat_path = work_dir / "concat.mp4"
            if len(segment_paths) == 1:
                shutil.copy(segment_paths[0], concat_path)
            else:
                _concat_segments(segment_paths, concat_path, work_dir)

            final_path = settings.artifacts_path / f"{topic.id}_{uuid.uuid4().hex[:8]}.mp4"
            self._enforce_max_duration(concat_path, final_path)

            size_bytes = final_path.stat().st_size
            duration = self._probe_duration(final_path)
            # Deliberately not script.narration_text: reflects exactly the
            # kept slides, so the mechanical content-accuracy check that
            # runs on this file (app/qc/validator.py) validates what this
            # file actually says, same reasoning as the Veo provider.
            kept_narration_text = " ".join(s.narration for s in kept_slides)
            return RenderResult(
                path=final_path,
                duration_seconds=duration,
                size_bytes=size_bytes,
                provider=self.name,
                narration_text=kept_narration_text,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _enforce_max_duration(concat_path: Path, final_path: Path) -> None:
        duration = LocalProvider._probe_duration(concat_path)
        if duration > settings.local_max_duration_seconds:
            # Last-resort safety net -- only reachable if a single kept
            # slide's own narration alone exceeds the budget, since
            # select_slides_within_budget already stopped adding further
            # slides once the running total would exceed it.
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(concat_path), "-t", str(settings.local_max_duration_seconds), "-c", "copy", str(final_path)],
                capture_output=True, timeout=120, check=True,
            )
        else:
            shutil.move(str(concat_path), str(final_path))

    @staticmethod
    def _probe_duration(path: Path) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
