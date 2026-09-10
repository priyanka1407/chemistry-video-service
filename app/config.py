"""Every configurable knob in the service, read once from the environment.

Nothing outside this module should call `os.getenv` directly -- if a value
needs to change between environments, it belongs here and in `.env.example`.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Persistence ---------------------------------------------------
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    db_allow_sqlite_fallback: bool = True
    db_fallback_sqlite_path: str = "./jobs.db"

    # --- LLM provider (LangChain) --------------------------------------
    llm_provider: str = "google"  # google | openai | anthropic
    google_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    embedding_model: str = "gemini-embedding-001"
    script_model: str = "gemini-2.5-flash"
    guardrail_use_llm: bool = False
    guardrail_model: str = "gemini-2.5-flash"

    # --- Video provider --------------------------------------------------
    video_provider: str = "local"  # local | veo
    fallback_to_local: bool = True

    veo_model: str = "veo-3.1-fast-generate-preview"
    veo_aspect_ratio: str = "16:9"
    veo_duration_seconds: int = 8
    veo_poll_interval_seconds: int = 10
    veo_timeout_seconds: int = 600
    veo_add_tts_audio_overlay: bool = True

    # --- Local renderer ---------------------------------------------------
    tts_provider: str = "gtts"  # gtts | pyttsx3 | silent
    tts_lang: str = "en"
    tts_tld: str = "com"
    video_width: int = 1280
    video_height: int = 720
    video_fps: int = 24
    artifacts_dir: str = "./artifacts"

    # --- Reliability -------------------------------------------------------
    max_script_attempts: int = 3
    max_render_attempts: int = 2

    # --- Semantic gate --------------------------------------------------
    semantic_similarity_threshold: float = 0.80
    lexical_similarity_threshold: float = 0.35

    # --- Guardrails -------------------------------------------------------
    min_query_length: int = 3
    max_query_length: int = 500
    rate_limit_per_minute: int = 30

    # --- Cost model (USD) ---------------------------------------------------
    cost_embedding_per_1k_tokens: float = 0.00015
    cost_script_input_per_1m_tokens: float = 0.30
    cost_script_output_per_1m_tokens: float = 2.50
    cost_veo_per_second: float = 0.15
    cost_tts_per_1k_chars: float = 0.0

    # --- Runtime ---------------------------------------------------------
    warmup_on_startup: bool = True
    strict_startup: bool = False
    log_level: str = "INFO"

    @property
    def artifacts_path(self) -> Path:
        path = Path(self.artifacts_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
