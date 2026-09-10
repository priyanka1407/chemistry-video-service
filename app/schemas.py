"""API request/response models and the job status enum.

This is the contract boundary between the HTTP layer and everything else --
`app/db/models.py` is the persistence-side mirror of `JobStatus`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    """Job lifecycle.

    PENDING -> PROCESSING -> SUCCESS   (matched a topic; video served, cached or freshly rendered)
                          -> REJECTED  (did not match any supported topic; out of scope)
                          -> FAILED    (unrecoverable error after all retries/fallbacks)

    Terminal states: SUCCESS, REJECTED, FAILED. `stage` narrates progress
    *within* PROCESSING (Started, Embedding, Matching, Scripting, Rendering,
    Validating, Completed, ...).
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCESS, JobStatus.REJECTED, JobStatus.FAILED}


class VideoRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="The learner's chemistry question")


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    is_seed: bool
    topic_id: Optional[str] = None
    matched_question: Optional[str] = None
    status: JobStatus
    stage: str
    similarity_score: Optional[float] = None
    match_method: Optional[str] = None
    provider: Optional[str] = None
    llm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    cost_usd: float = 0.0
    video_location: Optional[str] = None
    duration_seconds: Optional[float] = None
    size_bytes: Optional[int] = None
    validation_passed: Optional[bool] = None
    validation_details: Optional[dict[str, Any]] = None
    cache_hit: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class TopicOut(BaseModel):
    id: str
    question: str
    example_phrasings: list[str]


class HealthOut(BaseModel):
    status: str
    database: str
    llm_provider: str
    video_provider: str
    match_method_default: str
    topics_ready: list[str]
    topics_degraded: list[str]
    ffmpeg_available: bool


class ErrorOut(BaseModel):
    detail: str
    code: Optional[str] = None
    context: dict[str, Any] = Field(default_factory=dict)
