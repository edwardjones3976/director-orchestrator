# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | BEGIN]]
# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | BEGIN]]
# Administrative attribution only; material implementation remains Cursor-authored.
# [[Cursor | Composer 2.5 | 2026-09-21 | 20:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 01:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:08 UTC | BEGIN]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | BEGIN]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:07 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:02 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from .interactions import DirectorIdentity, ProviderInteractionEvent
from .workload_policy import WorkSize


def canonical_opaque_identifier_digest(value: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_task_effect_digest(value: dict[str, Any]) -> str:
    provider = value.get("provider")
    if isinstance(provider, ProviderName):
        provider = provider.value
    payload = {
        "event_version": "v2",
        "effect_key": value.get("effect_key"),
        "kind": value.get("kind"),
        "phase": value.get("phase"),
        "outcome": value.get("outcome"),
        "plan_generation": int(value.get("plan_generation", 0)),
        "assignment_sequence": (
            int(value["assignment_sequence"])
            if value.get("assignment_sequence") is not None
            else None
        ),
        "assignment_digest": value.get("assignment_digest"),
        "provider": provider,
        "attempt": int(value.get("attempt", 1)),
        "run_id_digest": value.get("run_id_digest"),
        "estimated_cost_usd": float(value.get("estimated_cost_usd", 0)),
        "hop_units": int(value.get("hop_units", 0)),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    REVIEWING = "REVIEWING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"


class OwnerApprovalSource(StrEnum):
    API = "api"
    SLACK_COMMAND = "slack_command"
    SLACK_BUTTON = "slack_button"


class IntakeAuthentication(StrEnum):
    BEARER_DRY_RUN = "bearer_dry_run"
    SLACK_GATEWAY_ATTESTED = "slack_gateway_attested"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProviderName(StrEnum):
    CURSOR = "cursor"
    CLAUDE = "claude"
    GEMINI = "gemini"
    GROK = "grok"
    PERPLEXITY = "perplexity"
    OWNER = "owner"
    # Separately assigned production is distinct from Director control-plane work.
    OPENAI = "openai"
    FIRECRAWL = "firecrawl"
    GITHUB_COPILOT = "github-copilot"


class AssignmentKind(StrEnum):
    """The bounded work type an assignment carries.

    Antigravity is admitted only for the four subordinate kinds; every other
    provider keeps the general default unless the Director types it explicitly.
    """

    GENERAL = "general"
    IMPLEMENTATION = "implementation"
    RESEARCH = "research"
    INDEPENDENT_REVIEW = "independent_review"
    EXACT_SHA_REVIEW = "exact_sha_review"
    BOILERPLATE = "boilerplate"
    SCAFFOLDING = "scaffolding"
    LOW_LEVEL_IMPLEMENTATION = "low_level_implementation"
    ABSTRACT_IDEATION = "abstract_ideation"


class ResearchEvidenceType(StrEnum):
    """Public evidence a plan explicitly asks the research lane to collect."""

    EXTERNAL_SOURCES = "external_sources"
    USER_REVIEWS = "user_reviews"
    COMPETITOR_SOLUTIONS = "competitor_solutions"


class ReviewVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    OWNER = "owner"
    FAIL = "fail"


class DiscussionPhase(StrEnum):
    WORK = "work"
    POSITION = "position"
    REBUTTAL = "rebuttal"


class DebateActorRole(StrEnum):
    """Auditable OpenAI roles; none of these labels grants authority."""

    OPENAI_MODERATOR = "openai_moderator"
    OPENAI_PARTICIPANT = "openai_participant"
    OPENAI_DIRECTOR = "openai_director"
    CLAUDE_MODERATOR = "claude_moderator"
    CLAUDE_PARTICIPANT = "claude_participant"
    CLAUDE_DIRECTOR = "claude_director"


class MaterialDecisionDimension(StrEnum):
    """Decision dimensions that require owner resolution when tied."""

    COST = "cost"
    AUTHORITY = "authority"
    SAFETY = "safety"
    PRIVACY = "privacy"
    REPOSITORY_SCOPE = "repository_scope"
    RUNTIME_BEHAVIOR = "runtime_behavior"
    PROJECT_DIRECTION = "project_direction"


class AntigravityFixtureLane(StrEnum):
    """The complete set of disposable public work units Antigravity can be given.

    Antigravity receives no prose. The Director selects a lane, and the runtime
    substitutes that lane's fixed, repository-owned specification. A lane therefore
    describes work that is meaningful without any project context at all: the
    artifact comes back generic and a primary-team model adapts it. Selecting from
    a closed enum cannot encode a project fact, which is the property free text
    could never provide.
    """

    UNIT_TEST_FIXTURE_SET = "unit_test_fixture_set"
    DATA_RECORD_SAMPLES = "data_record_samples"
    MODULE_SKELETON = "module_skeleton"
    CONFIG_TEMPLATE = "config_template"
    VALIDATION_RULE_STUBS = "validation_rule_stubs"
    STATE_MACHINE_SKELETON = "state_machine_skeleton"
    PAGINATION_HELPERS = "pagination_helpers"
    RETRY_BACKOFF_VARIANTS = "retry_backoff_variants"
    ERROR_TAXONOMY_OPTIONS = "error_taxonomy_options"
    NAMING_ALTERNATIVES = "naming_alternatives"


class AntigravityLanguage(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "markdown"
    LANGUAGE_AGNOSTIC = "language_agnostic"


class AntigravityDirective(BaseModel):
    """Everything the Director may choose about an Antigravity dispatch.

    Closed enums and bounded integers only. There is deliberately no free-text
    field: a string here would be model-authored prose, and no filter can prove
    model-authored prose carries no confidential project fact.
    """

    model_config = ConfigDict(extra="forbid")

    lane: AntigravityFixtureLane
    language: AntigravityLanguage = AntigravityLanguage.LANGUAGE_AGNOSTIC
    variant_count: int = Field(default=1, ge=1, le=10)
    max_lines_per_artifact: int = Field(default=120, ge=10, le=400)


class UsageCheckStatus(StrEnum):
    OK = "ok"
    PROMPTED = "prompted"
    ERROR = "error"
    SKIPPED = "skipped"


MAX_SLACK_INTAKE_TEXT = 20_000


class SlackIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1, max_length=255)
    channel_id: str = Field(min_length=1, max_length=64)
    thread_ts: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_SLACK_INTAKE_TEXT)
    allocation_profile: Literal["engineering", "mixed", "public_research", "enterprise"] = "engineering"
    primary_required_capabilities: tuple[Literal["public_research", "enterprise_context", "bounded_scaffolding", "repository_assistance"], ...] = Field(default=(), max_length=4)
    primary_public_research_query: str | None = Field(default=None, min_length=1, max_length=500)
    permalink: str | None = Field(default=None, max_length=2_000)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def slack_status_text_digest(text: str) -> str:
    """Digest-only storage for status prose; raw text is not persisted."""

    normalized = text.strip()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SlackStatusReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1, max_length=255)
    channel_id: str = Field(min_length=1, max_length=64)
    thread_ts: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    text_digest: str = Field(min_length=8, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$")
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusReceiptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=64)
    deduplicated: bool
    created_at: datetime


class SlackThreadBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(min_length=1, max_length=64)
    thread_ts: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1, le=20)
    agent: ProviderName
    kind: AssignmentKind = AssignmentKind.GENERAL
    # Defaults keep pre-policy persisted assignments readable. A v1 workload plan
    # must declare the intended size and points explicitly in its authoritative
    # pre-stage packet; deterministic policy validates the resulting route.
    workload_size: WorkSize = WorkSize.SIMPLE
    effort_points: int = Field(default=1, ge=1, le=100)
    retry_limit: int = Field(default=3, ge=3, le=3)
    # Live LangGraph-primary plans may bind a transport lane that is broader than
    # the historical Director agent enum. Model-authored plans leave this unset;
    # the deterministic primary planner supplies it after policy normalization.
    allocation_profile: Literal["engineering", "mixed", "public_research", "enterprise"] = "engineering"
    primary_provider_lane: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
    )
    model_label: str | None = Field(default=None, min_length=1, max_length=255)
    task: str = Field(min_length=1, max_length=10_000)
    acceptance: list[str] = Field(min_length=1, max_length=20)
    public_research_query: str | None = Field(default=None, min_length=1, max_length=2_000)
    # Required on every Antigravity work assignment and null everywhere else. `task`
    # and `acceptance` stay for the Director's own review and the owner's Slack
    # thread; they are never transmitted to Antigravity.
    antigravity_directive: AntigravityDirective | None = None
    repository: str | None = Field(default=None, max_length=2_000)
    starting_ref: str = Field(default="main", min_length=1, max_length=255)
    depends_on: list[int] = Field(default_factory=list, max_length=20)
    requires_approval: bool = False
    allow_draft_pr: bool = False
    estimated_cost_usd: float = Field(default=0, ge=0, le=1_000)
    discussion_phase: DiscussionPhase = DiscussionPhase.WORK
    # Positions are round zero. Rebuttals use one-based rounds so policy can
    # validate a complete bounded exchange without interpreting provider prose.
    discussion_round: int = Field(default=0, ge=0, le=3)

    @model_validator(mode="after")
    def default_first_rebuttal_round(self) -> Assignment:
        # Preserve v1 plan compatibility: rebuttals persisted before the explicit
        # round field existed are first-round turns.
        if (
            self.discussion_phase == DiscussionPhase.REBUTTAL
            and self.discussion_round == 0
        ):
            self.discussion_round = 1
        return self


