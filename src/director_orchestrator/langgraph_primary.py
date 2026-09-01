# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:17 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 11:40 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 10:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:43 UTC | BEGIN]]
"""Typed, provider-neutral LangGraph parent workflow.

The graph in this module is a control-plane primitive.  It stores only compact
identifiers, exact SHAs, counters, booleans, and SHA-256 digests.  Provider
work and external effects are represented by caller-injected *pure* handlers;
this module imports no transport, provider, repository, Slack, or GitHub code.

Approval and provider-result nodes use LangGraph's dynamic ``interrupt``
primitive.  The facade checks the exact persisted approval request before
issuing ``Command(resume)`` and records an opaque receipt digest, making exact
replay idempotent.  Provider and effect execution are similarly fail closed:
the facade checkpoints the budget reservation and ``DISPATCHING``/``EXECUTING``
state before calling a pure handler.  A restart at either boundary is ambiguous
and routes to ``RECONCILIATION_REQUIRED`` without calling the handler again.

Every interrupting node evaluates its visit, cancellation, and timeout bounds
*before* the ``interrupt`` can consume a resumed value, so a bounded stop never
silently discards an approval or a paid provider/effect result.

The two side-effecting reservation transitions -- ``CLAIMED -> DISPATCHING``
and ``CLAIMED -> EXECUTING`` -- are deliberately *not* fenced by the
checkpointer.  ``BaseCheckpointSaver`` exposes no conditional write:
``update_state`` appends a new checkpoint unconditionally, so reading a status
and then writing it back is a read-modify-write, never a datastore
compare-and-swap, and no post-hoc re-read turns it into one.  Exclusion is
therefore delegated to an injected :class:`ConditionalClaimStore` whose single
required guarantee is that at most one caller is ever granted a given
``claim_key``.  When no such store is supplied the runtime refuses every
side-effecting transition and routes to ``RECONCILIATION_REQUIRED`` instead of
dispatching, so the absence of a genuine conditional store can never be
mistaken for permission to pay twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import uuid
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence, TypedDict, runtime_checkable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt


PRIMARY_SCHEMA_VERSION = "director-primary/v1"
DEFAULT_CHECKPOINT_NAMESPACE = "director-primary-v1"
MAX_RETAINED_VISITS = 32

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
_ACTOR_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_STATE_PATTERN = re.compile(r"^[A-Z][A-Z_]{0,63}$")


class PrimaryPhase(StrEnum):
    AUTHENTICATE_SCOPE = "authenticate_scope"
    CANONICALIZE = "canonicalize"
    PLAN = "plan"
    POLICY_BUDGET = "policy_budget"
    APPROVAL_INTERRUPT = "approval_interrupt"
    SCHEDULE = "schedule"
    PROVIDER_DISPATCH_CLAIM = "provider_dispatch_claim"
    PROVIDER_SUBGRAPH = "provider_subgraph"
    EVIDENCE_DEBATE = "evidence_debate"
    OPENAI_ACCEPTANCE_REVISION = "openai_acceptance_revision"
    EXACT_SHA_REVIEW = "exact_sha_review"
    CI_SECURITY_COMPLETION = "ci_security_completion"
    EXTERNAL_EFFECT_APPROVAL = "external_effect_approval"
    EFFECT_CLAIM = "effect_claim"
    EFFECT_EXECUTION_READBACK = "effect_execution_readback"
    TERMINAL_CLASSIFICATION = "terminal_classification"


class TerminalClassification(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REVISION_LIMIT = "REVISION_LIMIT"
    VISIT_LIMIT = "VISIT_LIMIT"
    EXACT_SHA_INVALID = "EXACT_SHA_INVALID"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class WorkflowRunStatus(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    TERMINAL = "TERMINAL"
    REJECTED = "REJECTED"
    ALREADY_APPLIED = "ALREADY_APPLIED"


class ApprovalKind(StrEnum):
    EXECUTION = "execution"
    EXTERNAL_EFFECT = "external_effect"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class AcceptanceDecision(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"


class EffectDisposition(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"


class EffectStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_STARTED = "NOT_STARTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ProviderDisposition(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"


class ProviderDispatchStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    CLAIMED = "CLAIMED"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def _require_digest(value: str, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must use sha256:<64 lowercase hex>")
    return value


def _require_sha(value: str, field: str) -> str:
    if not isinstance(value, str) or _SHA_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase 40-character Git SHA")
    return value


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a compact identifier")
    return value


def _require_actor_id(value: str, field: str) -> str:
    if not isinstance(value, str) or _ACTOR_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a compact lowercase logical actor ID")
    return value


def _require_code(value: str, field: str = "reason_code") -> str:
    if not isinstance(value, str) or _CODE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a compact lowercase code")
    return value


def _require_state(value: str, field: str) -> str:
    if not isinstance(value, str) or _STATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a compact uppercase status name")
    return value


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _checkpoint_id(config: Mapping[str, Any] | None) -> str | None:
    """Return the exact checkpoint a snapshot or a durable write refers to."""

    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    checkpoint_id = configurable.get("checkpoint_id")
    return str(checkpoint_id) if checkpoint_id is not None else None


def _freeze_audit_value(value: Any) -> Any:
    """Return a deeply immutable rebuild of one compact checkpoint value.

    Every container is rebuilt rather than wrapped, so the result shares no
    mutable object with the live checkpoint at any depth: lists become tuples,
    sets become frozensets, and mappings become read-only proxies over private
    dictionaries the caller can never reach.  A value of any other container
    kind is rejected rather than passed through, because a compact checkpoint is
    only ever allowed to hold identifiers, digests, counters, and booleans.
    """

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_audit_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_audit_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_audit_value(item) for item in value)
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value
    raise TypeError("compact checkpoint state carries a non-auditable value")


class ClaimStoreUnavailableError(ValueError):
    """No conditional claim store was supplied for a side-effecting transition."""


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    """Fence one side-effecting status transition within an attempt."""

    claim_key: str
    task_id: str
    field: str
    expected_state: str
    target_state: str
    attempt_digest: str
    owner_token: str

    def __post_init__(self) -> None:
        _require_digest(self.claim_key, "claim_key")
        _require_id(self.task_id, "task_id")
        _require_code(self.field, "field")
        _require_state(self.expected_state, "expected_state")
        _require_state(self.target_state, "target_state")
        _require_digest(self.attempt_digest, "attempt_digest")
        _require_id(self.owner_token, "owner_token")
        if self.expected_state == self.target_state:
            raise ValueError("a claim must change the fenced status")


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """A store's verdict for exactly one :class:`ClaimRequest`."""

    claim_key: str
    granted: bool
    owner_token: str
    state: str
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.claim_key, "claim_key")
        if type(self.granted) is not bool:
            raise ValueError("granted must be a boolean")
        _require_id(self.owner_token, "owner_token")
        _require_state(self.state, "state")
        _require_code(self.reason_code)


class ConditionalClaimStore(Protocol):
    """A durable store that fences side-effecting transitions atomically.

    What an implementation MUST guarantee, and what this module relies on:

    * ``claim`` is one atomic, already-committed durable operation.  For a given
      ``claim_key`` at most one call over the lifetime of the underlying storage
      may ever return ``granted=True`` -- including calls issued concurrently
      from other threads, processes, or hosts.  The canonical Postgres
      implementation is a single statement whose affected-row count decides the
      grant::

          INSERT INTO director_primary_claim
              (claim_key, task_id, field, expected_state, target_state,
               attempt_digest, owner_token)
          VALUES (%s, %s, %s, %s, %s, %s, %s)
          ON CONFLICT (claim_key) DO NOTHING

    * A refusal reports the ``owner_token`` and ``state`` that already hold the
      key, so a loser can be reconciled instead of retried.
    * ``claim_key`` is opaque: the store must derive no behaviour from it.

    What this protocol deliberately does NOT promise, so that callers do not
    assume it:

    * It is not a lease.  A grant never expires and there is no release, so an
      owner that dies after being granted a key leaves that key held forever.
      That is the intended fail-closed shape: a restart reconciles rather than
      issuing a second paid call.
    * It says nothing about the LangGraph checkpoint.  Recording the fenced
      transition durably remains this module's job, and a checkpoint write that
      cannot be confirmed is treated as a failure, never as a silent success.
    * It provides no ordering, fairness, or liveness guarantee between
      contenders.  The only invariant is single-winner exclusion.
    """

    def claim(self, request: ClaimRequest) -> ClaimOutcome:
        """Grant ``request`` its fence, or report which owner already holds it."""


class TaskWriterAuthority(Protocol):
    """Structural view of the durable control plane's current writer row."""

    task_id: str
    engine_owner: str
    owner_token: str
    fencing_generation: int


class BudgetBackedIntent(Protocol):
    """Structural view of one replay-safe, budget-backed intent reservation."""

    reservation_key: str
    claim_key: str
    task_id: str
    owner_token: str
    effect_key_digest: str
    effect_attempt_digest: str
    writer_owner_id: str
    reserved_units: int


@runtime_checkable
class DurablePrimaryControlPlane(ConditionalClaimStore, Protocol):
    """Production control-plane operations required before any handler I/O.

    ``PostgresConditionalClaimStore`` satisfies this protocol structurally.  It
    is intentionally defined here instead of importing the Postgres module,
    which imports the primary packet types and would create a cycle.
    """

    storage_identity: str

    def is_factory_bound_control_plane(self) -> bool:
        """Prove construction by the durable control plane's guarded factory."""

    def current_task_writer(self, task_id: str) -> TaskWriterAuthority | None:
        """Return the durable writer authority currently recorded for ``task_id``."""

    def register_task_budget(self, task_id: str, *, budget_limit_units: int) -> int:
        """Insert or verify the task's immutable budget ceiling."""

    def reserve_effect_intent(
        self,
        *,
        reservation_key: str,
        claim_key: str,
        task_id: str,
        owner_token: str,
        effect_key_digest: str,
        effect_attempt_digest: str,
        writer_owner_id: str,
        reserved_units: int,
    ) -> BudgetBackedIntent:
        """Atomically charge budget and insert or replay one exact intent."""


class InProcessConditionalClaimStore:
    """Insert-once fence that is atomic within exactly one process.

    Guaranteed: for one ``claim_key``, exactly one ``claim`` call in this
    process ever returns ``granted=True``.  The mutex spans the whole
    read-and-insert, so concurrent threads cannot both observe the key as free.

    NOT guaranteed: durability across a restart, and exclusion against any other
    process or host.  A sibling process holds its own empty table and would
    grant the same key again.  This implementation is therefore honest only for
    a strictly single-process deployment and for tests; a multi-process or
    Postgres-backed deployment must inject a store backed by the shared
    database, as :class:`ConditionalClaimStore` documents.
    """

    __slots__ = ("_lock", "_rows")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, ClaimRequest] = {}

    def claim(self, request: ClaimRequest) -> ClaimOutcome:
        if not isinstance(request, ClaimRequest):
            raise TypeError("claim requires a ClaimRequest")
        with self._lock:
            held = self._rows.setdefault(request.claim_key, request)
        granted = held is request
        return ClaimOutcome(
            claim_key=request.claim_key,
            granted=granted,
            owner_token=held.owner_token,
            state=held.target_state,
            reason_code="claim_granted" if granted else "claim_already_held",
        )

    def held_keys(self) -> tuple[str, ...]:
        """Return every fenced key, in insertion order, for audit and tests."""

        with self._lock:
            return tuple(self._rows)

    def held_claims(self, *, field: str | None = None) -> tuple[ClaimRequest, ...]:
        """Return the granted claims, optionally narrowed to one status field.

        One task normally holds several unrelated fences -- the provider
        dispatch and the external effect are separate transitions with separate
        keys -- so an audit that means "exactly one owner won the effect" has to
        say which field it is counting instead of counting the whole store.
        """

        with self._lock:
            granted = tuple(self._rows.values())
        if field is None:
            return granted
        return tuple(claim for claim in granted if claim.field == field)


