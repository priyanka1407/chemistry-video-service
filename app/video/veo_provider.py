"""Direct Google Veo calls -- the one part of the codebase that is
provider-specific by necessity, since LangChain has no video-generation
abstraction. Called through app/video/factory.py exactly like the local
provider, so app/pipeline.py never knows which one ran.

A single Veo API call only produces a short clip (VEO_DURATION_SECONDS,
typically 4-8s), so a naive "generate once, loop it for the whole narration"
approach shows the exact same few seconds on repeat for the entire video --
and bills Veo's per-second rate for that whole looped duration, so a long
script becomes an expensive video of a repeating clip.

Instead: generate one distinct clip PER SCRIPT SLIDE (capped at
VEO_MAX_SEGMENTS), each prompted with that slide's own heading/bullets so
the visual actually tracks what's being said, muxed with that slide's own
narration, then concatenated. The final concatenated clip is looped up to
VEO_MIN_DURATION_SECONDS if short, or trimmed down to VEO_MAX_DURATION_SECONDS
if long -- so both the visible repetition AND the cost are bounded
regardless of how long the underlying script narration is: Veo is only ever
billed for `min(len(slides), VEO_MAX_SEGMENTS) * VEO_DURATION_SECONDS`
seconds of raw generation, a fixed number independent of script length.

One real limitation this doesn't remove: within a single segment, the raw
clip is still looped to cover that segment's own (shorter) narration chunk,
since a single Veo call can't natively extend a clip to an arbitrary length.
Splitting into multiple segments shrinks how much any one clip has to repeat,
but doesn't eliminate repetition entirely -- doing that would require Veo's
video-extension API, which is out of scope here.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from app.config import settings
from app.llm.script_writer import Script
from app.topics import Slide, Topic
from app.video import tts
from app.video.base import RenderResult

log = logging.getLogger(__name__)


class VeoGenerationError(RuntimeError):
    pass


class VeoProvider:
    name = "veo"

    def render(self, topic: Topic, script: Script, job_id: str) -> RenderResult:
        if not settings.google_api_key:
            raise VeoGenerationError("GOOGLE_API_KEY is not set -- required for the Veo provider.")

        from google import genai

        client = genai.Client(api_key=settings.google_api_key)

        work_dir = settings.artifacts_path / f"_work_{job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            slides = script.slides[: settings.veo_max_segments]
            segment_paths: list[Path] = []
            for i, slide in enumerate(slides):
                raw_path = work_dir / f"veo_raw_{i}.mp4"
                self._generate_raw_clip(client, self._segment_prompt(topic, slide), raw_path)

                segment_path = work_dir / f"segment_{i}.mp4"
                if settings.veo_add_tts_audio_overlay:
                    audio_path = work_dir / f"narration_{i}.mp3"
                    tts.synthesize(slide.narration, audio_path)
                    self._mux_segment(raw_path, audio_path, segment_path)
                else:
                    shutil.copy(raw_path, segment_path)
                segment_paths.append(segment_path)

            concat_path = work_dir / "concat.mp4"
            if len(segment_paths) == 1:
                shutil.copy(segment_paths[0], concat_path)
            else:
                self._concat_segments(segment_paths, concat_path, work_dir)

            final_path = settings.artifacts_path / f"{topic.id}_{uuid.uuid4().hex[:8]}.mp4"
            self._enforce_duration_bounds(concat_path, final_path)

            size_bytes = final_path.stat().st_size
            duration = self._probe_duration(final_path)
            billed_seconds = len(slides) * settings.veo_duration_seconds
            return RenderResult(
                path=final_path,
                duration_seconds=duration,
                size_bytes=size_bytes,
                provider=self.name,
                narration_text=script.narration_text,
                billed_seconds=billed_seconds,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _segment_prompt(topic: Topic, slide: Slide) -> str:
        base = topic.veo_prompt_hint or f"Educational chemistry animation about: {topic.question}"
        bullets = "; ".join(slide.bullets)
        return f"{base} This specific shot should visualize: '{slide.heading}' -- {bullets}."

    def _generate_raw_clip(self, client, prompt: str, out_path: Path) -> None:
        from google.genai import types

        operation = client.models.generate_videos(
            model=settings.veo_model,
            prompt=prompt,
            config=types.GenerateVideosConfig(
                aspect_ratio=settings.veo_aspect_ratio,
                duration_seconds=settings.veo_duration_seconds,
            ),
        )

        elapsed = 0
        while not operation.done:
            if elapsed >= settings.veo_timeout_seconds:
                raise VeoGenerationError(f"Veo generation timed out after {settings.veo_timeout_seconds}s.")
            time.sleep(settings.veo_poll_interval_seconds)
            elapsed += settings.veo_poll_interval_seconds
            operation = client.operations.get(operation)

        if operation.error:
            raise VeoGenerationError(f"Veo generation failed: {operation.error}")

        generated = operation.response.generated_videos
        if not generated:
            raise VeoGenerationError("Veo returned no video in its response.")

        video_file = generated[0].video
        client.files.download(file=video_file)
        video_file.save(str(out_path))

    @staticmethod
    def _mux_segment(video_path: Path, audio_path: Path, out_path: Path) -> None:
        # Loop this segment's (short, silent) raw clip to cover this
        # segment's own narration chunk, then cut to the narration's length
        # -- narration drives segment length, same convention the local
        # provider uses, just scoped to one slide instead of the whole script.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", str(video_path),
                "-i", str(audio_path),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-c:a", "aac", "-b:a", "160k",
                "-shortest",
                str(out_path),
            ],
            capture_output=True, timeout=120, check=True,
        )

    @staticmethod
    def _concat_segments(segment_paths: list[Path], out_path: Path, work_dir: Path) -> None:
        list_file = work_dir / "concat_list.txt"
        list_file.write_text("\n".join(f"file '{p.name}'" for p in segment_paths), encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
            capture_output=True, timeout=120, check=True, cwd=str(work_dir),
        )

    @staticmethod
    def _enforce_duration_bounds(concat_path: Path, final_path: Path) -> None:
        duration = VeoProvider._probe_duration(concat_path)
        if duration < settings.veo_min_duration_seconds:
            target = settings.veo_min_duration_seconds
            subprocess.run(
                ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(concat_path), "-t", str(target), "-c", "copy", str(final_path)],
                capture_output=True, timeout=120, check=True,
            )
        elif duration > settings.veo_max_duration_seconds:
            target = settings.veo_max_duration_seconds
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(concat_path), "-t", str(target), "-c", "copy", str(final_path)],
                capture_output=True, timeout=120, check=True,
            )
        else:
            shutil.copy(concat_path, final_path)

    @staticmethod
    def _probe_duration(path: Path) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