class DebateInitialPosition(BaseModel):
    """OpenAI's preliminary view captured before specialist conclusions exist.

    ``provisional_option`` is deliberately not an advisory vote. The executing
    Director casts its one weighted vote or records an explicit abstention in
    the later synthesis response; moderating preserves its right to vote.
    """

    model_config = ConfigDict(extra="forbid")

    position: str = Field(min_length=1, max_length=2_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    contradictions: list[str] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(min_length=1, max_length=10)
    material_risks: list[str] = Field(default_factory=list, max_length=10)
    unknowns: list[str] = Field(min_length=1, max_length=10)
    conditions_to_change: list[str] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0, le=1)
    provisional_option: str | None = Field(default=None, min_length=1, max_length=255)
    abstain: bool = False

    @model_validator(mode="after")
    def validate_preference(self) -> DebateInitialPosition:
        if self.abstain == (self.provisional_option is not None):
            raise ValueError(
                "exactly one of provisional_option or abstain is required"
            )
        return self


class TieRubricCriterion(BaseModel):
    """One predeclared, ordered criterion for an ordinary tie judgment."""

    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=255)
    evaluation_question: str = Field(min_length=1, max_length=1_000)


class TieJudgmentRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[TieRubricCriterion] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_unique_criteria(self) -> TieJudgmentRubric:
        normalized = [item.criterion.strip().lower() for item in self.criteria]
        if len(normalized) != len(set(normalized)):
            raise ValueError("tie rubric criteria must be unique")
        return self


class TieRubricApplication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=255)
    finding: str = Field(min_length=1, max_length=2_000)
    favors_option: str | None = Field(default=None, min_length=1, max_length=255)


class DebatePlan(BaseModel):
    """A bounded, Director-owned discussion attached to one task."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2_000)
    # Optional on deserialization so pre-transition persisted tasks remain readable;
    # execution policy requires all three fields before any new debate can run.
    neutral_framing: str | None = Field(default=None, min_length=1, max_length=2_000)
    alternative_option: str | None = Field(default=None, min_length=1, max_length=255)
    openai_initial_position: DebateInitialPosition | None = None
    tie_judgment_rubric: TieJudgmentRubric | None = None
    material_dimensions: list[MaterialDecisionDimension] | None = Field(
        default=None, max_length=7
    )
    position_assignments: list[int] = Field(min_length=2, max_length=4)
    rebuttal_assignments: list[int] = Field(min_length=2, max_length=4)
    vote_options: list[str] = Field(min_length=2, max_length=6)
    deadline_minutes: int = Field(default=20, ge=5, le=120)
    max_rebuttal_rounds: Literal[1] = 1
    material_risk: bool = False
    estimated_counter_bias_cost_usd: float = Field(default=0, ge=0, le=1_000)
    estimated_synthesis_cost_usd: float = Field(default=0, ge=0, le=1_000)
    estimated_tie_judgment_cost_usd: float = Field(default=0, ge=0, le=1_000)


class LeanExecutionLimits(BaseModel):
    """Immutable execution limits attached to every v1 workload plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    retry_limit_source: Literal["immutable_assignment_prestage"] = (
        "immutable_assignment_prestage"
    )
    additional_attempt_requires_classified_failure: Literal[True] = True
    additional_attempt_requires_positive_information_gain: Literal[True] = True
    additional_attempt_requires_changed_basis: Literal[True] = True
    token_cost_timeout_and_cancellation_budgets_enforced: Literal[True] = True
    ambiguous_paid_dispatch_retry_allowed: Literal[False] = False
    unchanged_green_validation_rerun_allowed: Literal[False] = False
    preferred_completion_signal: Literal["callback_or_event"] = "callback_or_event"
    browser_transport_priority: Literal["lower_priority_fallback"] = (
        "lower_priority_fallback"
    )
    browser_usage_reduction_target_percent: Literal[0] = 0
    initial_status_read_limit: Literal[1] = 1
    terminal_status_verification_limit: Literal[1] = 1
    major_action_limit: Literal[5] = 5
    status_check_reduction_target_percent: Literal[80] = 80
    duplicate_validation_reduction_target_percent: Literal[70] = 70
    total_action_reduction_target_percent: Literal[60] = 60


