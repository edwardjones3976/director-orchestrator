from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    REVIEWING = "REVIEWING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProviderName(StrEnum):
    CURSOR = "cursor"
    CLAUDE = "claude"
    GEMINI = "gemini"
    PERPLEXITY = "perplexity"
    OWNER = "owner"


class ReviewVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    OWNER = "owner"
    FAIL = "fail"


class SlackIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1, max_length=255)
    channel_id: str = Field(min_length=1, max_length=64)
    thread_ts: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=20_000)
    permalink: str | None = Field(default=None, max_length=2_000)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=20)
    agent: ProviderName
    task: str = Field(min_length=1, max_length=10_000)
    acceptance: list[str] = Field(min_length=1, max_length=20)
    repository: str | None = Field(default=None, max_length=2_000)
    starting_ref: str = Field(default="main", min_length=1, max_length=255)
    depends_on: list[int] = Field(default_factory=list, max_length=20)
    requires_approval: bool = False
    estimated_cost_usd: float = Field(default=0, ge=0, le=1_000)


class DirectorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1, max_length=255)
    normalized_outcome: str = Field(min_length=1, max_length=10_000)
    risk_level: RiskLevel
    approval_required: bool
    approval_reasons: list[str] = Field(default_factory=list, max_length=20)
    max_hops: int = Field(ge=1, le=20)
    max_revisions: int = Field(ge=0, le=10)
    assignments: list[Assignment] = Field(min_length=1, max_length=20)
    final_acceptance: list[str] = Field(min_length=1, max_length=30)
    owner_question: str | None = Field(default=None, max_length=2_000)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    summary: str = Field(min_length=1, max_length=10_000)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    revision_prompt: str | None = Field(default=None, max_length=10_000)
    owner_question: str | None = Field(default=None, max_length=2_000)


class ProviderRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: ProviderName
    assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    run_id: str
    status: str
    session_id: str | None = None
    url: str | None = None
    result: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus
    intake: SlackIntake
    plan: DirectorPlan | None = None
    provider_runs: list[ProviderRun] = Field(default_factory=list)
    review: ReviewDecision | None = None
    next_assignment_index: int = Field(default=0, ge=0, le=20)
    revision_count: int = 0
    owner_approved: bool = False
    message: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OwnerApproval(BaseModel):
    owner_user_id: str
    note: str = Field(default="Approved", max_length=2_000)


class ClaudeCallback(BaseModel):
    status: str
    result: str = Field(min_length=1, max_length=50_000)
    session_url: str | None = None
