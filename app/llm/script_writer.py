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


_PROMPT_TEMPLATE = """You are a chemistry teacher writing a short (30-60 second) educational \
video script for the question: "{question}"

Write {min_slides} to {max_slides} slides. Each slide needs a short heading, 2-3 short bullet \
points, and 1-3 sentences of spoken narration that a text-to-speech engine will read aloud.

You MUST base every factual statement ONLY on the source material below -- do not add facts, \
numbers, or claims that are not stated in it. If the source material doesn't cover something, \
leave it out rather than inventing it.

Source material (id: text):
{source_material}

Requirements:
- Be scientifically accurate to the source material above, and age-appropriate for audience age {audience_age}.
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


def _validate(script: GeneratedScript, topic: Topic) -> str | None:
    if not (2 <= len(script.slides) <= 4):
        return f"expected 2-4 slides, got {len(script.slides)}"
    narration = " ".join(s.narration for s in script.slides).lower()
    missing = [term for term in topic.must_mention if term.lower() not in narration]
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
    """
    chunks = tuple(all_chunks_for_topic(topic.id))
    chunk_ids = tuple(c.id for c in chunks)

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
        must_mention=", ".join(topic.must_mention),
        correction_block=correction_block,
    )

    for attempt in range(1, settings.max_script_attempts + 1):
        try:
            result: GeneratedScript = structured_chat.invoke(prompt)  # type: ignore[assignment]
            error = _validate(result, topic)
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
