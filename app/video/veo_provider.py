"""Direct Google Veo calls -- the one part of the codebase that is
provider-specific by necessity, since LangChain has no video-generation
abstraction. Called through app/video/factory.py exactly like the local
provider, so app/pipeline.py never knows which one ran.

Veo clips are short (VEO_DURATION_SECONDS, typically 8s) and silent; this
provider overlays the same TTS narration the local provider uses so every
video, regardless of provider, satisfies the same QC audio-track check.
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
from app.topics import Topic
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
        from google.genai import types

        client = genai.Client(api_key=settings.google_api_key)
        prompt = topic.veo_prompt_hint or f"Educational chemistry animation: {topic.question}"

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

        work_dir = settings.artifacts_path / f"_work_{job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw_path = work_dir / "veo_raw.mp4"
            video_file = generated[0].video
            client.files.download(file=video_file)
            video_file.save(str(raw_path))

            final_path = settings.artifacts_path / f"{topic.id}_{uuid.uuid4().hex[:8]}.mp4"

            if settings.veo_add_tts_audio_overlay:
                audio_path = work_dir / "narration.mp3"
                tts.synthesize(script.narration_text, audio_path)
                self._overlay_audio(raw_path, audio_path, final_path)
            else:
                shutil.copy(raw_path, final_path)

            size_bytes = final_path.stat().st_size
            duration = self._probe_duration(final_path)
            return RenderResult(
                path=final_path,
                duration_seconds=duration,
                size_bytes=size_bytes,
                provider=self.name,
                narration_text=script.narration_text,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _overlay_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
        # Loop the (short, silent) Veo clip to cover the full narration, then
        # cut to the narration's length -- narration length drives the
        # output, same convention as the local provider.
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
    def _probe_duration(path: Path) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
