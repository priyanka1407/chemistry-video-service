"""Unit tests for the main script's and the Veo highlight script's
word-budget validation and curated fallbacks (app/llm/script_writer.py) --
the part that guarantees BOTH scripts are sized to fit their respective
duration caps from the moment they're written, never generated full-length
and trimmed after the fact."""
from __future__ import annotations

from app.llm.script_writer import (
    GeneratedScript,
    GeneratedSlide,
    _validate,
    _validate_highlight,
    generate_highlight_script,
)
from app.topics import get_topic


def _slide(narration: str, heading: str = "Heading") -> GeneratedSlide:
    return GeneratedSlide(heading=heading, bullets=["A point"], narration=narration)


def test_validate_rejects_over_budget_narration():
    topic = get_topic("ph_scale")
    script = GeneratedScript(title="t", slides=[
        _slide("word " * 80 + "ph acid hydrogen"), _slide("more words here today"),
    ])
    error = _validate(script, topic, max_words=30)
    assert error is not None
    assert "word" in error.lower()


def test_validate_accepts_a_script_within_budget():
    topic = get_topic("ph_scale")
    script = GeneratedScript(title="t", slides=[
        _slide("The ph scale measures acid strength using hydrogen ions."),
        _slide("Below 7 is acidic and above 7 is basic."),
    ])
    assert _validate(script, topic, max_words=69) is None


def test_validate_highlight_rejects_over_budget_narration():
    topic = get_topic("ph_scale")
    script = GeneratedScript(title="t", slides=[_slide("word " * 60 + "ph acid hydrogen")])
    error = _validate_highlight(script, topic, max_words=20)
    assert error is not None
    assert "word" in error.lower()


def test_validate_highlight_rejects_missing_must_mention_terms():
    topic = get_topic("ph_scale")
    script = GeneratedScript(title="t", slides=[_slide("This is short and on topic somewhat.")])
    error = _validate_highlight(script, topic, max_words=50)
    assert error is not None
    assert "missing required terms" in error


def test_validate_highlight_accepts_a_short_grounded_slide():
    topic = get_topic("ph_scale")
    script = GeneratedScript(title="t", slides=[_slide("The ph scale measures acid strength using hydrogen ions.")])
    assert _validate_highlight(script, topic, max_words=50) is None


def test_validate_highlight_rejects_too_many_slides():
    topic = get_topic("ph_scale")
    script = GeneratedScript(title="t", slides=[_slide("ph acid hydrogen short.") for _ in range(5)])
    error = _validate_highlight(script, topic, max_words=200)
    assert error is not None
    assert "slides" in error


def test_generate_highlight_script_falls_back_to_first_curated_slide_without_a_chat_model(monkeypatch):
    from app.llm.factory import ProviderNotConfigured

    def _raise():
        raise ProviderNotConfigured("no key")

    monkeypatch.setattr("app.llm.script_writer.get_chat_model", _raise)

    topic = get_topic("ph_scale")
    script = generate_highlight_script(topic)
    assert script.source == "curated"
    assert len(script.slides) == 1
    assert script.slides[0] == topic.curated_script[0]