@dataclass(frozen=True, slots=True)
class PhaseResult:
    digest: str
    passed: bool = True
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.digest, "digest")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a boolean")
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class PlanResult:
    """The validated plan and the exact work packet derived from that plan."""

    plan_digest: str
    work_packet_digest: str
    passed: bool = True
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.plan_digest, "plan_digest")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a boolean")
        _require_digest(self.work_packet_digest, "work_packet_digest")
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    schedule_digest: str
    producer_id: str
    passed: bool = True
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.schedule_digest, "schedule_digest")
        _require_actor_id(self.producer_id, "producer_id")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a boolean")
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ProviderSubgraphResult:
    provider_attempt_digest: str
    idempotency_key_digest: str
    authority_snapshot_digest: str
    producer_id: str
    disposition: ProviderDisposition
    result_digest: str | None = None
    candidate_sha: str | None = None
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.provider_attempt_digest, "provider_attempt_digest")
        _require_digest(self.idempotency_key_digest, "idempotency_key_digest")
        _require_digest(self.authority_snapshot_digest, "authority_snapshot_digest")
        _require_actor_id(self.producer_id, "producer_id")
        object.__setattr__(self, "disposition", ProviderDisposition(self.disposition))
        if self.result_digest is not None:
            _require_digest(self.result_digest, "result_digest")
        if self.candidate_sha is not None:
            _require_sha(self.candidate_sha, "candidate_sha")
        if self.disposition == ProviderDisposition.COMPLETED and (
            self.result_digest is None or self.candidate_sha is None
        ):
            raise ValueError(
                "completed provider results require result_digest and candidate_sha"
            )
        if self.disposition != ProviderDisposition.COMPLETED and self.candidate_sha is not None:
            raise ValueError("only completed provider results may contain candidate_sha")
        _require_code(self.reason_code)

    def as_resume_payload(self) -> dict[str, Any]:
        return {
            "provider_attempt_digest": self.provider_attempt_digest,
            "idempotency_key_digest": self.idempotency_key_digest,
            "authority_snapshot_digest": self.authority_snapshot_digest,
            "producer_id": self.producer_id,
            "disposition": self.disposition.value,
            "result_digest": self.result_digest,
            "candidate_sha": self.candidate_sha,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class EvidenceDebateResult:
    evidence_digest: str
    debate_digest: str
    subject_sha: str
    passed: bool = True
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.evidence_digest, "evidence_digest")
        _require_digest(self.debate_digest, "debate_digest")
        _require_sha(self.subject_sha, "subject_sha")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a boolean")
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    acceptance_digest: str
    subject_sha: str
    decision: AcceptanceDecision
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.acceptance_digest, "acceptance_digest")
        _require_sha(self.subject_sha, "subject_sha")
        object.__setattr__(self, "decision", AcceptanceDecision(self.decision))
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class ExactShaReviewResult:
    review_digest: str
    reviewed_sha: str
    producer_id: str
    reviewer_id: str
    accepted: bool = True
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.review_digest, "review_digest")
        _require_sha(self.reviewed_sha, "reviewed_sha")
        _require_actor_id(self.producer_id, "producer_id")
        _require_actor_id(self.reviewer_id, "reviewer_id")
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be a boolean")
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class CompletionGateResult:
    ci_digest: str
    security_digest: str
    completion_digest: str
    subject_sha: str
    passed: bool = True
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.ci_digest, "ci_digest")
        _require_digest(self.security_digest, "security_digest")
        _require_digest(self.completion_digest, "completion_digest")
        _require_sha(self.subject_sha, "subject_sha")
        if type(self.passed) is not bool:
            raise ValueError("passed must be a boolean")
        _require_code(self.reason_code)


