"""The AI/generation boundary for text and embeddings.

Every text/embedding call in the app goes through `get_embeddings()` /
`get_chat_model()`, both returning plain LangChain `Embeddings` /
`BaseChatModel` objects. Swapping providers is a `LLM_PROVIDER` env change
plus installing that provider's `langchain-<provider>` package -- nothing
else in the codebase references Gemini, OpenAI, or Anthropic by name.

Video generation (app/video/) is intentionally NOT behind this factory:
LangChain has no video-generation abstraction, so the Veo provider calls
`google-genai` directly. That's the one seam in the codebase that is
provider-specific by necessity.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings

log = logging.getLogger(__name__)


class ProviderNotConfigured(RuntimeError):
    """Raised when LLM_PROVIDER names a provider whose package/key isn't set up."""


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    provider = settings.llm_provider.lower()

    if provider == "google":
        if not settings.google_api_key:
            raise ProviderNotConfigured("GOOGLE_API_KEY is not set but LLM_PROVIDER=google")
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=settings.embedding_model, google_api_key=settings.google_api_key)

    if provider == "openai":
        if not settings.openai_api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not set but LLM_PROVIDER=openai")
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ProviderNotConfigured(
                "LLM_PROVIDER=openai requires `pip install langchain-openai`"
            ) from exc
        return OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)

    if provider == "anthropic":
        # Anthropic has no first-party embeddings model; this provider is
        # for the chat model only. Callers needing embeddings under
        # LLM_PROVIDER=anthropic should keep GOOGLE_API_KEY set as the
        # embedding source, or point EMBEDDING_MODEL at a different provider.
        raise ProviderNotConfigured(
            "Anthropic has no embeddings API. Set LLM_PROVIDER=google or openai for embeddings."
        )

    raise ProviderNotConfigured(f"Unknown LLM_PROVIDER: {provider!r}")


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    provider = settings.llm_provider.lower()

    if provider == "google":
        if not settings.google_api_key:
            raise ProviderNotConfigured("GOOGLE_API_KEY is not set but LLM_PROVIDER=google")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=settings.script_model, google_api_key=settings.google_api_key)

    if provider == "openai":
        if not settings.openai_api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not set but LLM_PROVIDER=openai")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ProviderNotConfigured(
                "LLM_PROVIDER=openai requires `pip install langchain-openai`"
            ) from exc
        return ChatOpenAI(model=settings.script_model, api_key=settings.openai_api_key)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is not set but LLM_PROVIDER=anthropic")
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ProviderNotConfigured(
                "LLM_PROVIDER=anthropic requires `pip install langchain-anthropic`"
            ) from exc
        return ChatAnthropic(model=settings.script_model, api_key=settings.anthropic_api_key)

    raise ProviderNotConfigured(f"Unknown LLM_PROVIDER: {provider!r}")
