"""Text-to-speech with a fallback chain: gTTS (network) -> pyttsx3 (offline)
-> silent (last resort, so a network blip can never fully block a video).

Every caller gets back which method actually produced the audio, and a
duration in seconds so the video track can be sized to match.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def _synthesize_gtts(text: str, out_path: Path) -> None:
    from gtts import gTTS

    tts = gTTS(text=text, lang=settings.tts_lang, tld=settings.tts_tld)
    tts.save(str(out_path))


def _synthesize_pyttsx3(text: str, out_path: Path) -> None:
    import pyttsx3

    engine = pyttsx3.init()
    wav_path = out_path.with_suffix(".wav")
    engine.save_to_file(text, str(wav_path))
    engine.runAndWait()
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), str(out_path)],
        capture_output=True, timeout=60, check=True,
    )
    wav_path.unlink(missing_ok=True)


def _synthesize_silent(out_path: Path, seconds: float = 4.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(seconds), str(out_path),
        ],
        capture_output=True, timeout=30, check=True,
    )


def synthesize(text: str, out_path: Path) -> tuple[float, str]:
    """Returns (duration_seconds, method_used)."""
    provider = settings.tts_provider.lower()
    order = {
        "gtts": [_try_gtts, _try_pyttsx3, _try_silent],
        "pyttsx3": [_try_pyttsx3, _try_silent],
        "silent": [_try_silent],
    }.get(provider, [_try_gtts, _try_pyttsx3, _try_silent])

    for attempt in order:
        method = attempt(text, out_path)
        if method:
            return _probe_duration(out_path), method

    raise RuntimeError("All TTS methods, including silent fallback, failed.")


def _try_gtts(text: str, out_path: Path) -> str | None:
    try:
        _synthesize_gtts(text, out_path)
        return "gtts"
    except Exception:  # noqa: BLE001
        log.warning("gTTS synthesis failed, falling back.", exc_info=True)
        return None


def _try_pyttsx3(text: str, out_path: Path) -> str | None:
    try:
        _synthesize_pyttsx3(text, out_path)
        return "pyttsx3"
    except Exception:  # noqa: BLE001
        log.warning("pyttsx3 synthesis failed, falling back.", exc_info=True)
        return None


def _try_silent(text: str, out_path: Path) -> str | None:
    try:
        word_count = max(len(text.split()), 1)
        seconds = max(word_count / 2.3, 2.0)  # ~140 wpm speaking rate estimate
        _synthesize_silent(out_path, seconds=seconds)
        log.warning("Falling back to silent audio (no narration audible) for a slide.")
        return "silent"
    except Exception:  # noqa: BLE001
        log.error("Silent audio fallback failed -- no TTS method available.", exc_info=True)
        return None