@dataclass(frozen=True, slots=True)
class EffectExecutionResult:
    effect_key_digest: str
    effect_attempt_digest: str
    writer_owner_id: str
    disposition: EffectDisposition
    result_digest: str | None = None
    readback_digest: str | None = None
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        _require_digest(self.effect_key_digest, "effect_key_digest")
        _require_digest(self.effect_attempt_digest, "effect_attempt_digest")
        _require_id(self.writer_owner_id, "writer_owner_id")
        object.__setattr__(self, "disposition", EffectDisposition(self.disposition))
        _require_code(self.reason_code)
        if self.result_digest is not None:
            _require_digest(self.result_digest, "result_digest")
        if self.readback_digest is not None:
            _require_digest(self.readback_digest, "readback_digest")
        if self.disposition == EffectDisposition.COMPLETED and (
            self.result_digest is None or self.readback_digest is None
        ):
            raise ValueError("completed effect results require result and readback digests")
        if self.disposition != EffectDisposition.COMPLETED and self.readback_digest is not None:
            raise ValueError("only completed effects may contain readback evidence")

    def as_resume_payload(self) -> dict[str, Any]:
        return {
            "effect_key_digest": self.effect_key_digest,
            "effect_attempt_digest": self.effect_attempt_digest,
            "writer_owner_id": self.writer_owner_id,
            "disposition": self.disposition.value,
            "result_digest": self.result_digest,
            "readback_digest": self.readback_digest,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Allowlisted compact context exposed to pure phase handlers."""

    task_id: str
    source_event_id: str
    correlation_id: str
    intake_digest: str
    scope_digest: str
    authority_snapshot_digest: str
    work_packet_digest: str | None
    idempotency_key_digest: str
    starting_sha: str
    candidate_sha: str
    plan_digest: str | None
    scheduled_producer_id: str | None
    provider_attempt_digest: str | None
    provider_dispatch_status: str
    provider_result_digest: str | None
    producer_id: str | None
    reviewer_id: str | None
    revision_count: int
    budget_limit_units: int
    budget_used_units: int
    effect_key_digest: str | None
    effect_attempt_digest: str | None
    writer_owner_id: str


DigestHandler = Callable[[HandlerContext], PhaseResult]
PlanHandler = Callable[[HandlerContext], PlanResult]
ScheduleHandler = Callable[[HandlerContext], ScheduleResult]
ProviderHandler = Callable[[HandlerContext], ProviderSubgraphResult]
EvidenceHandler = Callable[[HandlerContext], EvidenceDebateResult]
AcceptanceHandler = Callable[[HandlerContext], AcceptanceResult]
ReviewHandler = Callable[[HandlerContext], ExactShaReviewResult]
CompletionHandler = Callable[[HandlerContext], CompletionGateResult]
EffectHandler = Callable[[HandlerContext], EffectExecutionResult]


@dataclass(frozen=True, slots=True)
class PrimaryHandlers:
    """Pure, injected phase boundaries; the graph supplies no transport defaults."""

    authenticate_scope: DigestHandler
    canonicalize: DigestHandler
    plan: PlanHandler
    policy_budget: DigestHandler
    schedule: ScheduleHandler
    provider_subgraph: ProviderHandler
    evidence_debate: EvidenceHandler
    openai_acceptance_revision: AcceptanceHandler
    exact_sha_review: ReviewHandler
    ci_security_completion: CompletionHandler
    effect_execution_readback: EffectHandler


@dataclass(frozen=True, slots=True)
class PrimaryRequest:
    """Small initial state; content and transcripts remain digest-addressed artifacts."""

    task_id: str
    source_event_id: str
    correlation_id: str
    intake_digest: str
    scope_digest: str
    authority_snapshot_digest: str
    # Compatibility-only pre-plan input. It is replaced by PlanResult before
    # any downstream phase is allowed to run.
    work_packet_digest: str
    idempotency_key_digest: str
    starting_sha: str
    dedup_payload_digest: str | None = None
    writer_owner_id: str = "langgraph-primary"
    budget_limit_units: int = 1
    provider_cost_units: int = 1
    effect_cost_units: int = 0
    max_visits: int = 64
    max_revisions: int = 1
    execution_approval_required: bool = True
    external_effect_required: bool = False
    effect_key_digest: str | None = None
    graph_writer_active: bool = False
    legacy_writer_active: bool = True
    cancel_requested: bool = False
    timeout_expired: bool = False

    def __post_init__(self) -> None:
        _require_id(self.task_id, "task_id")
        _require_id(self.source_event_id, "source_event_id")
        _require_id(self.correlation_id, "correlation_id")
        _require_digest(self.intake_digest, "intake_digest")
        _require_digest(self.scope_digest, "scope_digest")
        _require_digest(self.authority_snapshot_digest, "authority_snapshot_digest")
        _require_digest(self.work_packet_digest, "work_packet_digest")
        _require_digest(self.idempotency_key_digest, "idempotency_key_digest")
        _require_sha(self.starting_sha, "starting_sha")
        if self.dedup_payload_digest is not None:
            _require_digest(self.dedup_payload_digest, "dedup_payload_digest")
        _require_id(self.writer_owner_id, "writer_owner_id")
        for field in (
            "budget_limit_units",
            "provider_cost_units",
            "effect_cost_units",
            "max_visits",
            "max_revisions",
        ):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if not 1 <= self.max_visits <= 256:
            raise ValueError("max_visits must be between 1 and 256")
        if self.max_revisions > 20:
            raise ValueError("max_revisions must not exceed 20")
        for field in (
            "execution_approval_required",
            "external_effect_required",
            "graph_writer_active",
            "legacy_writer_active",
            "cancel_requested",
            "timeout_expired",
        ):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} must be a boolean")
        if self.external_effect_required != (self.effect_key_digest is not None):
            raise ValueError(
                "effect_key_digest is required exactly when an external effect is required"
            )
        if self.effect_key_digest is not None:
            _require_digest(self.effect_key_digest, "effect_key_digest")

    def binding_payload(self) -> dict[str, Any]:
        return {
            "schema_version": PRIMARY_SCHEMA_VERSION,
            "task_id": self.task_id,
            "source_event_id": self.source_event_id,
            "correlation_id": self.correlation_id,
            "intake_digest": self.intake_digest,
            "scope_digest": self.scope_digest,
            "authority_snapshot_digest": self.authority_snapshot_digest,
            "work_packet_digest": self.work_packet_digest,
            "idempotency_key_digest": self.idempotency_key_digest,
            "starting_sha": self.starting_sha,
            "dedup_payload_digest": self.dedup_payload_digest,
            "writer_owner_id": self.writer_owner_id,
            "budget_limit_units": self.budget_limit_units,
            "provider_cost_units": self.provider_cost_units,
            "effect_cost_units": self.effect_cost_units,
            "max_visits": self.max_visits,
            "max_revisions": self.max_revisions,
            "execution_approval_required": self.execution_approval_required,
            "external_effect_required": self.external_effect_required,
            "effect_key_digest": self.effect_key_digest,
            "graph_writer_active": self.graph_writer_active,
            "legacy_writer_active": self.legacy_writer_active,
            "cancel_requested": self.cancel_requested,
            "timeout_expired": self.timeout_expired,
        }

    @property
    def input_digest(self) -> str:
        return _canonical_digest(self.binding_payload())

    def graph_input(self) -> "PrimaryGraphState":
        state: PrimaryGraphState = dict(self.binding_payload())  # type: ignore[assignment]
        state.update(
            {
                "input_digest": self.input_digest,
                "candidate_sha": self.starting_sha,
                "plan_work_packet_binding_digest": None,
                "scheduled_producer_id": None,
                "parallel_plan_digest": None,
                "parallel_aggregate_digest": None,
                "parallel_terminal": False,
                "parallel_succeeded": False,
                "parallel_ambiguous_slot_count": 0,
                "provider_dispatch_status": ProviderDispatchStatus.NOT_STARTED.value,
                "provider_attempt_digest": None,
                "provider_result_digest": None,
                "provider_result_receipt_digest": None,
                "producer_id": None,
                "review_producer_id": None,
                "reviewer_id": None,
                "review_binding_digest": None,
                "budget_used_units": 0,
                "revision_count": 0,
                "visit_count": 0,
                "visited_nodes": [],
                "current_phase": "start",
                "previous_phase": None,
                "transition_reason_code": "start",
                "pending_approval_kind": None,
                "pending_approval_request_digest": None,
                "execution_approval_receipt_digest": None,
                "effect_approval_receipt_digest": None,
                "effect_status": (
                    EffectStatus.NOT_STARTED.value
                    if self.external_effect_required
                    else EffectStatus.NOT_REQUIRED.value
                ),
                "effect_result_digest": None,
                "effect_result_receipt_digest": None,
                "effect_readback_digest": None,
                "terminal_classification": None,
            }
        )
        return state


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    task_id: str
    kind: ApprovalKind
    request_digest: str


@dataclass(frozen=True, slots=True)
class ApprovalResume:
    task_id: str
    kind: ApprovalKind
    request_digest: str
    approval_event_id: str
    decision: ApprovalDecision = ApprovalDecision.APPROVE

    def __post_init__(self) -> None:
        _require_id(self.task_id, "task_id")
        object.__setattr__(self, "kind", ApprovalKind(self.kind))
        _require_digest(self.request_digest, "request_digest")
        _require_id(self.approval_event_id, "approval_event_id")
        object.__setattr__(self, "decision", ApprovalDecision(self.decision))

    def as_resume_payload(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "request_digest": self.request_digest,
            "approval_event_id": self.approval_event_id,
            "decision": self.decision.value,
        }

    @property
    def receipt_digest(self) -> str:
        return _canonical_digest(self.as_resume_payload())


@dataclass(frozen=True, slots=True)
class ProviderDispatchIntent:
    """Durably checkpointed provider intent passed to an idempotent adapter."""

    task_id: str
    provider_attempt_digest: str
    idempotency_key_digest: str
    authority_snapshot_digest: str
    producer_id: str
    revision_count: int

    def __post_init__(self) -> None:
        _require_id(self.task_id, "task_id")
        _require_digest(self.provider_attempt_digest, "provider_attempt_digest")
        _require_digest(self.idempotency_key_digest, "idempotency_key_digest")
        _require_digest(self.authority_snapshot_digest, "authority_snapshot_digest")
        _require_actor_id(self.producer_id, "producer_id")
        if type(self.revision_count) is not int or self.revision_count < 0:
            raise ValueError("revision_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class PrimaryOutcome:
    task_id: str
    run_status: WorkflowRunStatus
    phase: str
    terminal_classification: TerminalClassification | None
    reason_code: str
    pending_approval: ApprovalRequest | None
    provider_dispatch_status: ProviderDispatchStatus
    effect_status: EffectStatus
    state_digest: str
    visit_count: int
    visited_nodes: tuple[str, ...]
    revision_count: int
    budget_used_units: int
    parallel_plan_digest: str | None = None
    parallel_aggregate_digest: str | None = None
    parallel_terminal: bool = False
    parallel_succeeded: bool = False
    parallel_ambiguous_slot_count: int = 0


class PrimaryGraphState(TypedDict, total=False):
    """Checkpoint-safe state containing no secrets, prompts, or transcripts."""

    schema_version: str
    task_id: str
    source_event_id: str
    correlation_id: str
    intake_digest: str
    scope_digest: str
    authority_snapshot_digest: str
    work_packet_digest: str | None
    idempotency_key_digest: str
    input_digest: str
    starting_sha: str
    dedup_payload_digest: str | None
    candidate_sha: str
    writer_owner_id: str
    budget_limit_units: int
    provider_cost_units: int
    effect_cost_units: int
    budget_used_units: int
    max_visits: int
    max_revisions: int
    revision_count: int
    execution_approval_required: bool
    external_effect_required: bool
    effect_key_digest: str | None
    graph_writer_active: bool
    legacy_writer_active: bool
    cancel_requested: bool
    timeout_expired: bool
    authenticate_scope_digest: str | None
    canonical_digest: str | None
    plan_digest: str | None
    plan_work_packet_binding_digest: str | None
    policy_digest: str | None
    schedule_digest: str | None
    parallel_plan_digest: str | None
    parallel_aggregate_digest: str | None
    parallel_terminal: bool
    parallel_succeeded: bool
    parallel_ambiguous_slot_count: int
    scheduled_producer_id: str | None
    provider_dispatch_status: str
    provider_attempt_digest: str | None
    provider_result_digest: str | None
    provider_result_receipt_digest: str | None
    producer_id: str | None
    evidence_digest: str | None
    debate_digest: str | None
    acceptance_digest: str | None
    acceptance_decision: str | None
    review_digest: str | None
    reviewed_sha: str | None
    review_producer_id: str | None
    reviewer_id: str | None
    review_binding_digest: str | None
    ci_digest: str | None
    security_digest: str | None
    completion_digest: str | None
    pending_approval_kind: str | None
    pending_approval_request_digest: str | None
    execution_approval_receipt_digest: str | None
    effect_approval_receipt_digest: str | None
    effect_status: str
    effect_attempt_digest: str | None
    effect_result_digest: str | None
    effect_result_receipt_digest: str | None
    effect_readback_digest: str | None
    current_phase: str
    previous_phase: str | None
    transition_reason_code: str
    terminal_classification: str | None
    visit_count: int
    visited_nodes: list[str]


_REQUEST_FIELDS = tuple(PrimaryRequest.__dataclass_fields__)


def _plan_work_packet_binding(plan_digest: str, work_packet_digest: str) -> str:
    return _canonical_digest(
        {
            "schema_version": PRIMARY_SCHEMA_VERSION,
            "plan_digest": plan_digest,
            "work_packet_digest": work_packet_digest,
        }
    )


def _post_plan_work_packet_digest(state: Mapping[str, Any]) -> str | None:
    """Return packet authority only when its post-plan binding is intact."""

    try:
        plan_digest = _require_digest(
            str(state.get("plan_digest") or ""), "plan_digest"
        )
        work_packet_digest = _require_digest(
            str(state.get("work_packet_digest") or ""), "work_packet_digest"
        )
        binding_digest = _require_digest(
            str(state.get("plan_work_packet_binding_digest") or ""),
            "plan_work_packet_binding_digest",
        )
    except ValueError:
        return None
    expected = _plan_work_packet_binding(plan_digest, work_packet_digest)
    return work_packet_digest if _secure_equal(binding_digest, expected) else None


def _context(state: Mapping[str, Any]) -> HandlerContext:
    return HandlerContext(
        task_id=state["task_id"],
        source_event_id=state["source_event_id"],
        correlation_id=state["correlation_id"],
        intake_digest=state["intake_digest"],
        scope_digest=state["scope_digest"],
        authority_snapshot_digest=state["authority_snapshot_digest"],
        work_packet_digest=_post_plan_work_packet_digest(state),
        idempotency_key_digest=state["idempotency_key_digest"],
        starting_sha=state["starting_sha"],
        candidate_sha=state["candidate_sha"],
        plan_digest=state.get("plan_digest"),
        scheduled_producer_id=state.get("scheduled_producer_id"),
        provider_attempt_digest=state.get("provider_attempt_digest"),
        provider_dispatch_status=str(state["provider_dispatch_status"]),
        provider_result_digest=state.get("provider_result_digest"),
        producer_id=state.get("producer_id"),
        reviewer_id=state.get("reviewer_id"),
        revision_count=state["revision_count"],
        budget_limit_units=state["budget_limit_units"],
        budget_used_units=state["budget_used_units"],
        effect_key_digest=state.get("effect_key_digest"),
        effect_attempt_digest=state.get("effect_attempt_digest"),
        writer_owner_id=state["writer_owner_id"],
    )


_POST_PLAN_PHASES = frozenset(
    phase
    for phase in PrimaryPhase
    if phase
    not in {
        PrimaryPhase.AUTHENTICATE_SCOPE,
        PrimaryPhase.CANONICALIZE,
        PrimaryPhase.PLAN,
        PrimaryPhase.TERMINAL_CLASSIFICATION,
    }
)


def _validate_initial_state(state: PrimaryGraphState) -> PrimaryGraphState:
    if state.get("schema_version") != PRIMARY_SCHEMA_VERSION:
        raise ValueError("unsupported primary graph schema version")
    payload = {field: state.get(field) for field in _REQUEST_FIELDS}
    request = PrimaryRequest(**payload)
    supplied_digest = _require_digest(str(state.get("input_digest") or ""), "input_digest")
    if not _secure_equal(supplied_digest, request.input_digest):
        raise ValueError("input_digest does not match the bounded request")
    return _entered_update(state, PrimaryPhase.AUTHENTICATE_SCOPE)[0]


def _entered_update(
    state: Mapping[str, Any], phase: PrimaryPhase
) -> tuple[PrimaryGraphState, bool]:
    prior_visit_count = int(state.get("visit_count", 0))
    prior = state.get("visited_nodes", [])
    if not isinstance(prior, list) or any(not isinstance(item, str) for item in prior):
        raise ValueError("visited_nodes must contain compact phase names")
    if prior_visit_count >= int(state["max_visits"]):
        return (
            {
                "previous_phase": str(state.get("current_phase") or "start"),
                "current_phase": phase.value,
                "transition_reason_code": "visit_limit",
                "visit_count": prior_visit_count,
                "visited_nodes": list(prior)[-MAX_RETAINED_VISITS:],
                "terminal_classification": TerminalClassification.VISIT_LIMIT.value,
            },
            False,
        )
    visit_count = prior_visit_count + 1
    update: PrimaryGraphState = {
        "previous_phase": str(state.get("current_phase") or "start"),
        "current_phase": phase.value,
        "transition_reason_code": "phase_entered",
        "visit_count": visit_count,
        "visited_nodes": [*prior, phase.value][-MAX_RETAINED_VISITS:],
    }
    if phase in _POST_PLAN_PHASES and _post_plan_work_packet_digest(state) is None:
        update.update(
            terminal_classification=TerminalClassification.POLICY_BLOCKED.value,
            transition_reason_code="work_packet_authority_invalid",
        )
        return update, False
    if bool(state.get("cancel_requested")):
        update.update(
            terminal_classification=TerminalClassification.CANCELLED.value,
            transition_reason_code="cancel_requested",
        )
        return update, False
    if bool(state.get("timeout_expired")):
        update.update(
            terminal_classification=TerminalClassification.TIMED_OUT.value,
            transition_reason_code="timeout_expired",
        )
        return update, False
    return update, True


def _handler_failure(
    update: PrimaryGraphState,
    *,
    classification: TerminalClassification = TerminalClassification.FAILED,
    reason_code: str,
) -> PrimaryGraphState:
    update.update(
        terminal_classification=classification.value,
        transition_reason_code=_require_code(reason_code),
    )
    return update


def _call_phase_handler(
    state: PrimaryGraphState,
    update: PrimaryGraphState,
    handler: DigestHandler,
    *,
    digest_field: str,
    failure_classification: TerminalClassification = TerminalClassification.FAILED,
    error_code: str,
) -> PrimaryGraphState:
    try:
        result = handler(_context(state))
        if not isinstance(result, PhaseResult):
            raise TypeError("handler returned the wrong result type")
    except Exception:
        return _handler_failure(update, reason_code=error_code)
    update[digest_field] = result.digest  # type: ignore[literal-required]
    update["transition_reason_code"] = result.reason_code
    if not result.passed:
        return _handler_failure(
            update,
            classification=failure_classification,
            reason_code=result.reason_code,
        )
    return update


def _approval_request_digest(state: Mapping[str, Any], kind: ApprovalKind) -> str:
    payload: dict[str, Any] = {
        "schema_version": PRIMARY_SCHEMA_VERSION,
        "task_id": state["task_id"],
        "kind": kind.value,
        "authority_snapshot_digest": state["authority_snapshot_digest"],
        "candidate_sha": state["candidate_sha"],
        "plan_digest": state.get("plan_digest"),
        "work_packet_digest": _post_plan_work_packet_digest(state),
        "policy_digest": state.get("policy_digest"),
        "budget_limit_units": state["budget_limit_units"],
        "max_revisions": state["max_revisions"],
    }
    if kind == ApprovalKind.EXTERNAL_EFFECT:
        payload.update(
            completion_digest=state.get("completion_digest"),
            effect_key_digest=state.get("effect_key_digest"),
            writer_owner_id=state["writer_owner_id"],
        )
    return _canonical_digest(payload)


def _validate_approval_payload(
    payload: Any,
    state: Mapping[str, Any],
    expected_kind: ApprovalKind,
) -> ApprovalResume:
    if not isinstance(payload, Mapping):
        raise ValueError("approval resume must be a bounded mapping")
    expected_fields = {
        "task_id",
        "kind",
        "request_digest",
        "approval_event_id",
        "decision",
    }
    if set(payload) != expected_fields:
        raise ValueError("approval resume fields do not match the v1 contract")
    resume = ApprovalResume(
        task_id=str(payload["task_id"]),
        kind=ApprovalKind(str(payload["kind"])),
        request_digest=str(payload["request_digest"]),
        approval_event_id=str(payload["approval_event_id"]),
        decision=ApprovalDecision(str(payload["decision"])),
    )
    if resume.task_id != state["task_id"] or resume.kind != expected_kind:
        raise ValueError("approval resume is bound to a different task or gate")
    pending = str(state.get("pending_approval_request_digest") or "")
    if not _secure_equal(resume.request_digest, pending):
        raise ValueError("approval resume is stale")
    return resume


def _approval_payload_failure(update: PrimaryGraphState) -> PrimaryGraphState:
    """Fail closed on a consumed approval that no receipt may be issued for."""

    update.update(
        pending_approval_kind=None,
        pending_approval_request_digest=None,
    )
    return _handler_failure(
        update,
        classification=TerminalClassification.OWNER_DECISION_REQUIRED,
        reason_code="approval_payload_invalid",
    )


def _build_nodes(handlers: PrimaryHandlers) -> dict[str, Callable[..., PrimaryGraphState]]:
    def authenticate_scope(state: PrimaryGraphState) -> PrimaryGraphState:
        update = _validate_initial_state(state)
        if update.get("terminal_classification"):
            return update
        return _call_phase_handler(
            state,
            update,
            handlers.authenticate_scope,
            digest_field="authenticate_scope_digest",
            error_code="authenticate_handler_error",
        )

    def canonicalize(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.CANONICALIZE)
        if not proceed:
            return update
        return _call_phase_handler(
            state,
            update,
            handlers.canonicalize,
            digest_field="canonical_digest",
            error_code="canonicalize_handler_error",
        )

    def plan(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.PLAN)
        if not proceed:
            return update
        try:
            result = handlers.plan(_context(state))
            if not isinstance(result, PlanResult):
                raise TypeError("plan handler returned the wrong result type")
        except Exception:
            return _handler_failure(update, reason_code="plan_handler_error")
        update.update(
            plan_digest=result.plan_digest,
            work_packet_digest=result.work_packet_digest,
            plan_work_packet_binding_digest=_plan_work_packet_binding(
                result.plan_digest, result.work_packet_digest
            ),
            transition_reason_code=result.reason_code,
        )
        if not result.passed:
            return _handler_failure(update, reason_code=result.reason_code)
        return update

    def policy_budget(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.POLICY_BUDGET)
        if not proceed:
            return update
        if state["external_effect_required"] and state["graph_writer_active"] and state["legacy_writer_active"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.OWNER_DECISION_REQUIRED,
                reason_code="dual_writer",
            )
        if state["budget_used_units"] + state["provider_cost_units"] > state["budget_limit_units"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.BUDGET_EXHAUSTED,
                reason_code="provider_budget",
            )
        update = _call_phase_handler(
            state,
            update,
            handlers.policy_budget,
            digest_field="policy_digest",
            failure_classification=TerminalClassification.POLICY_BLOCKED,
            error_code="policy_handler_error",
        )
        if update.get("terminal_classification"):
            return update
        if state["execution_approval_required"]:
            update.update(
                pending_approval_kind=ApprovalKind.EXECUTION.value,
                pending_approval_request_digest=_approval_request_digest(
                    {**state, **update}, ApprovalKind.EXECUTION
                ),
                transition_reason_code="execution_approval_required",
            )
        return update

    def approval_interrupt(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.APPROVAL_INTERRUPT)
        if not proceed:
            return update
        payload = interrupt(
            {
                "schema_version": PRIMARY_SCHEMA_VERSION,
                "type": "approval_required",
                "task_id": state["task_id"],
                "kind": ApprovalKind.EXECUTION.value,
                "request_digest": state["pending_approval_request_digest"],
            }
        )
        try:
            resume = _validate_approval_payload(
                payload, state, ApprovalKind.EXECUTION
            )
        except Exception:
            return _approval_payload_failure(update)
        update.update(
            pending_approval_kind=None,
            pending_approval_request_digest=None,
            execution_approval_receipt_digest=resume.receipt_digest,
            transition_reason_code="execution_approved",
        )
        if resume.decision == ApprovalDecision.DENY:
            return _handler_failure(
                update,
                classification=TerminalClassification.OWNER_DECISION_REQUIRED,
                reason_code="execution_approval_denied",
            )
        return update

    def schedule(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.SCHEDULE)
        if not proceed:
            return update
        try:
            result = handlers.schedule(_context(state))
            if not isinstance(result, ScheduleResult):
                raise TypeError("schedule handler returned the wrong result type")
        except Exception:
            return _handler_failure(update, reason_code="schedule_handler_error")
        update.update(
            schedule_digest=result.schedule_digest,
            scheduled_producer_id=result.producer_id,
            transition_reason_code=result.reason_code,
        )
        if not result.passed:
            return _handler_failure(update, reason_code=result.reason_code)
        return update

    def provider_dispatch_claim(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(
            state, PrimaryPhase.PROVIDER_DISPATCH_CLAIM
        )
        if not proceed:
            return update
        if state["budget_used_units"] + state["provider_cost_units"] > state["budget_limit_units"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.BUDGET_EXHAUSTED,
                reason_code="provider_budget",
            )
        prior_status = ProviderDispatchStatus(state["provider_dispatch_status"])
        if prior_status not in {
            ProviderDispatchStatus.NOT_STARTED,
            ProviderDispatchStatus.COMPLETED,
        }:
            update.update(
                provider_dispatch_status=ProviderDispatchStatus.RECONCILIATION_REQUIRED.value
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="provider_claim_replay",
            )
        producer_id = str(state.get("scheduled_producer_id") or "")
        try:
            _require_actor_id(producer_id, "scheduled_producer_id")
        except ValueError:
            return _handler_failure(update, reason_code="provider_route_missing")
        attempt_digest = _canonical_digest(
            {
                "schema_version": PRIMARY_SCHEMA_VERSION,
                "task_id": state["task_id"],
                "idempotency_key_digest": state["idempotency_key_digest"],
                "authority_snapshot_digest": state["authority_snapshot_digest"],
                "work_packet_digest": state["work_packet_digest"],
                "plan_digest": state["plan_digest"],
                "schedule_digest": state["schedule_digest"],
                "producer_id": producer_id,
                "revision_count": state["revision_count"],
                "prior_acceptance_digest": state.get("acceptance_digest"),
            }
        )
        update.update(
            provider_dispatch_status=ProviderDispatchStatus.CLAIMED.value,
            provider_attempt_digest=attempt_digest,
            budget_used_units=state["budget_used_units"] + state["provider_cost_units"],
            provider_result_digest=None,
            provider_result_receipt_digest=None,
            producer_id=None,
            evidence_digest=None,
            debate_digest=None,
            acceptance_digest=None,
            acceptance_decision=None,
            review_digest=None,
            reviewed_sha=None,
            review_producer_id=None,
            reviewer_id=None,
            review_binding_digest=None,
            ci_digest=None,
            security_digest=None,
            completion_digest=None,
            effect_result_digest=None,
            effect_result_receipt_digest=None,
            effect_readback_digest=None,
            transition_reason_code="provider_dispatch_claimed",
        )
        return update

    def provider_subgraph(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.PROVIDER_SUBGRAPH)
        if not proceed:
            if (
                state["provider_dispatch_status"]
                == ProviderDispatchStatus.DISPATCHING.value
            ):
                update.update(
                    provider_dispatch_status=ProviderDispatchStatus.RECONCILIATION_REQUIRED.value
                )
                return _handler_failure(
                    update,
                    classification=TerminalClassification.RECONCILIATION_REQUIRED,
                    reason_code="provider_dispatch_in_flight",
                )
            return update
        result_payload = interrupt(
            {
                "schema_version": PRIMARY_SCHEMA_VERSION,
                "type": "provider_result_required",
                "task_id": state["task_id"],
                "provider_attempt_digest": state["provider_attempt_digest"],
                "idempotency_key_digest": state["idempotency_key_digest"],
                "authority_snapshot_digest": state["authority_snapshot_digest"],
                "producer_id": state["scheduled_producer_id"],
                "revision_count": state["revision_count"],
            }
        )
        if not isinstance(result_payload, Mapping):
            update.update(
                provider_dispatch_status=ProviderDispatchStatus.RECONCILIATION_REQUIRED.value
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="provider_result_invalid",
            )
        try:
            if set(result_payload) != {
                "provider_attempt_digest",
                "idempotency_key_digest",
                "authority_snapshot_digest",
                "producer_id",
                "disposition",
                "result_digest",
                "candidate_sha",
                "reason_code",
            }:
                raise ValueError("provider result fields do not match the v1 contract")
            result = ProviderSubgraphResult(
                provider_attempt_digest=str(
                    result_payload["provider_attempt_digest"]
                ),
                idempotency_key_digest=str(
                    result_payload["idempotency_key_digest"]
                ),
                authority_snapshot_digest=str(
                    result_payload["authority_snapshot_digest"]
                ),
                producer_id=str(result_payload["producer_id"]),
                disposition=ProviderDisposition(str(result_payload["disposition"])),
                result_digest=(
                    str(result_payload["result_digest"])
                    if result_payload.get("result_digest") is not None
                    else None
                ),
                candidate_sha=(
                    str(result_payload["candidate_sha"])
                    if result_payload.get("candidate_sha") is not None
                    else None
                ),
                reason_code=str(result_payload["reason_code"]),
            )
        except Exception:
            update.update(
                provider_dispatch_status=ProviderDispatchStatus.RECONCILIATION_REQUIRED.value
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="provider_result_invalid",
            )
        if (
            not _secure_equal(
                result.provider_attempt_digest,
                str(state["provider_attempt_digest"]),
            )
            or not _secure_equal(
                result.idempotency_key_digest,
                state["idempotency_key_digest"],
            )
            or not _secure_equal(
                result.authority_snapshot_digest,
                state["authority_snapshot_digest"],
            )
            or result.producer_id != state["scheduled_producer_id"]
        ):
            update.update(
                provider_dispatch_status=ProviderDispatchStatus.RECONCILIATION_REQUIRED.value
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="provider_binding_mismatch",
            )
        result_receipt_digest = _canonical_digest(result.as_resume_payload())
        if result.disposition == ProviderDisposition.AMBIGUOUS:
            update.update(
                provider_dispatch_status=ProviderDispatchStatus.RECONCILIATION_REQUIRED.value,
                provider_result_receipt_digest=result_receipt_digest,
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code=result.reason_code,
            )
        if result.disposition == ProviderDisposition.FAILED:
            update.update(
                provider_dispatch_status=ProviderDispatchStatus.FAILED.value,
                provider_result_digest=result.result_digest,
                provider_result_receipt_digest=result_receipt_digest,
                producer_id=result.producer_id,
            )
            return _handler_failure(update, reason_code=result.reason_code)
        update.update(
            provider_result_digest=result.result_digest,
            provider_result_receipt_digest=result_receipt_digest,
            candidate_sha=result.candidate_sha,
            producer_id=result.producer_id,
            provider_dispatch_status=ProviderDispatchStatus.COMPLETED.value,
            transition_reason_code=result.reason_code,
        )
        return update

    def evidence_debate(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.EVIDENCE_DEBATE)
        if not proceed:
            return update
        try:
            result = handlers.evidence_debate(_context(state))
            if not isinstance(result, EvidenceDebateResult):
                raise TypeError("evidence handler returned the wrong result type")
        except Exception:
            return _handler_failure(update, reason_code="evidence_handler_error")
        if not _secure_equal(result.subject_sha, state["candidate_sha"]):
            return _handler_failure(
                update,
                classification=TerminalClassification.EXACT_SHA_INVALID,
                reason_code="stale_evidence_sha",
            )
        update.update(
            evidence_digest=result.evidence_digest,
            debate_digest=result.debate_digest,
            transition_reason_code=result.reason_code,
        )
        if not result.passed:
            return _handler_failure(
                update,
                classification=(
                    TerminalClassification.OWNER_DECISION_REQUIRED
                    if result.reason_code in {
                        "owner_decision_required",
                        "durable_completion_evidence_unavailable",
                    }
                    else TerminalClassification.FAILED
                ),
                reason_code=result.reason_code,
            )
        return update

    def openai_acceptance_revision(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(
            state, PrimaryPhase.OPENAI_ACCEPTANCE_REVISION
        )
        if not proceed:
            return update
        try:
            result = handlers.openai_acceptance_revision(_context(state))
            if not isinstance(result, AcceptanceResult):
                raise TypeError("acceptance handler returned the wrong result type")
        except Exception:
            return _handler_failure(update, reason_code="acceptance_handler_error")
        if not _secure_equal(result.subject_sha, state["candidate_sha"]):
            return _handler_failure(
                update,
                classification=TerminalClassification.EXACT_SHA_INVALID,
                reason_code="stale_acceptance_sha",
            )
        update.update(
            acceptance_digest=result.acceptance_digest,
            acceptance_decision=result.decision.value,
            transition_reason_code=result.reason_code,
        )
        if result.decision == AcceptanceDecision.REJECT:
            return _handler_failure(
                update,
                classification=(
                    TerminalClassification.OWNER_DECISION_REQUIRED
                    if result.reason_code in {
                        "owner_decision_required",
                        "durable_acceptance_evidence_unavailable",
                    }
                    else TerminalClassification.FAILED
                ),
                reason_code=(
                    result.reason_code
                    if result.reason_code != "ok"
                    else "acceptance_rejected"
                ),
            )
        if result.decision == AcceptanceDecision.REVISE:
            if state["revision_count"] >= state["max_revisions"]:
                return _handler_failure(
                    update,
                    classification=TerminalClassification.REVISION_LIMIT,
                    reason_code="revision_limit",
                )
            update.update(
                revision_count=state["revision_count"] + 1,
                transition_reason_code="revision_required",
            )
        return update

    def exact_sha_review(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.EXACT_SHA_REVIEW)
        if not proceed:
            return update
        try:
            result = handlers.exact_sha_review(_context(state))
            if not isinstance(result, ExactShaReviewResult):
                raise TypeError("review handler returned the wrong result type")
        except Exception:
            return _handler_failure(update, reason_code="review_handler_error")
        if (
            not _secure_equal(result.reviewed_sha, state["candidate_sha"])
            or result.producer_id != state.get("producer_id")
        ):
            update.update(
                review_digest=None,
                reviewed_sha=None,
                review_producer_id=None,
                reviewer_id=None,
                review_binding_digest=None,
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.EXACT_SHA_INVALID,
                reason_code="stale_review_sha",
            )
        if result.reviewer_id == result.producer_id:
            update.update(
                review_digest=None,
                reviewed_sha=None,
                review_producer_id=result.producer_id,
                reviewer_id=result.reviewer_id,
                review_binding_digest=None,
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.POLICY_BLOCKED,
                reason_code="same_provider_review",
            )
        review_binding_digest = _canonical_digest(
            {
                "task_id": state["task_id"],
                "candidate_sha": result.reviewed_sha,
                "review_digest": result.review_digest,
                "producer_id": result.producer_id,
                "reviewer_id": result.reviewer_id,
            }
        )
        update.update(
            review_digest=result.review_digest,
            reviewed_sha=result.reviewed_sha,
            review_producer_id=result.producer_id,
            reviewer_id=result.reviewer_id,
            review_binding_digest=review_binding_digest,
            transition_reason_code=result.reason_code,
        )
        if not result.accepted:
            return _handler_failure(
                update,
                classification=(
                    TerminalClassification.OWNER_DECISION_REQUIRED
                    if result.reason_code in {
                        "review_evidence_unavailable",
                        "review_binding_invalid",
                        "owner_decision_required",
                    }
                    else TerminalClassification.FAILED
                ),
                reason_code=result.reason_code,
            )
        return update

    def ci_security_completion(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(
            state, PrimaryPhase.CI_SECURITY_COMPLETION
        )
        if not proceed:
            return update
        if (
            state.get("review_digest") is None
            or state.get("reviewed_sha") != state.get("candidate_sha")
            or state.get("review_producer_id") != state.get("producer_id")
            or state.get("reviewer_id") is None
            or state.get("reviewer_id") == state.get("producer_id")
        ):
            return _handler_failure(
                update,
                classification=TerminalClassification.POLICY_BLOCKED,
                reason_code="independent_review_required",
            )
        expected_review_binding = _canonical_digest(
            {
                "task_id": state["task_id"],
                "candidate_sha": state["reviewed_sha"],
                "review_digest": state["review_digest"],
                "producer_id": state["review_producer_id"],
                "reviewer_id": state["reviewer_id"],
            }
        )
        if not _secure_equal(
            expected_review_binding, str(state.get("review_binding_digest") or "")
        ):
            return _handler_failure(
                update,
                classification=TerminalClassification.EXACT_SHA_INVALID,
                reason_code="review_binding_invalid",
            )
        try:
            result = handlers.ci_security_completion(_context(state))
            if not isinstance(result, CompletionGateResult):
                raise TypeError("completion handler returned the wrong result type")
        except Exception:
            return _handler_failure(update, reason_code="completion_handler_error")
        if not _secure_equal(result.subject_sha, state["candidate_sha"]):
            return _handler_failure(
                update,
                classification=TerminalClassification.EXACT_SHA_INVALID,
                reason_code="stale_completion_sha",
            )
        update.update(
            ci_digest=result.ci_digest,
            security_digest=result.security_digest,
            completion_digest=result.completion_digest,
            transition_reason_code=result.reason_code,
        )
        if not result.passed:
            return _handler_failure(
                update,
                classification=(
                    TerminalClassification.OWNER_DECISION_REQUIRED
                    if result.reason_code in {
                        "completion_evidence_unavailable",
                        "trusted_ci_evidence_missing",
                        "security_evidence_missing",
                        "owner_decision_required",
                    }
                    else TerminalClassification.FAILED
                ),
                reason_code=result.reason_code,
            )
        if not state["external_effect_required"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.SUCCEEDED,
                reason_code="completion_passed",
            )
        if state["graph_writer_active"] and state["legacy_writer_active"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.OWNER_DECISION_REQUIRED,
                reason_code="dual_writer",
            )
        if not state["graph_writer_active"] or state["legacy_writer_active"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.OWNER_DECISION_REQUIRED,
                reason_code="graph_writer_inactive",
            )
        merged = {**state, **update}
        update.update(
            pending_approval_kind=ApprovalKind.EXTERNAL_EFFECT.value,
            pending_approval_request_digest=_approval_request_digest(
                merged, ApprovalKind.EXTERNAL_EFFECT
            ),
            transition_reason_code="effect_approval_required",
        )
        return update

    def external_effect_approval(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(
            state, PrimaryPhase.EXTERNAL_EFFECT_APPROVAL
        )
        if not proceed:
            return update
        payload = interrupt(
            {
                "schema_version": PRIMARY_SCHEMA_VERSION,
                "type": "approval_required",
                "task_id": state["task_id"],
                "kind": ApprovalKind.EXTERNAL_EFFECT.value,
                "request_digest": state["pending_approval_request_digest"],
            }
        )
        try:
            resume = _validate_approval_payload(
                payload, state, ApprovalKind.EXTERNAL_EFFECT
            )
        except Exception:
            return _approval_payload_failure(update)
        update.update(
            pending_approval_kind=None,
            pending_approval_request_digest=None,
            effect_approval_receipt_digest=resume.receipt_digest,
            transition_reason_code="effect_approved",
        )
        if resume.decision == ApprovalDecision.DENY:
            return _handler_failure(
                update,
                classification=TerminalClassification.OWNER_DECISION_REQUIRED,
                reason_code="effect_approval_denied",
            )
        return update

    def effect_claim(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(state, PrimaryPhase.EFFECT_CLAIM)
        if not proceed:
            return update
        if state["budget_used_units"] + state["effect_cost_units"] > state["budget_limit_units"]:
            return _handler_failure(
                update,
                classification=TerminalClassification.BUDGET_EXHAUSTED,
                reason_code="effect_budget",
            )
        if state["effect_status"] != EffectStatus.NOT_STARTED.value:
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="effect_claim_replay",
            )
        attempt_digest = _canonical_digest(
            {
                "task_id": state["task_id"],
                "plan_digest": state["plan_digest"],
                "work_packet_digest": _post_plan_work_packet_digest(state),
                "candidate_sha": state["candidate_sha"],
                "completion_digest": state["completion_digest"],
                "effect_key_digest": state["effect_key_digest"],
                "writer_owner_id": state["writer_owner_id"],
            }
        )
        update.update(
            effect_status=EffectStatus.CLAIMED.value,
            effect_attempt_digest=attempt_digest,
            budget_used_units=state["budget_used_units"] + state["effect_cost_units"],
            transition_reason_code="effect_claimed",
        )
        return update

    def effect_execution_readback(state: PrimaryGraphState) -> PrimaryGraphState:
        update, proceed = _entered_update(
            state, PrimaryPhase.EFFECT_EXECUTION_READBACK
        )
        if not proceed:
            if state["effect_status"] == EffectStatus.EXECUTING.value:
                update.update(
                    effect_status=EffectStatus.RECONCILIATION_REQUIRED.value
                )
                return _handler_failure(
                    update,
                    classification=TerminalClassification.RECONCILIATION_REQUIRED,
                    reason_code="effect_execution_in_flight",
                )
            return update
        result_payload = interrupt(
            {
                "schema_version": PRIMARY_SCHEMA_VERSION,
                "type": "pure_effect_result_required",
                "task_id": state["task_id"],
                "effect_key_digest": state["effect_key_digest"],
                "writer_owner_id": state["writer_owner_id"],
                "effect_attempt_digest": state["effect_attempt_digest"],
            }
        )
        if not isinstance(result_payload, Mapping):
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="effect_result_invalid",
            )
        try:
            if set(result_payload) != {
                "effect_key_digest",
                "effect_attempt_digest",
                "writer_owner_id",
                "disposition",
                "result_digest",
                "readback_digest",
                "reason_code",
            }:
                raise ValueError("effect result fields do not match the v1 contract")
            result = EffectExecutionResult(
                effect_key_digest=str(result_payload["effect_key_digest"]),
                effect_attempt_digest=str(result_payload["effect_attempt_digest"]),
                writer_owner_id=str(result_payload["writer_owner_id"]),
                disposition=EffectDisposition(str(result_payload["disposition"])),
                result_digest=(
                    str(result_payload["result_digest"])
                    if result_payload.get("result_digest") is not None
                    else None
                ),
                readback_digest=(
                    str(result_payload["readback_digest"])
                    if result_payload.get("readback_digest") is not None
                    else None
                ),
                reason_code=str(result_payload["reason_code"]),
            )
        except Exception:
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="effect_result_invalid",
            )
        if (
            not _secure_equal(
                result.effect_key_digest, str(state["effect_key_digest"])
            )
            or not _secure_equal(
                result.effect_attempt_digest, str(state["effect_attempt_digest"])
            )
            or result.writer_owner_id != state["writer_owner_id"]
        ):
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code="effect_binding_mismatch",
            )
        result_receipt_digest = _canonical_digest(result.as_resume_payload())
        if result.disposition == EffectDisposition.AMBIGUOUS:
            update.update(
                effect_status=EffectStatus.RECONCILIATION_REQUIRED.value,
                effect_result_receipt_digest=result_receipt_digest,
            )
            return _handler_failure(
                update,
                classification=TerminalClassification.RECONCILIATION_REQUIRED,
                reason_code=result.reason_code,
            )
        if result.disposition == EffectDisposition.FAILED:
            update.update(
                effect_status=EffectStatus.FAILED.value,
                effect_result_digest=result.result_digest,
                effect_result_receipt_digest=result_receipt_digest,
            )
            return _handler_failure(update, reason_code=result.reason_code)
        update.update(
            effect_status=EffectStatus.COMPLETED.value,
            effect_result_digest=result.result_digest,
            effect_result_receipt_digest=result_receipt_digest,
            effect_readback_digest=result.readback_digest,
        )
        return _handler_failure(
            update,
            classification=TerminalClassification.SUCCEEDED,
            reason_code="effect_readback_verified",
        )

    def terminal_classification(state: PrimaryGraphState) -> PrimaryGraphState:
        classification = state.get("terminal_classification")
        if classification is None:
            classification = TerminalClassification.FAILED.value
        TerminalClassification(classification)
        prior = state.get("visited_nodes", [])
        return {
            "previous_phase": str(state.get("current_phase") or "start"),
            "current_phase": PrimaryPhase.TERMINAL_CLASSIFICATION.value,
            "visited_nodes": [
                *prior,
                PrimaryPhase.TERMINAL_CLASSIFICATION.value,
            ][-MAX_RETAINED_VISITS:],
            "terminal_classification": classification,
        }

    return {
        PrimaryPhase.AUTHENTICATE_SCOPE.value: authenticate_scope,
        PrimaryPhase.CANONICALIZE.value: canonicalize,
        PrimaryPhase.PLAN.value: plan,
        PrimaryPhase.POLICY_BUDGET.value: policy_budget,
        PrimaryPhase.APPROVAL_INTERRUPT.value: approval_interrupt,
        PrimaryPhase.SCHEDULE.value: schedule,
        PrimaryPhase.PROVIDER_DISPATCH_CLAIM.value: provider_dispatch_claim,
        PrimaryPhase.PROVIDER_SUBGRAPH.value: provider_subgraph,
        PrimaryPhase.EVIDENCE_DEBATE.value: evidence_debate,
        PrimaryPhase.OPENAI_ACCEPTANCE_REVISION.value: openai_acceptance_revision,
        PrimaryPhase.EXACT_SHA_REVIEW.value: exact_sha_review,
        PrimaryPhase.CI_SECURITY_COMPLETION.value: ci_security_completion,
        PrimaryPhase.EXTERNAL_EFFECT_APPROVAL.value: external_effect_approval,
        PrimaryPhase.EFFECT_CLAIM.value: effect_claim,
        PrimaryPhase.EFFECT_EXECUTION_READBACK.value: effect_execution_readback,
        PrimaryPhase.TERMINAL_CLASSIFICATION.value: terminal_classification,
    }


def _terminal_or(next_phase: PrimaryPhase) -> Callable[[PrimaryGraphState], str]:
    def route(state: PrimaryGraphState) -> str:
        if state.get("terminal_classification") is not None:
            return PrimaryPhase.TERMINAL_CLASSIFICATION.value
        return next_phase.value

    return route


def _route_policy(state: PrimaryGraphState) -> str:
    if state.get("terminal_classification") is not None:
        return PrimaryPhase.TERMINAL_CLASSIFICATION.value
    if state.get("pending_approval_kind") == ApprovalKind.EXECUTION.value:
        return PrimaryPhase.APPROVAL_INTERRUPT.value
    return PrimaryPhase.SCHEDULE.value


def _route_acceptance(state: PrimaryGraphState) -> str:
    if state.get("terminal_classification") is not None:
        return PrimaryPhase.TERMINAL_CLASSIFICATION.value
    if state.get("acceptance_decision") == AcceptanceDecision.REVISE.value:
        return PrimaryPhase.PROVIDER_DISPATCH_CLAIM.value
    return PrimaryPhase.EXACT_SHA_REVIEW.value


def _route_completion(state: PrimaryGraphState) -> str:
    if state.get("terminal_classification") is not None:
        return PrimaryPhase.TERMINAL_CLASSIFICATION.value
    return PrimaryPhase.EXTERNAL_EFFECT_APPROVAL.value


def build_primary_graph(
    handlers: PrimaryHandlers,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    interrupt_after: Sequence[str] | None = None,
) -> CompiledStateGraph:
    """Compile the bounded parent graph around caller-provided pure handlers."""

    nodes = _build_nodes(handlers)
    graph = StateGraph(PrimaryGraphState)
    for name, node in nodes.items():
        graph.add_node(name, node)

    graph.add_edge(START, PrimaryPhase.AUTHENTICATE_SCOPE.value)
    linear_edges = (
        (PrimaryPhase.AUTHENTICATE_SCOPE, PrimaryPhase.CANONICALIZE),
        (PrimaryPhase.CANONICALIZE, PrimaryPhase.PLAN),
        (PrimaryPhase.PLAN, PrimaryPhase.POLICY_BUDGET),
        (PrimaryPhase.APPROVAL_INTERRUPT, PrimaryPhase.SCHEDULE),
        (PrimaryPhase.SCHEDULE, PrimaryPhase.PROVIDER_DISPATCH_CLAIM),
        (PrimaryPhase.PROVIDER_DISPATCH_CLAIM, PrimaryPhase.PROVIDER_SUBGRAPH),
        (PrimaryPhase.PROVIDER_SUBGRAPH, PrimaryPhase.EVIDENCE_DEBATE),
        (PrimaryPhase.EVIDENCE_DEBATE, PrimaryPhase.OPENAI_ACCEPTANCE_REVISION),
        (PrimaryPhase.EXACT_SHA_REVIEW, PrimaryPhase.CI_SECURITY_COMPLETION),
        (PrimaryPhase.EXTERNAL_EFFECT_APPROVAL, PrimaryPhase.EFFECT_CLAIM),
        (PrimaryPhase.EFFECT_CLAIM, PrimaryPhase.EFFECT_EXECUTION_READBACK),
        (PrimaryPhase.EFFECT_EXECUTION_READBACK, PrimaryPhase.TERMINAL_CLASSIFICATION),
    )
    for source, target in linear_edges:
        graph.add_conditional_edges(
            source.value,
            _terminal_or(target),
            {
                target.value: target.value,
                PrimaryPhase.TERMINAL_CLASSIFICATION.value: PrimaryPhase.TERMINAL_CLASSIFICATION.value,
            },
        )
    graph.add_conditional_edges(
        PrimaryPhase.POLICY_BUDGET.value,
        _route_policy,
        {
            PrimaryPhase.APPROVAL_INTERRUPT.value: PrimaryPhase.APPROVAL_INTERRUPT.value,
            PrimaryPhase.SCHEDULE.value: PrimaryPhase.SCHEDULE.value,
            PrimaryPhase.TERMINAL_CLASSIFICATION.value: PrimaryPhase.TERMINAL_CLASSIFICATION.value,
        },
    )
    graph.add_conditional_edges(
        PrimaryPhase.OPENAI_ACCEPTANCE_REVISION.value,
        _route_acceptance,
        {
            PrimaryPhase.PROVIDER_DISPATCH_CLAIM.value: PrimaryPhase.PROVIDER_DISPATCH_CLAIM.value,
            PrimaryPhase.EXACT_SHA_REVIEW.value: PrimaryPhase.EXACT_SHA_REVIEW.value,
            PrimaryPhase.TERMINAL_CLASSIFICATION.value: PrimaryPhase.TERMINAL_CLASSIFICATION.value,
        },
    )
    graph.add_conditional_edges(
        PrimaryPhase.CI_SECURITY_COMPLETION.value,
        _route_completion,
        {
            PrimaryPhase.EXTERNAL_EFFECT_APPROVAL.value: PrimaryPhase.EXTERNAL_EFFECT_APPROVAL.value,
            PrimaryPhase.TERMINAL_CLASSIFICATION.value: PrimaryPhase.TERMINAL_CLASSIFICATION.value,
        },
    )
    graph.add_edge(PrimaryPhase.TERMINAL_CLASSIFICATION.value, END)
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) if interrupt_after is not None else None,
        name="director_langgraph_primary_v1",
    )


def primary_checkpoint_config(
    task_id: str,
    *,
    namespace: str = DEFAULT_CHECKPOINT_NAMESPACE,
    recursion_limit: int = 300,
) -> dict[str, Any]:
    """Return the stable, namespaced LangGraph thread configuration for a task."""

    _require_id(task_id, "task_id")
    _require_id(namespace, "namespace")
    if type(recursion_limit) is not int or recursion_limit < 1:
        raise ValueError("recursion_limit must be a positive integer")
    return {
        "configurable": {"thread_id": f"{namespace}:{task_id}"},
        "recursion_limit": recursion_limit,
    }


class PrimaryWorkflowRuntime:
    """Exact, replay-safe facade for one compiled primary graph definition."""

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver[Any],
        handlers: PrimaryHandlers,
        *,
        checkpoint_namespace: str = DEFAULT_CHECKPOINT_NAMESPACE,
        interrupt_after: Sequence[str] | None = None,
        auto_execute_provider: bool = True,
        auto_execute_effect: bool = True,
        reconcile_executing_effect: bool = False,
        claim_store: ConditionalClaimStore | None = None,
        owner_token: str | None = None,
    ) -> None:
        _require_id(checkpoint_namespace, "checkpoint_namespace")
        if type(auto_execute_provider) is not bool:
            raise ValueError("auto_execute_provider must be a boolean")
        if type(auto_execute_effect) is not bool:
            raise ValueError("auto_execute_effect must be a boolean")
        if type(reconcile_executing_effect) is not bool:
            raise ValueError("reconcile_executing_effect must be a boolean")
        if reconcile_executing_effect and not auto_execute_effect:
            raise ValueError(
                "reconcile_executing_effect requires auto_execute_effect"
            )
        if claim_store is not None and not callable(getattr(claim_store, "claim", None)):
            raise ValueError("claim_store must expose a callable claim(request)")
        if (
            claim_store is not None
            and self._requires_factory_bound_control_plane()
            and not self._is_durable_control_plane(claim_store)
        ):
            raise ValueError(
                "active primary execution requires a factory-bound durable "
                "control plane"
            )
        self._checkpointer = checkpointer
        self._handlers = handlers
        self._namespace = checkpoint_namespace
        self._auto_execute_provider = auto_execute_provider
        self._auto_execute_effect = auto_execute_effect
        self._reconcile_executing_effect = reconcile_executing_effect
        self._claim_store = claim_store
        self._owner_token = (
            _require_id(owner_token, "owner_token")
            if owner_token is not None
            else uuid.uuid4().hex
        )
        self._graph = build_primary_graph(
            handlers,
            checkpointer=checkpointer,
            interrupt_after=interrupt_after,
        )

    @staticmethod
    def _is_durable_control_plane(store: object) -> bool:
        """Recognize the factory-bound structural capability, not a claim duck."""

        # Imported only when a runtime is constructed: langgraph_postgres
        # imports the packet types above, so importing it at module load would
        # create a cycle.  Production admission is deliberately nominal before
        # it is structural or provenance-checked; a duck cannot forge this gate.
        from .langgraph_postgres import PostgresConditionalClaimStore

        if not isinstance(store, PostgresConditionalClaimStore):
            return False
        if not isinstance(store, DurablePrimaryControlPlane):
            return False
        identity = getattr(store, "storage_identity", None)
        identity_valid = bool(
            isinstance(identity, str)
            and re.fullmatch(r"postgres:sha256:[0-9a-f]{64}", identity)
        )
        if not identity_valid:
            return False
        try:
            return store.is_factory_bound_control_plane() is True
        except Exception:
            return False

    def _requires_factory_bound_control_plane(self) -> bool:
        """Return whether this runtime enforces the production factory boundary.

        Production always does.  The protected seam exists only so isolated
        unit-test subclasses can exercise claim mechanics without publishing a
        bypass in the runtime constructor.
        """

        return True

    def _durable_control_plane(self) -> DurablePrimaryControlPlane | None:
        store = self._claim_store
        if store is None or not self._requires_factory_bound_control_plane():
            return None
        if not self._is_durable_control_plane(store):
            return None
        return store  # type: ignore[return-value]

    def _config(self, task_id: str) -> dict[str, Any]:
        return primary_checkpoint_config(task_id, namespace=self._namespace)

    def _snapshot(self, task_id: str):
        return self._graph.get_state(self._config(task_id))

    def _fail_closed_invalid_post_plan_checkpoint(self, task_id: str) -> bool:
        """Quarantine a resumed checkpoint whose plan/packet binding drifted."""

        snapshot = self._snapshot(task_id)
        state = snapshot.values
        if not state or state.get("plan_digest") is None:
            return False
        if _post_plan_work_packet_digest(state) is not None:
            return False
        if (
            state.get("terminal_classification")
            != TerminalClassification.POLICY_BLOCKED.value
            or state.get("transition_reason_code")
            != "work_packet_authority_invalid"
        ):
            self._graph.update_state(
                self._config(task_id),
                {
                    "terminal_classification": (
                        TerminalClassification.POLICY_BLOCKED.value
                    ),
                    "transition_reason_code": "work_packet_authority_invalid",
                },
            )
        return True

    @staticmethod
    def _state_digest(state: Mapping[str, Any]) -> str:
        compact = {key: value for key, value in state.items() if key != "__interrupt__"}
        return _canonical_digest(compact)

    def _outcome(
        self,
        task_id: str,
        *,
        override: WorkflowRunStatus | None = None,
        reason_override: str | None = None,
    ) -> PrimaryOutcome:
        snapshot = self._snapshot(task_id)
        state = snapshot.values
        if not state:
            raise ValueError("primary graph checkpoint does not exist")
        pending: ApprovalRequest | None = None
        pending_kind = state.get("pending_approval_kind")
        pending_digest = state.get("pending_approval_request_digest")
        if pending_kind is not None and pending_digest is not None:
            pending = ApprovalRequest(
                task_id=task_id,
                kind=ApprovalKind(str(pending_kind)),
                request_digest=_require_digest(str(pending_digest), "request_digest"),
            )
        classification_raw = state.get("terminal_classification")
        classification = (
            TerminalClassification(str(classification_raw))
            if classification_raw is not None
            else None
        )
        if override is not None:
            run_status = override
        elif classification is not None:
            run_status = WorkflowRunStatus.TERMINAL
        elif pending is not None or snapshot.next:
            run_status = WorkflowRunStatus.PAUSED
        else:
            run_status = WorkflowRunStatus.RUNNING
        phase = str(state.get("current_phase") or "start")
        if PrimaryPhase.PROVIDER_SUBGRAPH.value in snapshot.next:
            phase = PrimaryPhase.PROVIDER_SUBGRAPH.value
        if PrimaryPhase.EFFECT_EXECUTION_READBACK.value in snapshot.next:
            phase = PrimaryPhase.EFFECT_EXECUTION_READBACK.value
        return PrimaryOutcome(
            task_id=task_id,
            run_status=run_status,
            phase=phase,
            terminal_classification=classification,
            reason_code=(
                _require_code(reason_override)
                if reason_override is not None
                else str(state.get("transition_reason_code") or "unknown")
            ),
            pending_approval=pending,
            provider_dispatch_status=ProviderDispatchStatus(
                str(state["provider_dispatch_status"])
            ),
            effect_status=EffectStatus(str(state["effect_status"])),
            state_digest=self._state_digest(state),
            visit_count=int(state["visit_count"]),
            visited_nodes=tuple(state.get("visited_nodes", [])),
            revision_count=int(state["revision_count"]),
            budget_used_units=int(state["budget_used_units"]),
            parallel_plan_digest=state.get("parallel_plan_digest"),
            parallel_aggregate_digest=state.get("parallel_aggregate_digest"),
            parallel_terminal=bool(state.get("parallel_terminal", False)),
            parallel_succeeded=bool(state.get("parallel_succeeded", False)),
            parallel_ambiguous_slot_count=int(
                state.get("parallel_ambiguous_slot_count", 0)
            ),
        )

    def record_parallel_aggregate(
        self,
        task_id: str,
        *,
        plan_digest: str,
        aggregate_digest: str,
        terminal: bool,
        succeeded: bool,
        ambiguous_slot_count: int,
    ) -> PrimaryOutcome:
        """Checkpoint deterministic Postgres aggregation without performing I/O."""

        _require_digest(plan_digest, "parallel_plan_digest")
        _require_digest(aggregate_digest, "parallel_aggregate_digest")
        if type(terminal) is not bool or type(succeeded) is not bool:
            raise ValueError("parallel terminal flags must be booleans")
        if succeeded and not terminal:
            raise ValueError("a successful parallel aggregate must be terminal")
        if type(ambiguous_slot_count) is not int or ambiguous_slot_count < 0:
            raise ValueError("ambiguous_slot_count must be non-negative")
        snapshot = self._snapshot(task_id)
        if not snapshot.values:
            raise ValueError("primary graph checkpoint does not exist")
        held = snapshot.values.get("parallel_plan_digest")
        if held is not None and not _secure_equal(str(held), plan_digest):
            raise ValueError("parallel plan binding changed")
        if snapshot.values.get("parallel_terminal") is True:
            if not (
                terminal
                and bool(snapshot.values.get("parallel_succeeded")) == succeeded
                and _secure_equal(
                    str(snapshot.values.get("parallel_aggregate_digest") or ""),
                    aggregate_digest,
                )
                and int(snapshot.values.get("parallel_ambiguous_slot_count", 0))
                == ambiguous_slot_count
            ):
                raise ValueError("terminal parallel aggregate cannot be rewritten")
            return self._outcome(task_id)
        self._graph.update_state(
            self._config(task_id),
            {
                "parallel_plan_digest": plan_digest,
                "parallel_aggregate_digest": aggregate_digest,
                "parallel_terminal": terminal,
                "parallel_succeeded": succeeded,
                "parallel_ambiguous_slot_count": ambiguous_slot_count,
            },
        )
        return self._outcome(task_id)

    def resume_parallel_aggregate(
        self,
        task_id: str,
        *,
        aggregate_digest: str,
        succeeded: bool,
        candidate_sha: str,
    ) -> PrimaryOutcome:
        """Consume one terminal all-assignment aggregate through the graph edge.

        The parallel ledger is evidence, not a second workflow engine.  This
        method therefore resumes the existing provider interrupt exactly once;
        every downstream evidence, independent-review, CI/security, effect and
        terminal transition remains graph-owned.
        """

        _require_digest(aggregate_digest, "aggregate_digest")
        _require_sha(candidate_sha, "candidate_sha")
        snapshot = self._snapshot(task_id)
        state = snapshot.values
        if not state:
            raise ValueError("primary graph checkpoint does not exist")
        if not bool(state.get("parallel_terminal")):
            raise ValueError("parallel aggregate is not terminal")
        if bool(state.get("parallel_succeeded")) != succeeded or not _secure_equal(
            str(state.get("parallel_aggregate_digest") or ""), aggregate_digest
        ):
            raise ValueError("parallel aggregate binding changed")
        if int(state.get("parallel_ambiguous_slot_count", 0)):
            raise ValueError("ambiguous parallel aggregate requires reconciliation")
        if state.get("provider_result_receipt_digest") is not None:
            return self._outcome(task_id)
        status = ProviderDispatchStatus(str(state["provider_dispatch_status"]))
        if status == ProviderDispatchStatus.CLAIMED:
            self.prepare_provider_dispatch(task_id)
            state = self._snapshot(task_id).values
        elif status != ProviderDispatchStatus.DISPATCHING:
            raise ValueError("parallel aggregate has no pending graph provider edge")
        result = ProviderSubgraphResult(
            provider_attempt_digest=str(state["provider_attempt_digest"]),
            idempotency_key_digest=str(state["idempotency_key_digest"]),
            authority_snapshot_digest=str(state["authority_snapshot_digest"]),
            producer_id=str(state["scheduled_producer_id"]),
            disposition=(
                ProviderDisposition.COMPLETED
                if succeeded
                else ProviderDisposition.FAILED
            ),
            result_digest=aggregate_digest,
            candidate_sha=candidate_sha if succeeded else None,
            reason_code=(
                "parallel_aggregate_completed"
                if succeeded
                else "parallel_aggregate_failed"
            ),
        )
        return self.record_provider_result(task_id, result)

    @staticmethod
    def _provider_intent(state: Mapping[str, Any]) -> ProviderDispatchIntent:
        return ProviderDispatchIntent(
            task_id=str(state["task_id"]),
            provider_attempt_digest=str(state["provider_attempt_digest"]),
            idempotency_key_digest=str(state["idempotency_key_digest"]),
            authority_snapshot_digest=str(state["authority_snapshot_digest"]),
            producer_id=str(state["scheduled_producer_id"]),
            revision_count=int(state["revision_count"]),
        )

    def _claim_key(
        self,
        task_id: str,
        *,
        field: str,
        expected: str,
        target: str,
        attempt_digest: str,
    ) -> str:
        """Return the stable fence identity for each transition attempt.

        The key is derived only from durable, replayable facts, so a restarted
        process, a second runtime instance, and a sibling host all address the
        exact same fence row.  It carries the attempt digest, so a genuinely new
        attempt -- a revision, which the graph re-claims with a fresh attempt
        digest -- gets its own fence instead of colliding with the previous one.
        """

        return _canonical_digest(
            {
                "schema_version": PRIMARY_SCHEMA_VERSION,
                "namespace": self._namespace,
                "task_id": task_id,
                "field": field,
                "expected_state": expected,
                "target_state": target,
                "attempt_digest": attempt_digest,
            }
        )

    def _claim_status_transition(
        self,
        task_id: str,
        *,
        field: str,
        attempt_field: str,
        expected: str,
        target: str,
        reason_code: str,
    ) -> bool:
        """Fence one side-effecting ``expected`` -> ``target`` move, or refuse it.

        Guaranteed by the injected :class:`ConditionalClaimStore`: for one
        ``claim_key`` at most one caller is ever granted the fence, so at most
        one owner may run the paid handler for the claimed attempt, whatever else races
        it.  That grant is the only exclusion mechanism here.

        NOT guaranteed by the checkpointer, and deliberately not claimed:
        ``update_state`` is an unconditional append, so the read below is a
        *pre-filter* that cheaply rejects an obviously wrong status, and the
        read after the write is a *confirmation* that the fenced transition
        actually landed.  Neither read excludes a concurrent writer, and neither
        is treated as if it did.

        Every failure path returns ``False`` so the caller fails closed.  A
        granted fence whose checkpoint write cannot be confirmed is not retried:
        the fence stays held, so the attempt can only be reconciled, never paid
        for twice.
        """

        store = self._claim_store
        if store is None:
            return False
        config = self._config(task_id)
        observed = self._graph.get_state(config)
        if not observed.values or str(observed.values.get(field)) != expected:
            return False
        if _post_plan_work_packet_digest(observed.values) is None:
            return False
        observed_id = _checkpoint_id(observed.config)
        if observed_id is None:
            return False
        attempt_digest = observed.values.get(attempt_field)
        if not isinstance(attempt_digest, str):
            return False
        try:
            request = ClaimRequest(
                claim_key=self._claim_key(
                    task_id,
                    field=field,
                    expected=expected,
                    target=target,
                    attempt_digest=attempt_digest,
                ),
                task_id=task_id,
                field=field,
                expected_state=expected,
                target_state=target,
                attempt_digest=attempt_digest,
                owner_token=self._owner_token,
            )
            control_plane = self._durable_control_plane()
            if self._requires_factory_bound_control_plane():
                if control_plane is None:
                    return False
                writer = control_plane.current_task_writer(task_id)
                if (
                    writer is None
                    or writer.task_id != task_id
                    or writer.engine_owner != str(observed.values["writer_owner_id"])
                    or not _secure_equal(writer.owner_token, self._owner_token)
                    or type(writer.fencing_generation) is not int
                    or writer.fencing_generation < 1
                ):
                    return False
                recorded_limit = control_plane.register_task_budget(
                    task_id,
                    budget_limit_units=int(observed.values["budget_limit_units"]),
                )
                if recorded_limit != int(observed.values["budget_limit_units"]):
                    return False
            outcome = store.claim(request)
            if (
                not isinstance(outcome, ClaimOutcome)
                or not outcome.granted
                or not _secure_equal(outcome.claim_key, request.claim_key)
                or not _secure_equal(outcome.owner_token, self._owner_token)
                or outcome.state != target
            ):
                return False
            if control_plane is not None:
                if field == "provider_dispatch_status":
                    intent_domain = "provider"
                    effect_key_digest = _canonical_digest(
                        {
                            "domain": "provider",
                            "idempotency_key_digest": observed.values[
                                "idempotency_key_digest"
                            ],
                            "authority_snapshot_digest": observed.values[
                                "authority_snapshot_digest"
                            ],
                            "producer_id": observed.values["scheduled_producer_id"],
                        }
                    )
                    reserved_units = int(observed.values["provider_cost_units"])
                else:
                    intent_domain = "external_effect"
                    effect_key_digest = str(observed.values["effect_key_digest"])
                    reserved_units = int(observed.values["effect_cost_units"])
                reservation_key = _canonical_digest(
                    {
                        "schema_version": PRIMARY_SCHEMA_VERSION,
                        "domain": intent_domain,
                        "claim_key": request.claim_key,
                        "task_id": task_id,
                        "effect_key_digest": effect_key_digest,
                        "attempt_digest": attempt_digest,
                        "authority_snapshot_digest": observed.values[
                            "authority_snapshot_digest"
                        ],
                        "writer_owner_id": observed.values["writer_owner_id"],
                        "reserved_units": reserved_units,
                    }
                )
                reservation = control_plane.reserve_effect_intent(
                    reservation_key=reservation_key,
                    claim_key=request.claim_key,
                    task_id=task_id,
                    owner_token=self._owner_token,
                    effect_key_digest=effect_key_digest,
                    effect_attempt_digest=attempt_digest,
                    writer_owner_id=str(observed.values["writer_owner_id"]),
                    reserved_units=reserved_units,
                )
                expected = {
                    "reservation_key": reservation_key,
                    "claim_key": request.claim_key,
                    "task_id": task_id,
                    "owner_token": self._owner_token,
                    "effect_key_digest": effect_key_digest,
                    "effect_attempt_digest": attempt_digest,
                    "writer_owner_id": str(observed.values["writer_owner_id"]),
                    "reserved_units": reserved_units,
                }
                if any(
                    getattr(reservation, key, None) != value
                    for key, value in expected.items()
                ):
                    return False
        except Exception:
            return False
        written_id = _checkpoint_id(
            self._graph.update_state(
                config,
                {field: target, "transition_reason_code": _require_code(reason_code)},
            )
        )
        latest = self._graph.get_state(config)
        return (
            written_id is not None
            and _checkpoint_id(latest.config) == written_id
            and _checkpoint_id(latest.parent_config) == observed_id
            and str(latest.values.get(field)) == target
        )

    def prepare_provider_dispatch(self, task_id: str) -> ProviderDispatchIntent:
        """Persist the paid-dispatch reservation before any handler can run.

        Fails closed with :class:`ClaimStoreUnavailableError` when no genuine
        conditional store was injected.  Otherwise only an exactly ``CLAIMED``
        reservation whose fence this runtime wins is handed out.  An already
        ``DISPATCHING`` thread belongs to another owner and is never handed out
        a second time -- not even back to the owner that won it; it is left for
        reconciliation.
        """

        if self._claim_store is None:
            raise ClaimStoreUnavailableError(
                "a conditional claim store is required before any paid dispatch"
            )
        snapshot = self._snapshot(task_id)
        if PrimaryPhase.PROVIDER_SUBGRAPH.value not in snapshot.next:
            raise ValueError("primary graph has no pending provider dispatch")
        if not self._claim_status_transition(
            task_id,
            field="provider_dispatch_status",
            attempt_field="provider_attempt_digest",
            expected=ProviderDispatchStatus.CLAIMED.value,
            target=ProviderDispatchStatus.DISPATCHING.value,
            reason_code="provider_dispatch_started",
        ):
            raise ValueError("provider dispatch is not claimable")
        return self._provider_intent(self._snapshot(task_id).values)

    def prepare_effect_execution(self, task_id: str) -> str:
        """Persist the single-owner effect reservation before any handler call.

        Mirrors :meth:`prepare_provider_dispatch`, including the fail-closed
        refusal when no conditional store is available: only an exactly
        ``CLAIMED`` effect whose fence this runtime wins is claimable, and an
        ``EXECUTING`` effect is never re-handed out.
        """

        if self._claim_store is None:
            raise ClaimStoreUnavailableError(
                "a conditional claim store is required before any external effect"
            )
        snapshot = self._snapshot(task_id)
        if PrimaryPhase.EFFECT_EXECUTION_READBACK.value not in snapshot.next:
            raise ValueError("primary graph has no pending external effect")
        if not self._claim_status_transition(
            task_id,
            field="effect_status",
            attempt_field="effect_attempt_digest",
            expected=EffectStatus.CLAIMED.value,
            target=EffectStatus.EXECUTING.value,
            reason_code="effect_execution_started",
        ):
            raise ValueError("external effect is not claimable")
        return str(self._snapshot(task_id).values["effect_attempt_digest"])

    def _resume_ambiguous_provider(self, task_id: str, reason_code: str) -> None:
        state = self._snapshot(task_id).values
        result = ProviderSubgraphResult(
            provider_attempt_digest=str(state["provider_attempt_digest"]),
            idempotency_key_digest=str(state["idempotency_key_digest"]),
            authority_snapshot_digest=str(state["authority_snapshot_digest"]),
            producer_id=str(state["scheduled_producer_id"]),
            disposition=ProviderDisposition.AMBIGUOUS,
            reason_code=reason_code,
        )
        self._graph.invoke(
            Command(resume=result.as_resume_payload()), self._config(task_id)
        )

    def _drive_provider_once(self, task_id: str) -> None:
        snapshot = self._snapshot(task_id)
        if PrimaryPhase.PROVIDER_SUBGRAPH.value not in snapshot.next:
            return
        state = snapshot.values
        status = ProviderDispatchStatus(str(state["provider_dispatch_status"]))
        if status == ProviderDispatchStatus.DISPATCHING:
            self._resume_ambiguous_provider(task_id, "provider_outcome_unknown")
            return
        if status != ProviderDispatchStatus.CLAIMED:
            self._resume_ambiguous_provider(task_id, "provider_state_invalid")
            return

        try:
            intent = self.prepare_provider_dispatch(task_id)
        except ClaimStoreUnavailableError:
            self._resume_ambiguous_provider(
                task_id, "provider_claim_store_unavailable"
            )
            return
        except ValueError:
            self._resume_ambiguous_provider(task_id, "provider_claim_contended")
            return
        state = self._snapshot(task_id).values
        try:
            result = self._handlers.provider_subgraph(_context(state))
            if not isinstance(result, ProviderSubgraphResult):
                raise TypeError("provider handler returned the wrong result type")
            if (
                not _secure_equal(
                    result.provider_attempt_digest,
                    intent.provider_attempt_digest,
                )
                or not _secure_equal(
                    result.idempotency_key_digest,
                    intent.idempotency_key_digest,
                )
                or not _secure_equal(
                    result.authority_snapshot_digest,
                    intent.authority_snapshot_digest,
                )
                or result.producer_id != intent.producer_id
            ):
                raise ValueError("provider handler returned mismatched bindings")
        except Exception:
            result = ProviderSubgraphResult(
                provider_attempt_digest=intent.provider_attempt_digest,
                idempotency_key_digest=intent.idempotency_key_digest,
                authority_snapshot_digest=intent.authority_snapshot_digest,
                producer_id=intent.producer_id,
                disposition=ProviderDisposition.AMBIGUOUS,
                reason_code="provider_handler_ambiguous",
            )
        self._graph.invoke(
            Command(resume=result.as_resume_payload()), self._config(task_id)
        )

    def _resume_ambiguous_effect(self, task_id: str, reason_code: str) -> None:
        state = self._snapshot(task_id).values
        result = EffectExecutionResult(
            effect_key_digest=str(state["effect_key_digest"]),
            effect_attempt_digest=str(state["effect_attempt_digest"]),
            writer_owner_id=str(state["writer_owner_id"]),
            disposition=EffectDisposition.AMBIGUOUS,
            reason_code=reason_code,
        )
        self._graph.invoke(Command(resume=result.as_resume_payload()), self._config(task_id))

    def _drive_effect_once(self, task_id: str) -> None:
        snapshot = self._snapshot(task_id)
        if PrimaryPhase.EFFECT_EXECUTION_READBACK.value not in snapshot.next:
            return
        state = snapshot.values
        effect_status = EffectStatus(str(state["effect_status"]))
        if (
            effect_status == EffectStatus.EXECUTING
            and not self._reconcile_executing_effect
        ):
            self._resume_ambiguous_effect(task_id, "effect_outcome_unknown")
            return
        if effect_status not in {EffectStatus.CLAIMED, EffectStatus.EXECUTING}:
            self._resume_ambiguous_effect(task_id, "effect_state_invalid")
            return

        if effect_status == EffectStatus.CLAIMED:
            try:
                self.prepare_effect_execution(task_id)
            except ClaimStoreUnavailableError:
                self._resume_ambiguous_effect(task_id, "effect_claim_store_unavailable")
                return
            except ValueError:
                self._resume_ambiguous_effect(task_id, "effect_claim_contended")
                return
        state = self._snapshot(task_id).values
        try:
            result = self._handlers.effect_execution_readback(_context(state))
            if not isinstance(result, EffectExecutionResult):
                raise TypeError("effect handler returned the wrong result type")
            if (
                not _secure_equal(
                    result.effect_key_digest, str(state["effect_key_digest"])
                )
                or not _secure_equal(
                    result.effect_attempt_digest,
                    str(state["effect_attempt_digest"]),
                )
                or result.writer_owner_id != state["writer_owner_id"]
            ):
                raise ValueError("effect handler returned mismatched bindings")
        except Exception:
            result = EffectExecutionResult(
                effect_key_digest=str(state["effect_key_digest"]),
                effect_attempt_digest=str(state["effect_attempt_digest"]),
                writer_owner_id=str(state["writer_owner_id"]),
                disposition=EffectDisposition.AMBIGUOUS,
                reason_code="effect_handler_ambiguous",
            )
        self._graph.invoke(
            Command(resume=result.as_resume_payload()),
            self._config(task_id),
        )

    def _drive_pending_boundaries(self, task_id: str) -> None:
        """Advance only explicit provider/effect boundaries, with a hard loop cap."""

        for _ in range(64):
            snapshot = self._snapshot(task_id)
            if not snapshot.values or snapshot.values.get("terminal_classification"):
                return
            if PrimaryPhase.PROVIDER_SUBGRAPH.value in snapshot.next:
                if not self._auto_execute_provider:
                    return
                self._drive_provider_once(task_id)
                continue
            if PrimaryPhase.EFFECT_EXECUTION_READBACK.value in snapshot.next:
                if not self._auto_execute_effect:
                    return
                self._drive_effect_once(task_id)
                continue
            return
        raise RuntimeError("primary boundary driver exceeded its bounded step limit")

    def start(self, request: PrimaryRequest) -> PrimaryOutcome:
        """Start once or recover/replay the stable thread without restarting work."""

        config = self._config(request.task_id)
        snapshot = self._graph.get_state(config)
        if snapshot.values:
            stored_digest = str(snapshot.values.get("input_digest") or "")
            if not _secure_equal(stored_digest, request.input_digest):
                return self._outcome(
                    request.task_id,
                    override=WorkflowRunStatus.REJECTED,
                    reason_override="stale_workflow_input",
                )
            if self._fail_closed_invalid_post_plan_checkpoint(request.task_id):
                return self._outcome(request.task_id)
            if (
                snapshot.values.get("terminal_classification") is None
                and snapshot.values.get("pending_approval_kind") is None
                and PrimaryPhase.PROVIDER_SUBGRAPH.value not in snapshot.next
                and PrimaryPhase.EFFECT_EXECUTION_READBACK.value not in snapshot.next
                and snapshot.next
            ):
                self._graph.invoke(None, config)
        else:
            self._graph.invoke(request.graph_input(), config)
        self._drive_pending_boundaries(request.task_id)
        return self._outcome(request.task_id)

    def resume_approval(self, resume: ApprovalResume) -> PrimaryOutcome:
        """Apply exactly one current approval or return a non-mutating rejection."""

        snapshot = self._snapshot(resume.task_id)
        state = snapshot.values
        if not state:
            raise ValueError("primary graph checkpoint does not exist")
        receipt_field = (
            "execution_approval_receipt_digest"
            if resume.kind == ApprovalKind.EXECUTION
            else "effect_approval_receipt_digest"
        )
        stored_receipt = state.get(receipt_field)
        if stored_receipt is not None and _secure_equal(
            str(stored_receipt), resume.receipt_digest
        ):
            return self._outcome(
                resume.task_id,
                override=WorkflowRunStatus.ALREADY_APPLIED,
                reason_override="approval_already_applied",
            )
        pending_kind = state.get("pending_approval_kind")
        pending_digest = state.get("pending_approval_request_digest")
        if (
            pending_kind != resume.kind.value
            or pending_digest is None
            or not _secure_equal(str(pending_digest), resume.request_digest)
            or resume.task_id != state.get("task_id")
        ):
            return self._outcome(
                resume.task_id,
                override=WorkflowRunStatus.REJECTED,
                reason_override="stale_approval",
            )
        self._graph.invoke(
            Command(resume=resume.as_resume_payload()),
            self._config(resume.task_id),
        )
        self._drive_pending_boundaries(resume.task_id)
        return self._outcome(resume.task_id)

    def resume(self, resume: ApprovalResume) -> PrimaryOutcome:
        """Alias for callers that model approval as the graph resume operation."""

        return self.resume_approval(resume)

    def execute_pending_effect(self, task_id: str) -> PrimaryOutcome:
        """Drive one pure effect handler call, or reconcile an in-flight restart."""

        self._drive_effect_once(task_id)
        return self._outcome(task_id)

    def execute_pending_provider(self, task_id: str) -> PrimaryOutcome:
        """Drive one reserved provider intent, or reconcile an in-flight restart."""

        self._drive_provider_once(task_id)
        self._drive_pending_boundaries(task_id)
        return self._outcome(task_id)

    def record_provider_result(
        self, task_id: str, result: ProviderSubgraphResult
    ) -> PrimaryOutcome:
        """Resume one already-dispatched provider boundary from authenticated input."""

        snapshot = self._snapshot(task_id)
        state = snapshot.values
        delivered_receipt = _canonical_digest(result.as_resume_payload())
        applied_receipt = state.get("provider_result_receipt_digest")
        if applied_receipt is not None:
            if _secure_equal(str(applied_receipt), delivered_receipt):
                return self._outcome(
                    task_id,
                    override=WorkflowRunStatus.ALREADY_APPLIED,
                    reason_override="provider_result_already_applied",
                )
            raise ValueError("provider result conflicts with the already applied result")
        if PrimaryPhase.PROVIDER_SUBGRAPH.value not in snapshot.next:
            raise ValueError("primary graph has no pending provider result")
        if ProviderDispatchStatus(str(state["provider_dispatch_status"])) != (
            ProviderDispatchStatus.DISPATCHING
        ):
            raise ValueError("provider result requires a durably dispatched intent")
        expected = self._provider_intent(state)
        if (
            not _secure_equal(
                result.provider_attempt_digest, expected.provider_attempt_digest
            )
            or not _secure_equal(
                result.idempotency_key_digest, expected.idempotency_key_digest
            )
            or not _secure_equal(
                result.authority_snapshot_digest,
                expected.authority_snapshot_digest,
            )
            or result.producer_id != expected.producer_id
        ):
            raise ValueError("provider result does not match the dispatched intent")
        self._graph.invoke(
            Command(resume=result.as_resume_payload()), self._config(task_id)
        )
        self._drive_pending_boundaries(task_id)
        return self._outcome(task_id)

    def record_effect_result(
        self, task_id: str, result: EffectExecutionResult
    ) -> PrimaryOutcome:
        """Resume one already-executing external effect from authenticated readback."""

        snapshot = self._snapshot(task_id)
        state = snapshot.values
        delivered_receipt = _canonical_digest(result.as_resume_payload())
        applied_receipt = state.get("effect_result_receipt_digest")
        if applied_receipt is not None:
            if _secure_equal(str(applied_receipt), delivered_receipt):
                return self._outcome(
                    task_id,
                    override=WorkflowRunStatus.ALREADY_APPLIED,
                    reason_override="effect_result_already_applied",
                )
            raise ValueError("effect result conflicts with the already applied result")
        if PrimaryPhase.EFFECT_EXECUTION_READBACK.value not in snapshot.next:
            raise ValueError("primary graph has no pending effect result")
        if EffectStatus(str(state["effect_status"])) != EffectStatus.EXECUTING:
            raise ValueError("effect result requires a durably executing intent")
        if (
            not _secure_equal(
                result.effect_key_digest, str(state["effect_key_digest"])
            )
            or not _secure_equal(
                result.effect_attempt_digest,
                str(state["effect_attempt_digest"]),
            )
            or result.writer_owner_id != state["writer_owner_id"]
        ):
            raise ValueError("effect result does not match the executing intent")
        self._graph.invoke(
            Command(resume=result.as_resume_payload()), self._config(task_id)
        )
        self._drive_pending_boundaries(task_id)
        return self._outcome(task_id)

    def cancel(self, task_id: str) -> PrimaryOutcome:
        """Persist cancellation without reviving or redispatching pending work."""

        snapshot = self._snapshot(task_id)
        if not snapshot.values:
            raise ValueError("primary graph checkpoint does not exist")
        if snapshot.values.get("terminal_classification") is not None:
            return self._outcome(task_id)
        provider_status = ProviderDispatchStatus(
            str(snapshot.values["provider_dispatch_status"])
        )
        effect_status = EffectStatus(str(snapshot.values["effect_status"]))
        if provider_status == ProviderDispatchStatus.DISPATCHING:
            self._resume_ambiguous_provider(task_id, "cancelled_after_dispatch")
            return self._outcome(task_id)
        if effect_status == EffectStatus.EXECUTING:
            self._resume_ambiguous_effect(task_id, "cancelled_after_effect_start")
            return self._outcome(task_id)
        self._graph.update_state(
            self._config(task_id),
            {
                "cancel_requested": True,
                "transition_reason_code": "cancel_requested",
            },
        )
        self._graph.invoke(None, self._config(task_id))
        return self._outcome(task_id)

    def recover(self, task_id: str) -> PrimaryOutcome:
        """Inspect a persisted thread; ambiguous executing effects never repeat."""

        snapshot = self._snapshot(task_id)
        if not snapshot.values:
            raise ValueError("primary graph checkpoint does not exist")
        if self._fail_closed_invalid_post_plan_checkpoint(task_id):
            return self._outcome(task_id)
        self._drive_pending_boundaries(task_id)
        return self._outcome(task_id)

    def compact_state(self, task_id: str) -> Mapping[str, Any]:
        """Return a deeply immutable audit snapshot of the compact checkpoint.

        Immutability is structural, not conventional: the mapping is read-only
        and every nested value is rebuilt as a tuple, frozenset, or read-only
        mapping.  No object reachable from the result aliases the live
        checkpoint, so an auditor can neither edit the snapshot nor reach
        through it -- for example through ``visited_nodes`` -- to edit the
        checkpoint the snapshot describes.
        """

        snapshot = self._snapshot(task_id)
        if not snapshot.values:
            raise ValueError("primary graph checkpoint does not exist")
        return _freeze_audit_value(dict(snapshot.values))


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:43 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 10:28 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 11:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | END]]
