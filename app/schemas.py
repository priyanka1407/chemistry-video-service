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

    PENDING -> QUEUED -> PROCESSING -> SUCCESS         (matched a topic; video served, cached or freshly generated)
                                    -> REJECTED         (did not match any supported topic, or a guardrail/rate limit rejected it)
                                    -> HOLD_FOR_REVIEW  (quality gates close to threshold -- needs a human look, see /jobs/{id}/report)
                                    -> FAILED_VERIFICATION (quality gates failed after every regeneration attempt)
                                    -> FAILED_RENDER    (rendering failed after every retry)
                                    -> FAILED_PERMANENT (retries exhausted -- see the dead-letter table)
                                    -> FAILED           (generic unrecoverable error, kept for backward compatibility)

    Terminal states: everything except PENDING/QUEUED/PROCESSING. `stage`
    narrates progress *within* PROCESSING (Retrieving, Scripting, Verifying,
    Rendering, Checking, Completed, ...) and `progress` (0-100) is the
    numeric counterpart a client renders as a progress bar.
    """

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    FAILED_RENDER = "FAILED_RENDER"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self not in {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PROCESSING}


class VideoRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="The learner's chemistry question")
    audience_age: int = Field(15, ge=5, le=99, description="Target learner age, used by the teaching-quality judge")


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    is_seed: bool
    topic_id: Optional[str] = None
    matched_question: Optional[str] = None
    status: JobStatus
    stage: str
    progress: int = 0
    similarity_score: Optional[float] = None
    match_method: Optional[str] = None
    provider: Optional[str] = None
    llm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    cost_usd: float = 0.0
    video_location: Optional[str] = None
    duration_seconds: Optional[float] = None
    size_bytes: Optional[int] = None
    local_video_available: bool = False
    veo_video_available: bool = False
    source_chunk_ids: Optional[list[str]] = None
    faithfulness_score: Optional[float] = None
    teaching_quality_score: Optional[float] = None
    veo_faithfulness_score: Optional[float] = None
    gate_decision: Optional[str] = None
    regeneration_attempts: int = 0
    validation_passed: Optional[bool] = None
    validation_details: Optional[dict[str, Any]] = None
    cache_hit: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @classmethod
    def from_job(cls, job: Any) -> "JobOut":
        out = cls.model_validate(job)
        out.local_video_available = bool(job.local_video_location)
        out.veo_video_available = bool(job.veo_video_location)
        return out


class QualityReportOut(BaseModel):
    job_id: str
    faithfulness_score: Optional[float] = None
    contradicted_claims: list[dict[str, Any]] = Field(default_factory=list)
    teaching_scores: dict[str, Any] = Field(default_factory=dict)
    teaching_aggregate: Optional[float] = None
    output_checks: dict[str, str] = Field(default_factory=dict)
    gate_decision: Optional[str] = None
    decision_reason: Optional[str] = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    # Per-video judge scorecard -- {"local": {...}, "veo": {...}}, each with
    # faithfulness_pct, quality_label, and score_out_of_10: the table a
    # human can use to judge either delivered video at a glance.
    video_scores: dict[str, Any] = Field(default_factory=dict)


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