class PlanPrestageRecord(BaseModel):
    """Immutable, digest-bound handoff packet prepared before provider dispatch.

    ``digest`` is optional only while a Director proposal is being parsed and for
    old persisted plans. ``enforce_plan`` replaces a proposed record with the
    canonical record produced by ``workload_policy.PrestagePacket``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    allocation_profile: Literal["engineering", "mixed", "public_research", "enterprise"] = "engineering"
    allocation_profile_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    schema_version: Literal["prestage.v1"] = "prestage.v1"
    packet_id: str = Field(min_length=1, max_length=255)
    goal: str = Field(min_length=1, max_length=10_000)
    terminal_condition: str = Field(min_length=1, max_length=10_000)
    repository: str = Field(min_length=1, max_length=2_000)
    worktree: str = Field(min_length=1, max_length=2_000)
    branch: str = Field(min_length=1, max_length=255)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    producer: ProviderName
    independent_reviewer: ProviderName
    owned_paths: tuple[str, ...] = Field(min_length=1, max_length=100)
    prohibited_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    required_tests: tuple[str, ...] = Field(min_length=1, max_length=100)
    required_security_evidence: tuple[str, ...] = Field(min_length=1, max_length=100)
    budget_units: int = Field(ge=1, le=100_000)
    timeout_seconds: int = Field(ge=1, le=604_800)
    retry_limit: int = Field(ge=3, le=3)
    merge_eligibility_conditions: tuple[str, ...] = Field(
        min_length=1, max_length=50
    )
    stopping_conditions: tuple[str, ...] = Field(min_length=1, max_length=50)
    digest: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )


class WorkloadEnforcementRecord(BaseModel):
    """Trusted deterministic outcome attached by ``enforce_plan``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["disabled", "shadow", "enforced"]
    outcome: Literal["disabled", "allow", "block", "shadow_would_block"]
    allowed: bool
    would_block: bool
    violations: tuple[str, ...] = Field(default_factory=tuple, max_length=100)


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
    # None is the durable legacy marker. It lets pre-policy plans deserialize and
    # complete under their original authority while newly emitted v1 plans receive
    # strict deterministic workload, team, research, and pre-stage enforcement.
    workload_policy_version: Literal["v1"] | None = None
    workload_target_variance_reason: str | None = Field(
        default=None, max_length=2_000
    )
    research_evidence: tuple[ResearchEvidenceType, ...] = Field(
        default_factory=tuple, max_length=3
    )
    execution_limits: LeanExecutionLimits = Field(
        default_factory=LeanExecutionLimits
    )
    prestage: PlanPrestageRecord | None = None
    workload_enforcement: WorkloadEnforcementRecord | None = None


