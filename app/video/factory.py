"""Picks the active VideoProvider by VIDEO_PROVIDER, and is the one place
that knows about the veo -> local fallback."""
from __future__ import annotations

import logging

from app.config import settings
from app.llm.script_writer import Script
from app.topics import Topic
from app.video.base import RenderResult, VideoProvider
from app.video.local_provider import LocalProvider

log = logging.getLogger(__name__)


def get_provider(name: str | None = None) -> VideoProvider:
    provider_name = (name or settings.video_provider).lower()
    if provider_name == "local":
        return LocalProvider()
    if provider_name == "veo":
        from app.video.veo_provider import VeoProvider

        return VeoProvider()
    raise ValueError(f"Unknown VIDEO_PROVIDER: {provider_name!r}")


def render_with_fallback(topic: Topic, script: Script, job_id: str) -> RenderResult:
    """Render using the configured provider; if it's veo and it fails,
    optionally fall back to the local renderer rather than failing the job
    outright (FALLBACK_TO_LOCAL)."""
    provider = get_provider()
    try:
        return provider.render(topic, script, job_id)
    except Exception:
        if provider.name == "veo" and settings.fallback_to_local:
            log.exception("Veo rendering failed for topic %s -- falling back to the local provider.", topic.id)
            return LocalProvider().render(topic, script, job_id)
        raise


def render_both(topic: Topic, script: Script, job_id: str) -> tuple[RenderResult, RenderResult | None]:
    """Render with BOTH providers so a topic's cached master row carries a
    gTTS/local video and a Veo video, each with its own metrics, per the
    dual-generation requirement. The local render is required (cheap,
    deterministic, no external API); a Veo failure is recorded and returned
    as None rather than blocking delivery of the local video -- "no check
    may be skipped silently" applies here too, so the caller must record why
    the veo variant is missing, not just omit it."""
    local_result = LocalProvider().render(topic, script, f"{job_id}_local")

    veo_result: RenderResult | None = None
    try:
        from app.video.veo_provider import VeoProvider

        veo_result = VeoProvider().render(topic, script, f"{job_id}_veo")
    except Exception:
        log.exception("Veo rendering failed for topic %s while generating the dual-provider master.", topic.id)

    return local_result, veo_result
