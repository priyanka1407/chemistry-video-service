"""Turns a Topic into a slide-by-slide narration script via the LangChain
chat model, with retries and a guaranteed-good fallback.

This only ever runs for the 3 supported topics, generated on demand the
first time each is requested (never at startup) -- arbitrary user queries
never reach the LLM chat model, only the embeddings call in
semantic_gate.py, which keeps per-request cost and attack surface minimal.

Retrieve-then-generate: the script is written ONLY from chunks retrieved out
of source_material/chemistry_source.pdf (app/rag), with their chunk ids
threaded through so the exact source snapshot a script was written from is
reproducible and auditable later (app.judge.grounding checks claims back
against these same chunks).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.config import settings
from app.llm.factory import ProviderNotConfigured, get_chat_model
from app.rag.loader import Chunk
from app.rag.store import all_chunks_for_topic
from app.topics import Slide, Topic

log = logging.getLogger(__name__)

_WORDS_PER_SECOND = 2.3  # conservative spoken-word rate, leaves buffer for TTS timing variance


class GeneratedSlide(BaseModel):
    heading: str = Field(description="Short slide title, 2-6 words")
    bullets: list[str] = Field(description="2-3 short bullet points, each under 10 words")
    narration: str = Field(description="1-3 sentences of spoken narration for this slide, natural spoken English")


class GeneratedScript(BaseModel):
    title: str = Field(description="Short video title")
    slides: list[GeneratedSlide] = Field(description="2 to 4 slides covering the concept")


@dataclass
class Script:
    title: str
    slides: tuple[Slide, ...]
    source: str  # "llm" | "curated"
    model: str | None
    source_chunk_ids: tuple[str, ...] = ()

    @property
    def narration_text(self) -> str:
        return " ".join(s.narration for s in self.slides)


_PROMPT_TEMPLATE = """You are a chemistry teacher writing a short (under {target_seconds} seconds) \
educational video script for the question: "{question}"

Write {min_slides} to {max_slides} slides. Each slide needs a short heading, 2-3 short bullet \
points, and 1-2 short sentences of spoken narration that a text-to-speech engine will read aloud.

You MUST base every factual statement ONLY on the source material below -- do not add facts, \
numbers, or claims that are not stated in it. If the source material doesn't cover something, \
leave it out rather than inventing it.

Source material (id: text):
{source_material}

