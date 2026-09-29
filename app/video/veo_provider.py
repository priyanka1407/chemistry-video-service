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
narration, then concatenated.

A script's full narration is usually longer than VEO_MAX_DURATION_SECONDS,
so not every slide can fit -- but which ones are dropped is decided BEFORE
generating a slide's Veo clip (each slide's own narration is measured via
TTS, which is free, first), and only WHOLE slides are ever dropped, never a
mid-sentence cut of one that's kept. The returned RenderResult's
narration_text reflects exactly the kept slides' narration, not the full
script, so the mechanical content-accuracy check that runs on this file
(app/qc/validator.py) validates what this file actually says -- a capped
Veo video is an honest shorter highlight of the lesson, not a truncated,
sentence-cut copy of the full one (that's still the local/gTTS variant).
The concatenated result is then looped up to VEO_MIN_DURATION_SECONDS if
even the kept slides come in short, or hard-trimmed to
VEO_MAX_DURATION_SECONDS as a last-resort safety net (only reachable if a
single slide's own narration alone exceeds the cap).

Cost is bounded the same way: Veo is only ever billed for
`len(kept slides) * VEO_DURATION_SECONDS` seconds of raw generation, capped
at VEO_MAX_SEGMENTS -- fixed and independent of how long the full script is.

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


def select_slides_within_budget(durations: list[float], max_duration: float) -> int:
    """How many leading slides (in order) fit within `max_duration`, given
    each candidate slide's own narration duration. Always keeps at least
    one slide, even if it alone exceeds the budget (a single over-long
    slide is a rare edge case handled by _enforce_duration_bounds's
    last-resort trim, not by dropping every slide and delivering nothing).
    Pure function, independent of ffmpeg/TTS/Veo, so the actual selection
    decision is unit-testable on its own -- see tests/test_veo_provider.py.
    """
    if not durations:
        return 0
    total = durations[0]
    count = 1
    for d in durations[1:]:
        if total + d > max_duration:
            break
        total += d
        count += 1
    return count


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
            candidate_slides = script.slides[: settings.veo_max_segments]

            # Synthesize every candidate's narration first (TTS is free/cheap)
            # so which slides fit the duration budget is decided BEFORE
            # spending a single Veo API call -- a dropped slide never costs
            # a generation, and the ones kept are never cut mid-sentence:
            # the cap is enforced by which WHOLE segments get included.
            audio_paths: list[Path] = []
            durations: list[float] = []
            for i, slide in enumerate(candidate_slides):
                audio_path = work_dir / f"narration_{i}.mp3"
                tts.synthesize(slide.narration, audio_path)
                audio_paths.append(audio_path)
                durations.append(self._probe_duration(audio_path))

            keep_count = select_slides_within_budget(durations, settings.veo_max_duration_seconds)
            kept_slides = candidate_slides[:keep_count]

            segment_paths: list[Path] = []
            for i, slide in enumerate(kept_slides):
                raw_path = work_dir / f"veo_raw_{i}.mp4"
                self._generate_raw_clip(client, self._segment_prompt(topic, slide), raw_path)

                segment_path = work_dir / f"segment_{i}.mp4"
                if settings.veo_add_tts_audio_overlay:
                    self._mux_segment(raw_path, audio_paths[i], segment_path)
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
            billed_seconds = len(kept_slides) * settings.veo_duration_seconds
            # Deliberately NOT script.narration_text: this file only ever
            # speaks the kept slides' narration, so its own content-accuracy
            # check (app/qc/validator.py, called with THIS narration_text)
            # reflects what this specific file actually says -- a script
            # longer than the duration cap means the Veo variant is a
            # shorter highlight of the full (fully-narrated, in the local
            # variant) lesson, not a truncated copy of it.
            kept_narration_text = " ".join(s.narration for s in kept_slides)
            return RenderResult(
                path=final_path,
                duration_seconds=duration,
                size_bytes=size_bytes,
                provider=self.name,
                narration_text=kept_narration_text,
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