class DebateContribution(BaseModel):
    """A bounded provider position extracted by the Director from untrusted output."""

    model_config = ConfigDict(extra="forbid")

    position: str = Field(min_length=1, max_length=2_000)
    # Optional only so pre-transition persisted reviews remain readable. A new
    # POSITION assignment fails closed unless every field is explicitly present.
    rationale: str | None = Field(default=None, min_length=1, max_length=4_000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    contradictions: list[str] = Field(default_factory=list, max_length=10)
    assumptions: list[str] | None = Field(default=None, max_length=10)
    material_risks: list[str] | None = Field(default=None, max_length=10)
    unknowns: list[str] | None = Field(default=None, max_length=10)
    conditions_to_change: list[str] | None = Field(default=None, max_length=10)
    confidence: float = Field(ge=0, le=1)
    vote_option: str | None = Field(default=None, min_length=1, max_length=255)
    abstain: bool = False

    @model_validator(mode="after")
    def validate_vote(self) -> DebateContribution:
        if self.abstain == (self.vote_option is not None):
            raise ValueError("exactly one of vote_option or abstain is required")
        return self


class RetryEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    acceptance_index: int = Field(ge=0)
    evidence_index: int = Field(ge=0)
    evidence_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class RetryBasis(BaseModel):
    """Structured, candidate-bound justification for one additional attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    failed_acceptance_indexes: tuple[int, ...] = Field(min_length=1, max_length=20)
    prior_result_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    corrected_approach: str = Field(min_length=24, max_length=10_000)
    evidence_refs: tuple[RetryEvidenceRef, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_unique_nonnegative_indexes(self) -> RetryBasis:
        indexes = self.failed_acceptance_indexes
        if any(index < 0 for index in indexes) or len(indexes) != len(set(indexes)):
            raise ValueError("failed acceptance indexes must be unique and nonnegative")
        reference_pairs = {
            (item.acceptance_index, item.evidence_index) for item in self.evidence_refs
        }
        if len(reference_pairs) != len(self.evidence_refs):
            raise ValueError("retry evidence references must be unique")
        if {item.acceptance_index for item in self.evidence_refs} != set(indexes):
            raise ValueError("every failed acceptance must have exact new evidence")
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    summary: str = Field(min_length=1, max_length=10_000)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    revision_prompt: str | None = Field(default=None, max_length=10_000)
    retry_basis: RetryBasis | None = None
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

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_post_hoc_counter_bias(cls, value: object) -> object:
        """Read old rows without exposing obsolete fields to new output schemas."""

        if isinstance(value, dict):
            migrated = dict(value)
            migrated.pop("counter_bias_analysis", None)
            migrated.pop("counter_bias_changed_decision", None)
            migrated.pop("director_judgment_option", None)
            migrated.pop("tie_rubric_applications", None)
            return migrated
        return value

    @model_validator(mode="after")
    def validate_openai_vote(self) -> DebateSynthesis:
        if self.openai_abstains == (self.openai_vote_option is not None):
            raise ValueError("exactly one of openai_vote_option or openai_abstains is required")
        return self


class DebateTieJudgment(BaseModel):
    """A separate non-voting Director judgment for a deterministic ordinary tie."""

    model_config = ConfigDict(extra="forbid")

    chosen_option: str = Field(min_length=1, max_length=255)
    rubric_applications: list[TieRubricApplication] = Field(min_length=2, max_length=8)
    rationale: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0, le=1)


class DebateCounterBias(BaseModel):
    """A separate OpenAI Moderator result completed before final synthesis."""

    model_config = ConfigDict(extra="forbid")

    strongest_counterargument: str = Field(min_length=1, max_length=2_000)
    challenged_assumptions: list[str] = Field(min_length=1, max_length=10)
    disconfirming_evidence: list[str] = Field(default_factory=list, max_length=10)
    alternatives_reconsidered: list[str] = Field(min_length=1, max_length=10)
    independence_assessment: str = Field(min_length=1, max_length=2_000)
    recommendation_changed: bool
    provisional_recommendation: str | None = Field(
        default=None, min_length=1, max_length=255
    )
    confidence: float = Field(ge=0, le=1)


class CollaborationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    provider: ProviderName
    phase: Literal[
        "delegation",
        "work_result",
        "moderator",
        "position",
        "rebuttal",
        "counter_bias",
        "tie_judgment",
        "synthesis",
        "completion",
    ]
    debate_role: DebateActorRole | None = None
    correlation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    position: str = Field(min_length=1, max_length=2_000)
    rationale: str | None = Field(default=None, max_length=4_000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    contradictions: list[str] = Field(default_factory=list, max_length=10)
    assumptions: list[str] | None = Field(default=None, max_length=10)
    material_risks: list[str] | None = Field(default=None, max_length=10)
    unknowns: list[str] | None = Field(default=None, max_length=10)
    conditions_to_change: list[str] | None = Field(default=None, max_length=10)
    verified_debate_contribution: DebateContribution | None = None
    director_review: ReviewDecision | None = None
    director_review_interaction_id: str | None = Field(default=None, max_length=255)
    verified_director_review_event_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    director_review_result_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    confidence: float = Field(ge=0, le=1)
    vote_option: str | None = Field(default=None, max_length=255)
    abstain: bool = False
    slack_message_ts: str | None = Field(default=None, max_length=64)
    interaction_id: str | None = Field(default=None, min_length=1, max_length=255)
    verified_interaction_event_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    provider_result_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: ProviderName
    interaction_id: str | None = Field(default=None, min_length=1, max_length=255)
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
    interaction_id: str | None = Field(default=None, min_length=1, max_length=255)
    verified_interaction_event_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    provider_result_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    director_review_interaction_id: str | None = Field(
        default=None, min_length=1, max_length=255
    )
    verified_director_review_event_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    director_review_result_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
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


class TaskEffectEvidence(BaseModel):
    """Immutable, compact evidence for a paid or externally effective run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_version: Literal["v2"] = "v2"
    effect_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    effect_key: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    kind: Literal[
        "provider_intent",
        "provider_observation",
        "director_intent",
        "director_observation",
        "slack_intent",
        "slack_observation",
        "oracle_merge_intent",
        "oracle_merge_observation",
    ]
    phase: Literal[
        "dispatch",
        "revision",
        "plan",
        "acceptance_review",
        "debate_counter_bias",
        "debate_synthesis",
        "debate_tie_judgment",
        "slack_delivery",
        "oracle_merge",
    ]
    outcome: Literal[
        "reserved",
        "submitted",
        "failed_pre_submit",
        "ambiguous",
        "completed",
        "delivered",
        "merged",
        "stopped",
    ]
    plan_generation: int = Field(ge=0)
    assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    assignment_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    provider: ProviderName | None = None
    attempt: int = Field(default=1, ge=1, le=10)
    run_id_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    estimated_cost_usd: float = Field(default=0, ge=0, le=1_000)
    hop_units: int = Field(default=0, ge=0, le=10)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_event(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        run_id = migrated.pop("run_id", None)
        if run_id and not migrated.get("run_id_digest"):
            migrated["run_id_digest"] = canonical_opaque_identifier_digest(
                str(run_id)
            )
        if migrated.get("event_version") != "v2":
            migrated["event_version"] = "v2"
            migrated["effect_digest"] = canonical_task_effect_digest(migrated)
        return migrated

    @model_validator(mode="after")
    def verify_canonical_digest(self) -> TaskEffectEvidence:
        expected = canonical_task_effect_digest(self.model_dump(mode="python"))
        if self.effect_digest != expected:
            raise ValueError("Task effect digest does not match its canonical v2 event")
        return self


class OracleRecoveryState(BaseModel):
    """Exact-bound owner recovery state for a stopped Oracle merge attempt."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "artifact_authentication_failed",
        "automerge_claim_expired",
        "automerge_runtime_failed",
        "merge_policy_stopped",
    ]
    plan_generation: int = Field(ge=0)
    assignment_sequence: int = Field(ge=1, le=20)
    provider_run_id: str = Field(min_length=1, max_length=255)
    provider_run_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    evidence_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    artifact_id: str | None = Field(default=None, min_length=1, max_length=255)
    retry_not_before: datetime | None = None
    ordinal: int = Field(default=1, ge=1, le=3)
    decision_digest: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    retryable: bool = False
    approved: bool = False


class OpaqueEvidenceDigest(BaseModel):
    """Bounded proof that discarded plan-local evidence once existed.

    The original provider payload is intentionally unrecoverable from this
    projection. Durable external-effect accounting lives in TaskStore's
    append-only SQLite event table instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_value(cls, value: object) -> OpaqueEvidenceDigest:
        if isinstance(value, cls):
            return value
        if (
            isinstance(value, dict)
            and set(value) == {"digest"}
            and isinstance(value.get("digest"), str)
            and len(value["digest"]) == 64
            and all(character in "0123456789abcdef" for character in value["digest"])
        ):
            return cls.model_validate(value)
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return cls(digest=hashlib.sha256(encoded).hexdigest())


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # SQLite's optimistic concurrency token is deliberately not part of the
    # API or persisted task JSON. TaskStore attaches it to records loaded from
    # one exact row version so a stale in-memory callback/result cannot restore
    # an older workflow generation.
    _store_version: int | None = PrivateAttr(default=None)

    task_id: str
    status: TaskStatus
    intake: SlackIntake
    # Missing authentication authority always fails closed. Production Slack
    # intake must explicitly install its authenticated gateway attestation.
    intake_authentication: IntakeAuthentication = (
        IntakeAuthentication.BEARER_DRY_RUN
    )
    plan: DirectorPlan | None = None
    # Monotonically increases whenever the engine installs a newly generated
    # plan. Slack approval buttons bind to this generation and the plan digest.
    plan_generation: int = Field(default=0, ge=0)
    # Append only when an authenticated planning generation is reserved. Missing
    # historical entries mean OpenAI; current settings never supply their identity.
    director_generations: dict[int, DirectorIdentity] = Field(default_factory=dict)
    provider_runs: list[ProviderRun] = Field(default_factory=list)
    assignment_outcomes: list[AssignmentOutcome] = Field(default_factory=list)
    provider_run_history: list[OpaqueEvidenceDigest] = Field(
        default_factory=list, max_length=200
    )
    assignment_outcome_history: list[OpaqueEvidenceDigest] = Field(
        default_factory=list, max_length=200
    )
    provider_contact_attempt_history: list[OpaqueEvidenceDigest] = Field(
        default_factory=list, max_length=200
    )
    task_effect_ledger: list[TaskEffectEvidence] = Field(
        default_factory=list, max_length=200
    )
    provider_interaction_ledger: list[ProviderInteractionEvent] = Field(
        default_factory=list, max_length=500
    )
    cumulative_estimated_cost_usd: float = Field(default=0, ge=0, le=100_000)
    cumulative_effect_hops: int = Field(default=0, ge=0, le=10_000)
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
    debate_counter_bias: DebateCounterBias | None = None
    debate_counter_bias_interaction_id: str | None = Field(default=None, max_length=255)
    debate_synthesis: DebateSynthesis | None = None
    debate_tie_judgment: DebateTieJudgment | None = None
    debate_tie_judgment_interaction_id: str | None = Field(default=None, max_length=255)
    plan_interaction_id: str | None = Field(default=None, max_length=255)
    workload_selection_hold_reasons: list[str] = Field(default_factory=list, max_length=20)
    review_interaction_id: str | None = Field(default=None, max_length=255)
    debate_synthesis_interaction_id: str | None = Field(default=None, max_length=255)
    review: ReviewDecision | None = None
    oracle_recovery: OracleRecoveryState | None = None
    next_assignment_index: int = Field(default=0, ge=0, le=20)
    revision_count: int = 0
    revision_prompt_digests: list[str] = Field(default_factory=list, max_length=20)
    retry_basis_digests: list[str] = Field(default_factory=list, max_length=20)
    # A cancellation is retry authority, not transient process state. Persist the
    # authenticated request so recovery cannot silently recreate retry headroom.
    cancellation_requested_at: datetime | None = None
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

    @field_validator(
        "provider_run_history",
        "assignment_outcome_history",
        "provider_contact_attempt_history",
        mode="before",
    )
    @classmethod
    def migrate_legacy_history_to_digests(cls, value: object) -> object:
        """Accept legacy task JSON without retaining its provider text."""

        if value is None:
            return []
        if not isinstance(value, list):
            return value
        return [OpaqueEvidenceDigest.from_value(item) for item in value]

    def compact_history_projections(self) -> None:
        """Keep only the newest 200 unique opaque history digests."""

        for field_name in (
            "provider_run_history",
            "assignment_outcome_history",
            "provider_contact_attempt_history",
        ):
            values = getattr(self, field_name)
            kept_reversed: list[OpaqueEvidenceDigest] = []
            seen: set[str] = set()
            for item in reversed(values):
                if item.digest in seen:
                    continue
                kept_reversed.append(item)
                seen.add(item.digest)
                if len(kept_reversed) == 200:
                    break
            setattr(self, field_name, list(reversed(kept_reversed)))


# Response-only qualification readback. Nothing below is persisted or part of
# the ordinary TaskRecord contract; it is attached only when the authenticated
# GET opts in and is rebuilt from bounded READ ONLY queries of existing tables.
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class QualificationAssignmentRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    sequence: int = Field(ge=1)
    provider_id: str
    deployment_id: str
    status: str
    reservation_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    reason_code: str | None = None
    reserved_at: datetime | None = None
    completed_at: datetime | None = None


class QualificationAttemptRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    assignment_sequence: int = Field(ge=1)
    attempt_ordinal: int = Field(ge=1, le=3)
    event_sequence: int = Field(ge=1, le=3)
    provider_id: str
    format_id: str
    transport_id: str
    idempotency_key_digest: str = Field(pattern=_DIGEST_PATTERN)
    attempt_digest: str = Field(pattern=_DIGEST_PATTERN)
    provider_run_id_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    status: str
    result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    outcome_evidence_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    reason_code: str
    budget_evidence_digest: str = Field(pattern=_DIGEST_PATTERN)
    recorded_at: datetime


class QualificationEffectRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_key: str
    claim_key: str
    effect_key_digest: str
    effect_attempt_digest: str
    writer_owner_id: str
    fencing_generation: int
    reserved_units: int = Field(ge=0)
    reserved_at: datetime
    execution_started_at: datetime | None = None
    remote_system: str | None = None
    remote_identifier: str | None = None
    bound_at: datetime | None = None
    terminal_disposition: str | None = None
    terminal_evidence_digest: str | None = None
    terminal_recorded_at: datetime | None = None


class QualificationReconciliationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int
    claim_key: str
    state: str
    evidence_digest: str
    recorded_at: datetime


class QualificationDeliveryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_key: str
    projection_digest: str
    client_msg_id: str
    state: str
    created_at: datetime
    claimed_at: datetime | None = None
    acknowledged_at: datetime | None = None
    delivered_at: datetime | None = None
    slack_ts: str | None = None
    readback_attempts: int = Field(ge=0, le=2)
    last_error: str | None = None
    owner_decision: str | None = None
    owner_decision_at: datetime | None = None
    owner_command_id: str | None = None


class QualificationStatusReceiptRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    source_event_id: str
    created_at: datetime


class QualificationUsageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_digest: str
    provider_id: str
    reserved_microusd: int = Field(ge=0)
    token_limit: int = Field(ge=0)
    actual_microusd: int = Field(ge=0)
    observed_tokens: int = Field(ge=0)


class QualificationReadbackBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_limit: int = Field(ge=1)
    byte_limit: int = Field(ge=1024)
    wall_seconds: float = Field(gt=0)
    elapsed_seconds: float = Field(ge=0)


class QualificationReadbackSnapshot(BaseModel):
    """What one readback transaction covered; never a claim about the transport."""

    model_config = ConfigDict(extra="forbid")

    isolation: Literal["REPEATABLE READ"]
    read_only: Literal[True]
    single_transaction: Literal[True]
    covers_task_record: bool


class QualificationEvidenceReadback(BaseModel):
    """Bounded READ ONLY projection of one task's persisted qualification evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["director-qualification-readback/v1"]
    task_id: str
    engine_owner: str
    projection_version: int = Field(ge=0)
    projection_digest: str
    source_event_id: str | None = None
    execution_mode: str | None = None
    terminal_classification: str | None = None
    terminal_reason_code: str | None = None
    task_created_at: datetime
    task_updated_at: datetime
    snapshot: QualificationReadbackSnapshot
    bounds: QualificationReadbackBounds
    truncated: list[str] = Field(default_factory=list)
    absent: list[str] = Field(default_factory=list)
    complete: bool
    assignments: list[QualificationAssignmentRow] = Field(default_factory=list)
    provider_attempts: list[QualificationAttemptRow] = Field(default_factory=list)
    effects: list[QualificationEffectRow] = Field(default_factory=list)
    reconciliation_events: list[QualificationReconciliationRow] = Field(default_factory=list)
    slack_deliveries: list[QualificationDeliveryRow] = Field(default_factory=list)
    status_receipts: list[QualificationStatusReceiptRow] = Field(default_factory=list)
    provider_usage: list[QualificationUsageRow] = Field(default_factory=list)


class TaskReadbackRecord(TaskRecord):
    """TaskRecord plus the opt-in readback; never persisted, never digested."""

    qualification_evidence: QualificationEvidenceReadback | None = None


class SlackGatewayProof(BaseModel):
    """Short-lived proof emitted only by the local Slack gateway."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = "v1"
    task_version: int = Field(ge=0)
    channel_id: str = Field(min_length=1, max_length=64)
    issued_at: int = Field(ge=0)
    nonce: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class OwnerApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    note: str = Field(default="Approved", max_length=2_000)
    source: OwnerApprovalSource = OwnerApprovalSource.API
    decision_digest: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    workload_selection_decision_digest: str | None = Field(
        default=None, min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$",
    )
    retry_decision_digest: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    # The assignment the owner was actually shown. Slack approval buttons persist
    # in the channel, so an old button must not approve whatever assignment
    # happens to be pending now. None means "whatever is pending", which is only
    # safe for an approval the owner types fresh, such as the slash command.
    assignment_sequence: int | None = Field(default=None, ge=1, le=20)
    slack_gateway_proof: SlackGatewayProof | None = None

    @model_validator(mode="after")
    def validate_source_binding(self) -> OwnerApproval:
        if self.workload_selection_decision_digest is not None and self.source != OwnerApprovalSource.API:
            raise ValueError("Explicit workload clarification digest is reserved for API requests")
        note = self.note.strip()
        if self.source == OwnerApprovalSource.SLACK_BUTTON:
            if self.decision_digest is None:
                raise ValueError("Slack button approval requires an exact decision digest")
            if note != "Approved from Slack":
                raise ValueError("Slack button approval must use the generated button note")
            if self.retry_decision_digest is not None:
                raise ValueError("Slack approval buttons cannot authorize failed retries")
        else:
            if self.decision_digest is not None:
                raise ValueError("Only a Slack button may carry a decision digest")
            if note == "Approved from Slack":
                raise ValueError("Unbound legacy Slack button approval is not accepted")
        if self.retry_decision_digest is not None and self.assignment_sequence is None:
            raise ValueError("Failed retry digest requires an assignment sequence")
        return self


class OracleRecoveryApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str = Field(min_length=1, max_length=64)
    decision_digest: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    action: Literal["retry_recoverable", "replan_with_remediation", "cancel"]
    note: str = Field(min_length=3, max_length=2_000)


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


class ClaudeCallbackSideEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=2_000)
    status: Literal["NOT_ATTEMPTED", "ATTEMPTED", "COMPLETED", "FAILED"]
    idempotency_key: str = Field(min_length=1, max_length=2_000)
    external_id: str | None = Field(default=None, max_length=2_000)


class ClaudeCallbackSideEffectEvidence(BaseModel):
    """Bounded terminal effect attestation delivered by the callback itself."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    task_id: str = Field(min_length=1, max_length=64)
    assignment_id: str = Field(min_length=1, max_length=128)
    packet_idempotency_key: str = Field(min_length=1, max_length=2_000)
    authority_snapshot_digest: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    correlation_id: str = Field(min_length=1, max_length=255)
    repository: str = Field(min_length=1, max_length=255)
    starting_ref: str = Field(min_length=1, max_length=255)
    provider: Literal["claude"]
    provider_run_id: str = Field(min_length=1, max_length=255)
    result_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    effects: list[ClaudeCallbackSideEffect] = Field(max_length=20)


class ClaudeCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=255)
    status: Literal["completed", "failed", "cancelled"]
    result: str = Field(min_length=1, max_length=50_000)
    session_url: str | None = Field(default=None, max_length=2_000)
    side_effect_evidence: ClaudeCallbackSideEffectEvidence | None = None


class ManualRunCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str = Field(min_length=1, max_length=64)
    result: str = Field(min_length=1, max_length=50_000)
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 10:37 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:02 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:30 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:07 UTC | END]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:08 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 22:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:39 UTC | END]]

# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:54 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 01:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:02 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:45 UTC | END]]

# [[Cursor | Composer 2.5 | 2026-09-21 | 20:19 UTC | END]]

# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | END]]