Requirements:
- Be scientifically accurate to the source material above, and age-appropriate for audience age {audience_age}.
- The TOTAL narration across ALL slides, combined, must be at most {max_words} words -- it will be \
spoken aloud in under {target_seconds} seconds, so be concise: cover the core idea well rather than \
everything possible.
- The narration across all slides, combined, MUST naturally use these terms at least once each: \
{must_mention}.
- Keep narration conversational and clear -- it will be spoken aloud, not read.
- Do not mention that you are an AI, that this is generated, or that there is "source material".
{correction_block}"""

_CORRECTION_TEMPLATE = """
A previous attempt at this script failed verification. Fix these specific problems in this \
attempt:
{issues}
"""


def _format_source_material(chunks: tuple[Chunk, ...]) -> str:
    return "\n".join(f"[{c.id}] {c.text}" for c in chunks)


def _validate(script: GeneratedScript, topic: Topic, max_words: int) -> str | None:
    if not (2 <= len(script.slides) <= 4):
        return f"expected 2-4 slides, got {len(script.slides)}"
    narration = " ".join(s.narration for s in script.slides)
    word_count = len(narration.split())
    if word_count > max_words:
        return f"narration is {word_count} words, over the {max_words}-word budget"
    missing = [term for term in topic.must_mention if term.lower() not in narration.lower()]
    if missing:
        return f"narration is missing required terms: {missing}"
    for slide in script.slides:
        if not slide.heading.strip() or not slide.narration.strip():
            return "a slide had an empty heading or narration"
    return None


def _curated_fallback(topic: Topic, source_chunk_ids: tuple[str, ...]) -> Script:
    return Script(
        title=topic.question, slides=topic.curated_script, source="curated", model=None,
        source_chunk_ids=source_chunk_ids,
    )


def generate_script(topic: Topic, *, audience_age: int = 15, correction_issues: list[str] | None = None) -> Script:
    """Retrieve-then-generate: pull every indexed chunk for this topic out of
    the source PDF and hand the writer ONLY that text -- see app/rag. The
    small per-topic corpus (a handful of chunks) means the writer sees the
    whole section rather than a top-k slice, so nothing it could faithfully
    say is withheld from it.

    `correction_issues`, when set, feeds specific problems from a failed
    grounding/teaching-quality gate back in as explicit correction context
    for a regeneration attempt (BUILD_SPEC Part 3, "regenerate... feeding the
    failed claims back as explicit correction context").

    Word-budgeted to fit LOCAL_MAX_DURATION_SECONDS from the moment it's
    written -- not written long and trimmed after rendering. See
    app/video/local_provider.py, which still applies a whole-slide-selection
    safety net on top of this, same reasoning as the Veo highlight script.
    """
    chunks = tuple(all_chunks_for_topic(topic.id))
    chunk_ids = tuple(c.id for c in chunks)
    max_words = max(int(settings.local_max_duration_seconds * _WORDS_PER_SECOND), 20)

    try:
        chat = get_chat_model()
    except ProviderNotConfigured as exc:
        log.warning("Chat model unavailable (%s) for topic %s -- using curated script.", exc, topic.id)
        return _curated_fallback(topic, chunk_ids)

    structured_chat = chat.with_structured_output(GeneratedScript)
    correction_block = _CORRECTION_TEMPLATE.format(issues="\n".join(f"- {i}" for i in correction_issues)) if correction_issues else ""
    prompt = _PROMPT_TEMPLATE.format(
        question=topic.question,
        min_slides=2,
        max_slides=4,
        source_material=_format_source_material(chunks),
        audience_age=audience_age,
        max_words=max_words,
        target_seconds=settings.local_max_duration_seconds,
        must_mention=", ".join(topic.must_mention),
        correction_block=correction_block,
    )

    for attempt in range(1, settings.max_script_attempts + 1):
        try:
            result: GeneratedScript = structured_chat.invoke(prompt)  # type: ignore[assignment]
            error = _validate(result, topic, max_words)
            if error is None:
                slides = tuple(
                    Slide(heading=s.heading, bullets=tuple(s.bullets), narration=s.narration)
                    for s in result.slides
                )
                return Script(
                    title=result.title, slides=slides, source="llm", model=settings.script_model,
                    source_chunk_ids=chunk_ids,
                )
            log.warning(
                "Script attempt %d/%d for topic %s failed validation: %s",
                attempt, settings.max_script_attempts, topic.id, error,
            )
        except Exception:  # noqa: BLE001 - any provider/parsing failure, just retry
            log.exception("Script attempt %d/%d for topic %s raised an error.", attempt, settings.max_script_attempts, topic.id)

    log.error("All %d script attempts failed for topic %s -- using curated fallback.", settings.max_script_attempts, topic.id)
    return _curated_fallback(topic, chunk_ids)


# ---------------------------------------------------------------------------
# Highlight script: a separate, deliberately SHORT script for the Veo video,
# sized to fit a target duration from the moment it's written -- not the
# full lesson script cut down after the fact. See app/video/veo_provider.py
# for why: a video built from oversized narration and then trimmed can cut
# audio mid-sentence and misrepresent what the file actually says.
# ---------------------------------------------------------------------------

_HIGHLIGHT_PROMPT_TEMPLATE = """You are writing a VERY SHORT highlight narration -- a {target_seconds}-second \
teaser, NOT the full lesson -- for a short-form video answering: "{question}"

Write 1 to {max_slides} short slides. Each slide needs a short heading, 1 short bullet point, and ONE \
short sentence of spoken narration.

You MUST base every factual statement ONLY on the source material below -- do not add facts, \
numbers, or claims that are not stated in it.

Source material (id: text):
{source_material}

Hard requirements:
- The TOTAL narration across ALL slides, combined, must be at most {max_words} words. It will be \
spoken aloud in under {target_seconds} seconds, so brevity is mandatory -- prefer fewer, punchier \
slides over more, wordy ones.
- The narration across all slides, combined, MUST naturally use these terms at least once each: \
{must_mention}.
- Do not mention that you are an AI, that this is generated, or that there is "source material".
{correction_block}"""


def _validate_highlight(script: GeneratedScript, topic: Topic, max_words: int) -> str | None:
    if not (1 <= len(script.slides) <= settings.veo_max_segments):
        return f"expected 1-{settings.veo_max_segments} slides, got {len(script.slides)}"
    narration = " ".join(s.narration for s in script.slides)
    word_count = len(narration.split())
    if word_count > max_words:
        return f"narration is {word_count} words, over the {max_words}-word budget"
    missing = [term for term in topic.must_mention if term.lower() not in narration.lower()]
    if missing:
        return f"narration is missing required terms: {missing}"
    for slide in script.slides:
        if not slide.heading.strip() or not slide.narration.strip():
            return "a slide had an empty heading or narration"
    return None


def _curated_highlight_fallback(topic: Topic, source_chunk_ids: tuple[str, ...]) -> Script:
    # The curated script's first slide alone: guaranteed grounded (it's
    # hand-written against the same source material) and guaranteed short,
    # used only if the LLM can't produce a valid highlight after every retry.
    return Script(
        title=topic.question, slides=topic.curated_script[:1], source="curated", model=None,
        source_chunk_ids=source_chunk_ids,
    )


def generate_highlight_script(
    topic: Topic, *, audience_age: int = 15, target_seconds: int | None = None, correction_issues: list[str] | None = None,
) -> Script:
    """Retrieve-then-generate a short highlight script, word-budgeted to fit
    `target_seconds` (default VEO_MAX_DURATION_SECONDS) of spoken narration --
    for the Veo video, which is a short companion clip, not a second
    full-length rendering of the lesson (that's the local/gTTS video)."""
    target = target_seconds if target_seconds is not None else settings.veo_max_duration_seconds
    max_words = max(int(target * _WORDS_PER_SECOND), 12)

    chunks = tuple(all_chunks_for_topic(topic.id))
    chunk_ids = tuple(c.id for c in chunks)

    try:
        chat = get_chat_model()
    except ProviderNotConfigured as exc:
        log.warning("Chat model unavailable (%s) for highlight script, topic %s -- using curated fallback.", exc, topic.id)
        return _curated_highlight_fallback(topic, chunk_ids)

    structured_chat = chat.with_structured_output(GeneratedScript)
    correction_block = _CORRECTION_TEMPLATE.format(issues="\n".join(f"- {i}" for i in correction_issues)) if correction_issues else ""
    prompt = _HIGHLIGHT_PROMPT_TEMPLATE.format(
        question=topic.question,
        target_seconds=target,
        max_slides=settings.veo_max_segments,
        source_material=_format_source_material(chunks),
        max_words=max_words,
        must_mention=", ".join(topic.must_mention),
        correction_block=correction_block,
    )

    for attempt in range(1, settings.max_script_attempts + 1):
        try:
            result: GeneratedScript = structured_chat.invoke(prompt)  # type: ignore[assignment]
            error = _validate_highlight(result, topic, max_words)
            if error is None:
                slides = tuple(
                    Slide(heading=s.heading, bullets=tuple(s.bullets), narration=s.narration)
                    for s in result.slides
                )
                return Script(
                    title=result.title, slides=slides, source="llm", model=settings.script_model,
                    source_chunk_ids=chunk_ids,
                )
            log.warning(
                "Highlight script attempt %d/%d for topic %s failed validation: %s",
                attempt, settings.max_script_attempts, topic.id, error,
            )
        except Exception:  # noqa: BLE001
            log.exception("Highlight script attempt %d/%d for topic %s raised an error.", attempt, settings.max_script_attempts, topic.id)

    log.error("All %d highlight script attempts failed for topic %s -- using curated fallback.", settings.max_script_attempts, topic.id)
    return _curated_highlight_fallback(topic, chunk_ids)
