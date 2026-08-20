from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # The Director's own runtime. Never a plan assignment target: it is absent from
    # provider_registry() and from the available_agents list sent to the Director,
    # and exists only to label the OpenAI usage-check probe in usage.py.
    OPENAI = "openai"


class ReviewVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    OWNER = "owner"
    FAIL = "fail"


class DiscussionPhase(StrEnum):
    WORK = "work"
    POSITION = "position"
    REBUTTAL = "rebuttal"


class UsageCheckStatus(StrEnum):
    OK = "ok"
    PROMPTED = "prompted"
    ERROR = "error"
    SKIPPED = "skipped"


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
    public_research_query: str | None = Field(default=None, min_length=1, max_length=2_000)
    repository: str | None = Field(default=None, max_length=2_000)
    starting_ref: str = Field(default="main", min_length=1, max_length=255)
    depends_on: list[int] = Field(default_factory=list, max_length=20)
    requires_approval: bool = False
    allow_draft_pr: bool = False
    estimated_cost_usd: float = Field(default=0, ge=0, le=1_000)
    discussion_phase: DiscussionPhase = DiscussionPhase.WORK


class DebatePlan(BaseModel):
    """A bounded, Director-owned discussion attached to one task."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2_000)
    position_assignments: list[int] = Field(min_length=2, max_length=4)
    rebuttal_assignments: list[int] = Field(min_length=2, max_length=4)
    vote_options: list[str] = Field(min_length=2, max_length=6)
    deadline_minutes: int = Field(default=20, ge=5, le=120)
    max_rebuttal_rounds: Literal[1] = 1
    material_risk: bool = False
    estimated_synthesis_cost_usd: float = Field(default=0, ge=0, le=1_000)


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
    debate: DebatePlan | None = None


class DebateContribution(BaseModel):
    """A bounded provider position extracted by the Director from untrusted output."""

    model_config = ConfigDict(extra="forbid")

    position: str = Field(min_length=1, max_length=2_000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    contradictions: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    vote_option: str | None = Field(default=None, min_length=1, max_length=255)
    abstain: bool = False

    @model_validator(mode="after")
    def validate_vote(self) -> DebateContribution:
        if self.abstain == (self.vote_option is not None):
            raise ValueError("exactly one of vote_option or abstain is required")
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    summary: str = Field(min_length=1, max_length=10_000)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    revision_prompt: str | None = Field(default=None, max_length=10_000)
    owner_question: str | None = Field(default=None, max_length=2_000)
    debate_contribution: DebateContribution | None = None


class DebateSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=2_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    dissent: list[str] = Field(default_factory=list, max_length=10)
    next_action: str = Field(min_length=1, max_length=2_000)
    openai_vote_option: str | None = Field(default=None, min_length=1, max_length=255)
    openai_abstains: bool = False
    confidence: float = Field(ge=0, le=1)
    owner_question: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_openai_vote(self) -> DebateSynthesis:
        if self.openai_abstains == (self.openai_vote_option is not None):
            raise ValueError("exactly one of openai_vote_option or openai_abstains is required")
        return self


class CollaborationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_sequence: int = Field(ge=1, le=20)
    provider: ProviderName
    phase: Literal[
        "delegation", "work_result", "position", "rebuttal", "synthesis", "completion"
    ]
    correlation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    position: str = Field(min_length=1, max_length=2_000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    contradictions: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    vote_option: str | None = Field(default=None, max_length=255)
    abstain: bool = False
    slack_message_ts: str | None = Field(default=None, max_length=64)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: ProviderName
    assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    run_id: str
    status: str
    session_id: str | None = None
    url: str | None = None
    result: str | None = None
    attempt_count: int = Field(default=1, ge=1, le=10)
    evidence_source: Literal[
        "provider_authenticated", "owner_recovery", "unverified"
    ] = "unverified"
    raw: dict[str, Any] = Field(default_factory=dict)


class UsageCheckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    status: UsageCheckStatus
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    response_text: str | None = Field(default=None, max_length=2_000)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: float | None = Field(default=None, ge=0)
    attempt_count: int = Field(default=1, ge=0, le=10)
    provider_error_code: str | None = Field(default=None, min_length=1, max_length=100)
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=2_000)


class UsageCheckRunStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_DUE = "not_due"
    BUSY = "busy"


class UsageCheckRunResponse(BaseModel):
    """Deterministic manual-cycle schedule contract for POST /v1/usage-checks/run."""

    model_config = ConfigDict(extra="forbid")

    status: UsageCheckRunStatus
    cycle_id: str | None = Field(default=None, min_length=1, max_length=64)
    scheduled_providers: list[str] = Field(default_factory=list, max_length=20)
    cooling_down_providers: list[str] = Field(default_factory=list, max_length=20)
    unsupported_providers: list[str] = Field(default_factory=list, max_length=20)
    minimum_interval_minutes: int = Field(default=15, ge=15, le=15)
    reason: str | None = Field(default=None, max_length=500)


class AssignmentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_sequence: int = Field(ge=1, le=20)
    provider: ProviderName
    run_id: str = Field(min_length=1, max_length=255)
    result: str = Field(min_length=1, max_length=50_000)
    review_summary: str = Field(min_length=1, max_length=10_000)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    unresolved_question: str | None = Field(default=None, max_length=2_000)


class ProviderContactAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_sequence: int = Field(ge=1, le=20)
    provider: ProviderName
    ordinal: int = Field(ge=1, le=3)
    phase: Literal["dispatch", "revision", "poll", "callback", "reconciliation"]
    outcome: Literal[
        "no_response",
        "ambiguous_dispatch",
        "reconciled_not_created",
        "reconciled_created",
    ]
    run_id: str | None = Field(default=None, max_length=255)
    partial_output: str | None = Field(default=None, max_length=2_000)
    error: str = Field(min_length=1, max_length=2_000)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus
    intake: SlackIntake
    plan: DirectorPlan | None = None
    provider_runs: list[ProviderRun] = Field(default_factory=list)
    assignment_outcomes: list[AssignmentOutcome] = Field(default_factory=list)
    review_handoff_digests: dict[int, str] = Field(default_factory=dict)
    provider_contact_attempts: list[ProviderContactAttempt] = Field(default_factory=list, max_length=200)
    unavailable_providers: list[ProviderName] = Field(default_factory=list)
    deferred_assignments: list[int] = Field(default_factory=list)
    availability_impacts: list[str] = Field(default_factory=list, max_length=50)
    collaboration_thread_ts: str | None = Field(default=None, max_length=64)
    collaboration_thread_opened: bool = False
    collaboration_turns: list[CollaborationTurn] = Field(default_factory=list, max_length=50)
    collaboration_delivery_errors: list[str] = Field(default_factory=list, max_length=20)
    collaboration_fallback_active: bool = False
    collaboration_fallback_reason: str | None = Field(default=None, max_length=1_000)
    debate_tally: dict[str, Any] | None = None
    debate_synthesis: DebateSynthesis | None = None
    review: ReviewDecision | None = None
    next_assignment_index: int = Field(default=0, ge=0, le=20)
    revision_count: int = 0
    # Approval that the plan as a whole may proceed. Does not by itself clear
    # any individual gated assignment -- see approved_assignments.
    owner_approved: bool = False
    # Sequence numbers of assignments the owner has explicitly approved. Each
    # gated assignment must appear here before it runs, so one approval cannot
    # silently clear every remaining gated assignment in the plan.
    approved_assignments: list[int] = Field(default_factory=list)
    # SHA-256 of the exact validated Assignment the owner saw. Sequence numbers
    # alone are insufficient because a later plan mutation could otherwise reuse
    # an old approval for a different public query or write scope.
    approved_assignment_digests: dict[int, str] = Field(default_factory=dict)
    message: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OwnerApproval(BaseModel):
    owner_user_id: str
    note: str = Field(default="Approved", max_length=2_000)
    # The assignment the owner was actually shown. Slack approval buttons persist
    # in the channel, so an old button must not approve whatever assignment
    # happens to be pending now. None means "whatever is pending", which is only
    # safe for an approval the owner types fresh, such as the slash command.
    assignment_sequence: int | None = Field(default=None, ge=1, le=20)


class AmbiguousDispatchResolution(BaseModel):
    """Owner-authenticated reconciliation for a dispatch with unknown outcome."""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: str = Field(min_length=1, max_length=64)
    assignment_sequence: int = Field(ge=1, le=20)
    resolution: Literal["not_created", "created"]
    note: str = Field(min_length=3, max_length=2_000)
    run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )

    @model_validator(mode="after")
    def validate_created_run(self) -> AmbiguousDispatchResolution:
        if self.resolution == "created" and not self.run_id:
            raise ValueError("created reconciliation requires run_id")
        if self.resolution == "not_created" and any(
            value is not None
            for value in (self.run_id, self.session_id)
        ):
            raise ValueError("not-created reconciliation cannot include provider-run fields")
        return self


class ClaudeCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=255)
    status: Literal["completed", "failed", "cancelled"]
    result: str = Field(min_length=1, max_length=50_000)
    session_url: str | None = Field(default=None, max_length=2_000)


class ManualRunCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str = Field(min_length=1, max_length=64)
    result: str = Field(min_length=1, max_length=50_000)
