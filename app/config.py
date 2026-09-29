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

    # --- OpenAI LLM-as-judge ---------------------------------------------
    # Independent of LLM_PROVIDER above: script *generation* can run on
    # Gemini/OpenAI/Anthropic, but *judging* (grounding/faithfulness,
    # teaching-quality rubric, final output review) always goes through
    # OpenAI directly, per the spec's requirement for a dedicated judge model.
    openai_judge_model: str = "gpt-4o-mini"
    openai_judge_timeout_seconds: int = 60
    openai_judge_max_retries: int = 3

    # --- Video provider --------------------------------------------------
    video_provider: str = "local"  # local | veo -- which provider a single-provider run uses
    enable_dual_video_generation: bool = True  # render local AND veo; both stored + gated in the DB
    default_video_delivery_provider: str = "local"  # local | veo -- which variant GET /jobs/{id}/video streams by default
    fallback_to_local: bool = True

    veo_model: str = "veo-3.1-fast-generate-preview"
    veo_aspect_ratio: str = "16:9"
    veo_duration_seconds: int = 8  # per-segment length requested from the Veo API
    veo_min_duration_seconds: int = 16  # final delivered clip is padded/looped up to at least this
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
    max_task_retries: int = 3  # Celery task-level retries on transient failure (API timeout, rate limit)
    task_retry_backoff_seconds: int = 5  # exponential: 5s, 10s, 20s

    # --- Retrieval / grounding / teaching-quality gates --------------------
    source_material_dir: str = "./source_material"
    source_material_filename: str = "chemistry_source.pdf"
    retrieval_top_k: int = 4
    faithfulness_threshold: float = 0.8  # supported / total_factual_claims, below this -> regenerate
    teaching_quality_threshold: float = 3.5  # weighted 1-5 rubric aggregate
    max_regeneration_attempts: int = 2  # after a failed gate, feed corrections back and retry this many times
    review_hold_margin: float = 0.1  # within this fraction of a threshold -> HOLD_FOR_REVIEW instead of REJECT

    # --- Async job pipeline (Celery) ---------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_task_always_eager: bool = False  # tests force this True so no broker/worker is needed

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
    cost_openai_judge_input_per_1m_tokens: float = 0.15
    cost_openai_judge_output_per_1m_tokens: float = 0.60

    # --- Runtime ---------------------------------------------------------
    log_level: str = "INFO"
    log_dir: str = "./logs"
    log_max_bytes: int = 5_000_000  # rotate each component's log file at ~5MB
    log_backup_count: int = 3

    @property
    def artifacts_path(self) -> Path:
        path = Path(self.artifacts_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logs_path(self) -> Path:
        path = Path(self.log_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def source_material_path(self) -> Path:
        path = Path(self.source_material_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path / self.source_material_filename


settings = Settings()
