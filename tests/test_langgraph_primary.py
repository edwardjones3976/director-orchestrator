# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:27 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:17 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 11:40 UTC | BEGIN]]
# [[Cursor Agent | Composer | 2026-08-31 | 10:55 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 10:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:43 UTC | BEGIN]]
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from typing import Any, Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from director_orchestrator.langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    ApprovalDecision,
    ApprovalKind,
    ApprovalResume,
    ClaimOutcome,
    ClaimRequest,
    ClaimStoreUnavailableError,
    CompletionGateResult,
    EffectDisposition,
    EffectExecutionResult,
    EffectStatus,
    EvidenceDebateResult,
    ExactShaReviewResult,
    InProcessConditionalClaimStore,
    PhaseResult,
    PlanResult,
    PrimaryHandlers,
    PrimaryPhase,
    PrimaryRequest,
    PrimaryWorkflowRuntime,
    ProviderDispatchStatus,
    ProviderDisposition,
    ProviderSubgraphResult,
    ScheduleResult,
    TerminalClassification,
    WorkflowRunStatus,
    build_primary_graph,
    primary_checkpoint_config,
)


SHA_A = "a" * 40
SHA_B = "b" * 40

# How many sibling owners contend for one reservation in the race tests.
CONTENDERS = 6

# Every key the durable checkpoint is allowed to carry.  Each one is a compact
# identifier, exact SHA, counter, boolean, or SHA-256 digest; anything else
# reaching the checkpoint has to be added here deliberately.
CHECKPOINT_KEY_ALLOWLIST = frozenset(
    {
        "acceptance_decision",
        "acceptance_digest",
        "authenticate_scope_digest",
        "authority_snapshot_digest",
        "budget_limit_units",
        "budget_used_units",
        "cancel_requested",
        "candidate_sha",
        "canonical_digest",
        "ci_digest",
        "completion_digest",
        "correlation_id",
        "current_phase",
        "debate_digest",
        "dedup_payload_digest",
        "effect_approval_receipt_digest",
        "effect_attempt_digest",
        "effect_cost_units",
        "effect_key_digest",
        "effect_readback_digest",
        "effect_result_digest",
        "effect_result_receipt_digest",
        "effect_status",
        "evidence_digest",
        "execution_approval_receipt_digest",
        "execution_approval_required",
        "external_effect_required",
        "graph_writer_active",
        "idempotency_key_digest",
        "input_digest",
        "intake_digest",
        "legacy_writer_active",
        "max_revisions",
        "max_visits",
        "pending_approval_kind",
        "pending_approval_request_digest",
        "parallel_aggregate_digest",
        "parallel_ambiguous_slot_count",
        "parallel_plan_digest",
        "parallel_succeeded",
        "parallel_terminal",
        "plan_digest",
        "plan_work_packet_binding_digest",
        "policy_digest",
        "previous_phase",
        "producer_id",
        "provider_attempt_digest",
        "provider_cost_units",
        "provider_dispatch_status",
        "provider_result_digest",
        "provider_result_receipt_digest",
        "review_binding_digest",
        "review_digest",
        "review_producer_id",
        "reviewed_sha",
        "reviewer_id",
        "revision_count",
        "schedule_digest",
        "scheduled_producer_id",
        "schema_version",
        "scope_digest",
        "security_digest",
        "source_event_id",
        "starting_sha",
        "task_id",
        "terminal_classification",
        "timeout_expired",
        "transition_reason_code",
        "visit_count",
        "visited_nodes",
        "work_packet_digest",
        "writer_owner_id",
    }
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _request(task_id: str, **updates) -> PrimaryRequest:
    request = PrimaryRequest(
        task_id=task_id,
        source_event_id=f"event-{task_id}",
        correlation_id=f"correlation-{task_id}",
        intake_digest=_digest(f"intake:{task_id}"),
        scope_digest=_digest(f"scope:{task_id}"),
        authority_snapshot_digest=_digest(f"authority:{task_id}"),
        work_packet_digest=_digest(f"packet:{task_id}"),
        idempotency_key_digest=_digest(f"idempotency:{task_id}"),
        starting_sha=SHA_A,
        budget_limit_units=8,
        provider_cost_units=1,
        max_visits=64,
        max_revisions=1,
        execution_approval_required=False,
    )
    return replace(request, **updates)


def _handlers(
    counters: dict[str, int] | None = None,
    **overrides,
) -> PrimaryHandlers:
    counts = counters if counters is not None else {}

    def counted(name, result_factory):
        def handler(context):
            counts[name] = counts.get(name, 0) + 1
            return result_factory(context)

        return handler

    defaults = {
        "authenticate_scope": counted(
            "authenticate_scope",
            lambda context: PhaseResult(_digest(f"auth:{context.task_id}")),
        ),
        "canonicalize": counted(
            "canonicalize",
            lambda context: PhaseResult(_digest(f"canon:{context.authority_snapshot_digest}")),
        ),
        "plan": counted(
            "plan",
            lambda context: PlanResult(
                _digest(f"plan:{context.task_id}"),
                _digest(f"validated-work-packet:{context.task_id}"),
            ),
        ),
        "policy_budget": counted(
            "policy_budget",
            lambda context: PhaseResult(_digest(f"policy:{context.scope_digest}")),
        ),
        "schedule": counted(
            "schedule",
            lambda context: ScheduleResult(
                _digest(f"schedule:{context.task_id}:{context.revision_count}"),
                "claude",
            ),
        ),
        "provider_subgraph": counted(
            "provider_subgraph",
            lambda context: ProviderSubgraphResult(
                provider_attempt_digest=context.provider_attempt_digest,
                idempotency_key_digest=context.idempotency_key_digest,
                authority_snapshot_digest=context.authority_snapshot_digest,
                producer_id=context.scheduled_producer_id,
                disposition=ProviderDisposition.COMPLETED,
                result_digest=_digest(
                    f"provider:{context.task_id}:{context.revision_count}"
                ),
                candidate_sha=SHA_A,
            ),
        ),
        "evidence_debate": counted(
            "evidence_debate",
            lambda context: EvidenceDebateResult(
                _digest(f"evidence:{context.candidate_sha}:{context.revision_count}"),
                _digest(f"debate:{context.candidate_sha}:{context.revision_count}"),
                context.candidate_sha,
            ),
        ),
        "openai_acceptance_revision": counted(
            "openai_acceptance_revision",
            lambda context: AcceptanceResult(
                _digest(f"acceptance:{context.candidate_sha}:{context.revision_count}"),
                context.candidate_sha,
                AcceptanceDecision.ACCEPT,
            ),
        ),
        "exact_sha_review": counted(
            "exact_sha_review",
            lambda context: ExactShaReviewResult(
                _digest(f"review:{context.candidate_sha}"),
                context.candidate_sha,
                context.producer_id,
                "cursor",
            ),
        ),
        "ci_security_completion": counted(
            "ci_security_completion",
            lambda context: CompletionGateResult(
                _digest(f"ci:{context.candidate_sha}"),
                _digest(f"security:{context.candidate_sha}"),
                _digest(f"completion:{context.candidate_sha}"),
                context.candidate_sha,
            ),
        ),
        "effect_execution_readback": counted(
            "effect_execution_readback",
            lambda context: EffectExecutionResult(
                context.effect_key_digest or _digest("missing-effect-key"),
                context.effect_attempt_digest or _digest("missing-effect-attempt"),
                context.writer_owner_id,
                EffectDisposition.COMPLETED,
                result_digest=_digest(f"effect-result:{context.task_id}"),
                readback_digest=_digest(f"effect-readback:{context.task_id}"),
            ),
        ),
    }
    defaults.update(overrides)
    return PrimaryHandlers(**defaults)


class _ClaimOnlyTestRuntime(PrimaryWorkflowRuntime):
    """Test-only facade retaining local claim-store coverage without a public bypass."""

    def _requires_factory_bound_control_plane(self) -> bool:
        return False

    def __init__(self, saver, handlers, **kwargs):
        from director_orchestrator.langgraph_primary import AcceptanceJournal, SqliteAcceptanceJournal
        store = kwargs.get("claim_store")
        while hasattr(store, "_inner"):
            store = store._inner
        if isinstance(store, AcceptanceJournal):
            journal = store
        else:
            journal = getattr(saver, "_test_acceptance_journal", None)
            if journal is None:
                directory = tempfile.TemporaryDirectory(prefix="acceptance-test-")
                journal = SqliteAcceptanceJournal(os.path.join(directory.name, "acceptance.sqlite3"))
                journal._test_directory = directory
                saver._test_acceptance_journal = journal
        kwargs.setdefault("acceptance_store", journal)
        super().__init__(saver, handlers, **kwargs)


def _runtime(
    handlers: PrimaryHandlers | None = None,
    *,
    saver: InMemorySaver | None = None,
    claim_store: Any | None = None,
    **kwargs,
) -> PrimaryWorkflowRuntime:
    """Build the facade over an in-memory checkpointer and a real claim store.

    Side-effecting transitions refuse to proceed without an injected conditional
    store, so every test that expects a dispatch supplies one exactly as a
    deployment must.  The tests that assert the fail-closed boundary construct
    :class:`PrimaryWorkflowRuntime` directly, deliberately without a store.
    """

    return _ClaimOnlyTestRuntime(
        saver if saver is not None else InMemorySaver(),
        handlers if handlers is not None else _handlers(),
        claim_store=(
            claim_store
            if claim_store is not None
            else InProcessConditionalClaimStore()
        ),
        **kwargs,
    )


def _approval(outcome, event_id: str) -> ApprovalResume:
    assert outcome.pending_approval is not None
    return ApprovalResume(
        task_id=outcome.task_id,
        kind=outcome.pending_approval.kind,
        request_digest=outcome.pending_approval.request_digest,
        approval_event_id=event_id,
        decision=ApprovalDecision.APPROVE,
    )


from director_orchestrator.langgraph_primary import SqliteAcceptanceJournal


class _SqlConditionalClaimStore(SqliteAcceptanceJournal):
    """A conditional claim store whose grant is decided by real SQL.

    The statement below is the one a Postgres deployment runs, and the grant is
    decided the same way it is decided there: by the affected-row count of a
    single ``INSERT ... ON CONFLICT (claim_key) DO NOTHING``.

    The database is a real file, and every ``claim`` opens its own connection to
    it, so concurrent callers contend inside SQLite exactly as concurrent
    sessions contend inside Postgres.  There is no shared cursor, no shared
    connection, and no application-level lock anywhere in the decision: the
    winner is chosen by the primary-key conflict, and the writer lock plus
    ``busy_timeout`` serialize the contenders.  A temporary directory keeps the
    file local to the test run; nothing is written to any external service.

    What this deliberately does not reproduce, and is not claimed to: multiple
    hosts, replication, failover, and network partitions.  Those belong to the
    deployed SQL implementation of the claim-store protocol, which lives outside
    this module.
    """

    _INSERT = (
        "INSERT INTO director_primary_claim"
        " (claim_key, task_id, field, expected_state, target_state,"
        "  attempt_digest, owner_token)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (claim_key) DO NOTHING"
    )

    def __init__(self) -> None:
        self._directory = tempfile.mkdtemp(prefix="director-primary-claim-")
        self._path = os.path.join(self._directory, "claims.sqlite3")
        connection = self._connect()
        try:
            # Write-ahead logging is what a concurrent SQL deployment runs; it
            # also keeps a losing contender waiting on the writer lock instead
            # of failing the statement outright.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE director_primary_claim ("
                " claim_key TEXT PRIMARY KEY,"
                " task_id TEXT NOT NULL,"
                " field TEXT NOT NULL,"
                " expected_state TEXT NOT NULL,"
                " target_state TEXT NOT NULL,"
                " attempt_digest TEXT NOT NULL,"
                " owner_token TEXT NOT NULL)"
            )
        finally:
            connection.close()

        super().__init__(self._path)

    def _connect(self) -> sqlite3.Connection:
        """Open one private autocommit connection to the shared database file."""

        connection = sqlite3.connect(self._path, isolation_level=None, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def claim(self, request: ClaimRequest) -> ClaimOutcome:
        connection = self._connect()
        try:
            cursor = connection.execute(
                self._INSERT,
                (
                    request.claim_key,
                    request.task_id,
                    request.field,
                    request.expected_state,
                    request.target_state,
                    request.attempt_digest,
                    request.owner_token,
                ),
            )
            # The grant is this statement's own affected-row count.  The row is
            # then read back for the refusal detail; because the key is a
            # primary key the row can only ever have one author, so the read is
            # never racy even though it is a second statement.
            granted = cursor.rowcount == 1
            state, owner = connection.execute(
                "SELECT target_state, owner_token FROM director_primary_claim"
                " WHERE claim_key = ?",
                (request.claim_key,),
            ).fetchone()
        finally:
            connection.close()
        return ClaimOutcome(
            claim_key=request.claim_key,
            granted=granted,
            owner_token=owner,
            state=state,
            reason_code="claim_granted" if granted else "claim_already_held",
        )

    def rows(self, *, field: str | None = None) -> int:
        connection = self._connect()
        try:
            if field is None:
                row = connection.execute(
                    "SELECT COUNT(*) FROM director_primary_claim"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM director_primary_claim WHERE field = ?",
                    (field,),
                ).fetchone()
        finally:
            connection.close()
        return int(row[0])

    def close(self) -> None:
        shutil.rmtree(self._directory, ignore_errors=True)


class _DurableControlPlane:
    """Deterministic structural stand-in for the factory-opened Postgres store."""

    storage_identity = "postgres:sha256:" + "d" * 64

    def __init__(self, owner_token: str, events: list[str]) -> None:
        self.owner_token = owner_token
        self.events = events
        self.claims: dict[str, ClaimRequest] = {}
        self.budgets: dict[str, int] = {}
        self.reservations: dict[str, SimpleNamespace] = {}

    def is_factory_bound_control_plane(self) -> bool:
        # Even a forged affirmative capability is insufficient without the
        # nominal factory-guarded Postgres type checked by production runtime.
        return True

    def current_task_writer(self, task_id: str):
        self.events.append("current_writer")
        return SimpleNamespace(
            task_id=task_id,
            engine_owner="langgraph-primary",
            owner_token=self.owner_token,
            fencing_generation=1,
        )

    def register_task_budget(self, task_id: str, *, budget_limit_units: int) -> int:
        self.events.append("register_budget")
        recorded = self.budgets.setdefault(task_id, budget_limit_units)
        if recorded != budget_limit_units:
            raise ValueError("immutable budget mismatch")
        return recorded

    def claim(self, request: ClaimRequest) -> ClaimOutcome:
        self.events.append(f"claim:{request.field}")
        held = self.claims.setdefault(request.claim_key, request)
        return ClaimOutcome(
            claim_key=request.claim_key,
            granted=held is request,
            owner_token=held.owner_token,
            state=held.target_state,
            reason_code="claim_granted" if held is request else "claim_already_held",
        )

    def reserve_effect_intent(self, **values):
        self.events.append(f"reserve:{values['writer_owner_id']}")
        existing = self.reservations.get(values["claim_key"])
        candidate = SimpleNamespace(**values)
        if existing is not None:
            if vars(existing) != vars(candidate):
                raise ValueError("reservation conflict")
            return existing
        self.reservations[values["claim_key"]] = candidate
        return candidate


class _DurableTestRuntime(PrimaryWorkflowRuntime):
    """Test-only override for exercising the full durable ordering contract."""

    @staticmethod
    def _is_durable_control_plane(store: object) -> bool:
        return isinstance(store, _DurableControlPlane)


class _BarrieredClaimStore:
    """Hold every contender inside ``claim`` so only the fence can separate them.

    By the time a contender blocks here it has already read the checkpoint and
    seen the status it expects, because nothing writes the checkpoint until a
    claim has been granted.  The pre-flight read therefore cannot be what
    rejects the losers: once the barrier releases, the inner store's exclusion
    is the only thing left that can decide the winner.
    """

    def __init__(self, inner: Any, parties: int) -> None:
        self._inner = inner
        self._barrier = threading.Barrier(parties)
        self._lock = threading.Lock()
        self._entered = 0

    def claim(self, request: ClaimRequest) -> ClaimOutcome:
        with self._lock:
            self._entered += 1
        self._barrier.wait(timeout=30)
        return self._inner.claim(request)

    @property
    def entered(self) -> int:
        with self._lock:
            return self._entered


def _race(
    target: Callable[[int], Any], parties: int
) -> tuple[list[Any], list[BaseException]]:
    """Run ``target`` on ``parties`` real OS threads; return results and errors."""

    results: list[Any] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def run(index: int) -> None:
        try:
            value = target(index)
        except BaseException as error:  # recorded, then asserted on by the caller
            with guard:
                errors.append(error)
            return
        with guard:
            results.append(value)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("a racing contender did not finish")
    return results, errors


class ConditionalClaimStoreTests(unittest.TestCase):
    """The fence itself, before any runtime is allowed to depend on it."""

    @staticmethod
    def _claim(owner_token: str, *, attempt: str = "attempt") -> ClaimRequest:
        return ClaimRequest(
            claim_key=_digest(f"claim:provider:{attempt}"),
            task_id="DIR-PRIMARY-FENCE",
            field="provider_dispatch_status",
            expected_state=ProviderDispatchStatus.CLAIMED.value,
            target_state=ProviderDispatchStatus.DISPATCHING.value,
            attempt_digest=_digest(f"attempt:{attempt}"),
            owner_token=owner_token,
        )

    def test_a_claim_request_must_describe_a_real_status_change(self):
        with self.assertRaises(ValueError):
            replace(
                self._claim("owner-a"),
                target_state=ProviderDispatchStatus.CLAIMED.value,
            )
        with self.assertRaises(ValueError):
            replace(self._claim("owner-a"), claim_key="not-a-digest")
        with self.assertRaises(ValueError):
            replace(self._claim("owner-a"), expected_state="claimed")

    def test_the_in_process_fence_admits_exactly_one_racing_thread(self):
        store = InProcessConditionalClaimStore()
        barriered = _BarrieredClaimStore(store, CONTENDERS)

        outcomes, errors = _race(
            lambda index: barriered.claim(self._claim(f"owner-{index}")),
            CONTENDERS,
        )

        self.assertEqual(errors, [])
        # Every contender was inside the fence at the same moment.
        self.assertEqual(barriered.entered, CONTENDERS)
        self.assertEqual(len(outcomes), CONTENDERS)
        granted = [outcome for outcome in outcomes if outcome.granted]
        self.assertEqual(len(granted), 1)
        self.assertEqual(len(store.held_keys()), 1)
        for outcome in outcomes:
            self.assertEqual(outcome.owner_token, granted[0].owner_token)
            self.assertEqual(
                outcome.state, ProviderDispatchStatus.DISPATCHING.value
            )

    def test_a_distinct_attempt_receives_its_own_fence(self):
        store = InProcessConditionalClaimStore()

        first = store.claim(self._claim("owner-a", attempt="one"))
        replayed = store.claim(self._claim("owner-b", attempt="one"))
        second_attempt = store.claim(self._claim("owner-b", attempt="two"))

        self.assertTrue(first.granted)
        self.assertFalse(replayed.granted)
        self.assertEqual(replayed.reason_code, "claim_already_held")
        self.assertEqual(replayed.owner_token, "owner-a")
        self.assertTrue(second_attempt.granted)
        self.assertEqual(len(store.held_keys()), 2)

    def test_a_sql_conditional_store_admits_exactly_one_contender(self):
        store = _SqlConditionalClaimStore()
        self.addCleanup(store.close)

        first = store.claim(self._claim("owner-first"))
        losers = [store.claim(self._claim(f"owner-{index}")) for index in range(4)]

        self.assertTrue(first.granted)
        self.assertEqual(first.reason_code, "claim_granted")
        self.assertEqual([loser.granted for loser in losers], [False] * 4)
        for loser in losers:
            self.assertEqual(loser.owner_token, "owner-first")
            self.assertEqual(loser.reason_code, "claim_already_held")
            self.assertEqual(
                loser.state, ProviderDispatchStatus.DISPATCHING.value
            )
        self.assertEqual(store.rows(), 1)

    def test_a_sql_conditional_store_admits_one_winner_under_a_thread_race(self):
        """Independent connections to one shared database file, decided by SQL."""

        store = _SqlConditionalClaimStore()
        self.addCleanup(store.close)
        barriered = _BarrieredClaimStore(store, CONTENDERS)

        outcomes, errors = _race(
            lambda index: barriered.claim(self._claim(f"owner-{index}")),
            CONTENDERS,
        )

        self.assertEqual(errors, [])
        self.assertEqual(barriered.entered, CONTENDERS)
        self.assertEqual(len(outcomes), CONTENDERS)
        granted = [outcome for outcome in outcomes if outcome.granted]
        self.assertEqual(len(granted), 1)
        self.assertEqual(store.rows(), 1)
        # Every loser agrees on who holds the key it lost.
        for outcome in outcomes:
            self.assertEqual(outcome.owner_token, granted[0].owner_token)
            self.assertEqual(
                outcome.state, ProviderDispatchStatus.DISPATCHING.value
            )


class PrimaryWorkflowTests(unittest.TestCase):
    def test_missing_authenticated_evidence_stops_for_owner_not_hard_failure(self):
        handlers = _handlers(
            evidence_debate=lambda context: EvidenceDebateResult(
                _digest("missing-evidence"),
                _digest("missing-debate"),
                context.candidate_sha,
                passed=False,
                reason_code="owner_decision_required",
            )
        )
        outcome = _runtime(handlers).start(_request("DIR-PRIMARY-EVIDENCE-OWNER"))
        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.OWNER_DECISION_REQUIRED,
        )
        self.assertEqual(outcome.reason_code, "owner_decision_required")

    def test_missing_review_and_completion_evidence_stop_for_owner(self):
        review_handlers = _handlers(
            exact_sha_review=lambda context: ExactShaReviewResult(
                _digest("missing-review"),
                context.candidate_sha,
                context.producer_id,
                "cursor",
                accepted=False,
                reason_code="review_evidence_unavailable",
            )
        )
        review = _runtime(review_handlers).start(
            _request("DIR-PRIMARY-REVIEW-OWNER")
        )
        self.assertEqual(
            review.terminal_classification,
            TerminalClassification.OWNER_DECISION_REQUIRED,
        )
        completion_handlers = _handlers(
            ci_security_completion=lambda context: CompletionGateResult(
                _digest("missing-ci"),
                _digest("missing-security"),
                _digest("missing-completion"),
                context.candidate_sha,
                passed=False,
                reason_code="completion_evidence_unavailable",
            )
        )
        completion = _runtime(completion_handlers).start(
            _request("DIR-PRIMARY-COMPLETION-OWNER")
        )
        self.assertEqual(
            completion.terminal_classification,
            TerminalClassification.OWNER_DECISION_REQUIRED,
        )

    def test_schema_workload_hold_is_checkpointed_and_survives_recovery(self):
        saver = InMemorySaver()
        claims = InProcessConditionalClaimStore()
        counters = {}
        runtime = _runtime(_handlers(counters), saver=saver, claim_store=claims)
        request = _request("DIR-SCHEMA-WORKLOAD-HOLD", execution_approval_required=True)
        runtime.start(request)
        aggregate_digest = _digest("schema-held-aggregate")
        runtime.record_parallel_aggregate(request.task_id,
            plan_digest=_digest("schema-held-plan"), aggregate_digest=aggregate_digest,
            terminal=True, succeeded=False, ambiguous_slot_count=0)
        held = runtime.record_parallel_workload_hold(request.task_id,
            aggregate_digest=aggregate_digest,
            reason_code="workload_schema_policy_incompatible")
        self.assertEqual(held.terminal_classification,
                         TerminalClassification.OWNER_DECISION_REQUIRED)
        state = runtime._snapshot(request.task_id).values
        self.assertEqual(state["transition_reason_code"], "workload_schema_policy_incompatible")
        self.assertIsNone(state.get("provider_result_receipt_digest"))
        self.assertEqual(counters.get("provider_subgraph", 0), 0)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)
        before = dict(counters)
        restarted = _runtime(_handlers(counters), saver=saver, claim_store=claims)
        recovered = restarted.recover(request.task_id)
        self.assertEqual(recovered.terminal_classification,
                         TerminalClassification.OWNER_DECISION_REQUIRED)
        self.assertEqual(restarted._snapshot(request.task_id).values["transition_reason_code"],
                         "workload_schema_policy_incompatible")
        self.assertEqual(counters, before)
        self.assertEqual(restarted.record_parallel_workload_hold(request.task_id,
            aggregate_digest=aggregate_digest,
            reason_code="workload_schema_policy_incompatible"), recovered)
        with self.assertRaisesRegex(ValueError, "unknown workload hold reason"):
            restarted.record_parallel_workload_hold(request.task_id,
                aggregate_digest=aggregate_digest, reason_code="untrusted_schema_override")

    def test_parallel_aggregate_is_checkpointed_with_immutable_plan_binding(self):
        runtime = _runtime()
        request = _request("DIR-PRIMARY-PARALLEL-AGGREGATE")
        runtime.start(request)
        plan_digest = _digest("parallel-plan")
        first = runtime.record_parallel_aggregate(
            request.task_id,
            plan_digest=plan_digest,
            aggregate_digest=_digest("parallel-partial"),
            terminal=False,
            succeeded=False,
            ambiguous_slot_count=1,
        )
        self.assertEqual(first.parallel_plan_digest, plan_digest)
        self.assertEqual(first.parallel_ambiguous_slot_count, 1)
        final = runtime.record_parallel_aggregate(
            request.task_id,
            plan_digest=plan_digest,
            aggregate_digest=_digest("parallel-final"),
            terminal=True,
            succeeded=True,
            ambiguous_slot_count=0,
        )
        self.assertTrue(final.parallel_terminal)
        self.assertTrue(final.parallel_succeeded)
        with self.assertRaisesRegex(ValueError, "plan binding changed"):
            runtime.record_parallel_aggregate(
                request.task_id,
                plan_digest=_digest("different-parallel-plan"),
                aggregate_digest=_digest("parallel-final"),
                terminal=True,
                succeeded=True,
                ambiguous_slot_count=0,
            )

    def test_happy_path_visits_every_non_effect_control_plane_phase(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        graph = build_primary_graph(handlers).get_graph()
        self.assertTrue(
            {phase.value for phase in PrimaryPhase}.issubset(set(graph.nodes))
        )

        runtime = _runtime(handlers)
        request = _request("DIR-PRIMARY-HAPPY")
        outcome = runtime.start(request)

        self.assertEqual(outcome.run_status, WorkflowRunStatus.TERMINAL)
        self.assertEqual(
            outcome.terminal_classification, TerminalClassification.SUCCEEDED
        )
        self.assertEqual(
            outcome.visited_nodes,
            (
                PrimaryPhase.AUTHENTICATE_SCOPE.value,
                PrimaryPhase.CANONICALIZE.value,
                PrimaryPhase.PLAN.value,
                PrimaryPhase.POLICY_BUDGET.value,
                PrimaryPhase.SCHEDULE.value,
                PrimaryPhase.PROVIDER_DISPATCH_CLAIM.value,
                PrimaryPhase.PROVIDER_SUBGRAPH.value,
                PrimaryPhase.EVIDENCE_DEBATE.value,
                PrimaryPhase.OPENAI_ACCEPTANCE_REVISION.value,
                PrimaryPhase.EXACT_SHA_REVIEW.value,
                PrimaryPhase.CI_SECURITY_COMPLETION.value,
                PrimaryPhase.TERMINAL_CLASSIFICATION.value,
            ),
        )
        self.assertEqual(counters.get("provider_subgraph"), 1)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

        state = dict(runtime.compact_state(request.task_id))
        serialized = json.dumps(state, sort_keys=True).lower()
        self.assertNotIn("secret", serialized)
        self.assertNotIn("transcript", serialized)
        self.assertNotIn("prompt", serialized)
        self.assertEqual(state["producer_id"], "claude")
        self.assertEqual(state["reviewer_id"], "cursor")
        self.assertIsNotNone(state["review_binding_digest"])
        self.assertEqual(
            primary_checkpoint_config(request.task_id)["configurable"]["thread_id"],
            f"director-primary-v1:{request.task_id}",
        )

    def test_plan_result_replaces_pre_plan_digest_before_downstream_work(self):
        observed: list[str | None] = []
        actual = _digest("actual-validated-work-packet")

        def plan(context):
            self.assertIsNone(context.work_packet_digest)
            return PlanResult(_digest("actual-plan"), actual)

        def policy(context):
            observed.append(context.work_packet_digest)
            return PhaseResult(_digest("policy"))

        runtime = _runtime(_handlers(plan=plan, policy_budget=policy))
        request = _request(
            "DIR-PRIMARY-POST-PLAN-AUTHORITY",
            work_packet_digest=_digest("untrusted-preview"),
        )

        outcome = runtime.start(request)

        self.assertEqual(TerminalClassification.SUCCEEDED, outcome.terminal_classification)
        self.assertEqual([actual], observed)
        state = runtime.compact_state(request.task_id)
        self.assertEqual(actual, state["work_packet_digest"])
        self.assertIsNotNone(state["plan_work_packet_binding_digest"])

    def test_non_plan_result_never_becomes_work_packet_authority(self):
        counters: dict[str, int] = {}
        handlers = _handlers(
            counters,
            plan=lambda _: PhaseResult(_digest("legacy-plan-result")),
        )
        outcome = _runtime(handlers).start(
            _request("DIR-PRIMARY-LEGACY-PLAN-RESULT")
        )

        self.assertEqual(TerminalClassification.FAILED, outcome.terminal_classification)
        self.assertEqual("plan_handler_error", outcome.reason_code)
        self.assertEqual(0, counters.get("policy_budget", 0))
        self.assertEqual(0, counters.get("provider_subgraph", 0))

    def test_restart_with_missing_post_plan_binding_fails_before_resume(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request(
            "DIR-PRIMARY-MISSING-POST-PLAN-BINDING",
            execution_approval_required=True,
        )
        paused = runtime.start(request)
        self.assertEqual(WorkflowRunStatus.PAUSED, paused.run_status)
        runtime._graph.update_state(  # deliberate checkpoint-corruption test
            runtime._config(request.task_id),
            {"plan_work_packet_binding_digest": None},
        )

        outcome = runtime.resume(_approval(paused, "approval-missing-packet-binding"))

        self.assertEqual(TerminalClassification.POLICY_BLOCKED, outcome.terminal_classification)
        self.assertEqual("work_packet_authority_invalid", outcome.reason_code)
        self.assertEqual(0, counters.get("provider_subgraph", 0))

    def test_restart_reclassifies_terminal_checkpoint_with_lost_packet_binding(self):
        runtime = _runtime()
        request = _request("DIR-PRIMARY-TERMINAL-PACKET-BINDING-LOST")
        completed = runtime.start(request)
        self.assertEqual(TerminalClassification.SUCCEEDED, completed.terminal_classification)
        runtime._graph.update_state(  # deliberate checkpoint-corruption test
            runtime._config(request.task_id),
            {"plan_work_packet_binding_digest": None},
        )

        recovered = runtime.recover(request.task_id)

        self.assertEqual(TerminalClassification.POLICY_BLOCKED, recovered.terminal_classification)
        self.assertEqual("work_packet_authority_invalid", recovered.reason_code)

    def test_malformed_post_plan_digest_cannot_be_claimed_for_dispatch(self):
        counters: dict[str, int] = {}
        runtime = _runtime(
            _handlers(counters),
            auto_execute_provider=False,
        )
        request = _request("DIR-PRIMARY-MALFORMED-POST-PLAN-DIGEST")
        pending = runtime.start(request)
        self.assertEqual(PrimaryPhase.PROVIDER_SUBGRAPH.value, pending.phase)
        runtime._graph.update_state(  # deliberate checkpoint-corruption test
            runtime._config(request.task_id),
            {"work_packet_digest": "sha256:not-valid"},
        )

        with self.assertRaisesRegex(ValueError, "not claimable"):
            runtime.prepare_provider_dispatch(request.task_id)
        self.assertEqual(0, counters.get("provider_subgraph", 0))

    def test_exact_approval_resume_is_idempotent(self):
        runtime = _runtime()
        request = _request(
            "DIR-PRIMARY-APPROVAL",
            execution_approval_required=True,
        )

        paused = runtime.start(request)
        self.assertEqual(paused.run_status, WorkflowRunStatus.PAUSED)
        self.assertEqual(paused.pending_approval.kind, ApprovalKind.EXECUTION)

        resume = _approval(paused, "approval-primary-1")
        completed = runtime.resume(resume)
        replay = runtime.resume(resume)

        self.assertEqual(completed.run_status, WorkflowRunStatus.TERMINAL)
        self.assertEqual(
            completed.terminal_classification, TerminalClassification.SUCCEEDED
        )
        self.assertEqual(replay.run_status, WorkflowRunStatus.ALREADY_APPLIED)
        self.assertEqual(replay.state_digest, completed.state_digest)

    def test_restart_and_terminal_replay_do_not_repeat_handlers(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = InProcessConditionalClaimStore()
        request = _request("DIR-PRIMARY-RESTART")

        first_runtime = _runtime(
            handlers,
            saver=saver,
            claim_store=store,
            interrupt_after=[PrimaryPhase.PROVIDER_SUBGRAPH.value],
        )
        interrupted = first_runtime.start(request)
        self.assertEqual(interrupted.run_status, WorkflowRunStatus.PAUSED)
        self.assertEqual(counters.get("provider_subgraph"), 1)

        restarted_runtime = _runtime(handlers, saver=saver, claim_store=store)
        completed = restarted_runtime.start(request)
        replay = restarted_runtime.start(request)

        self.assertEqual(
            completed.terminal_classification, TerminalClassification.SUCCEEDED
        )
        self.assertEqual(replay.state_digest, completed.state_digest)
        self.assertEqual(counters.get("provider_subgraph"), 1)
        self.assertEqual(counters.get("exact_sha_review"), 1)
        # Each claimed attempt has one fence: the restart never duplicated it.
        self.assertEqual(len(store.held_keys()), 1)

    def test_restart_after_durable_provider_reservation_never_redispatches(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = InProcessConditionalClaimStore()
        request = _request("DIR-PRIMARY-PROVIDER-CRASH")

        first_runtime = _runtime(
            handlers,
            saver=saver,
            claim_store=store,
            auto_execute_provider=False,
        )
        pending = first_runtime.start(request)
        self.assertEqual(pending.phase, PrimaryPhase.PROVIDER_SUBGRAPH.value)
        self.assertEqual(
            pending.provider_dispatch_status, ProviderDispatchStatus.CLAIMED
        )
        self.assertEqual(pending.budget_used_units, request.provider_cost_units)
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

        intent = first_runtime.prepare_provider_dispatch(request.task_id)
        reserved = first_runtime.compact_state(request.task_id)
        self.assertEqual(
            reserved["provider_dispatch_status"],
            ProviderDispatchStatus.DISPATCHING.value,
        )
        self.assertEqual(
            intent.idempotency_key_digest, request.idempotency_key_digest
        )

        restarted_runtime = _runtime(handlers, saver=saver, claim_store=store)
        reconciled = restarted_runtime.recover(request.task_id)
        replay = restarted_runtime.recover(request.task_id)

        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(
            reconciled.provider_dispatch_status,
            ProviderDispatchStatus.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(replay.state_digest, reconciled.state_digest)
        self.assertEqual(counters.get("provider_subgraph", 0), 0)
        # The fence is never released, so the attempt can only be reconciled.
        self.assertEqual(len(store.held_keys()), 1)

    def test_exact_provider_result_redelivery_is_already_applied(self):
        counters: dict[str, int] = {}
        runtime = _runtime(
            _handlers(counters),
            auto_execute_provider=False,
        )
        request = _request("DIR-PRIMARY-PROVIDER-RESULT-REDELIVERY")
        runtime.start(request)
        intent = runtime.prepare_provider_dispatch(request.task_id)
        result = ProviderSubgraphResult(
            provider_attempt_digest=intent.provider_attempt_digest,
            idempotency_key_digest=intent.idempotency_key_digest,
            authority_snapshot_digest=intent.authority_snapshot_digest,
            producer_id=intent.producer_id,
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest("provider-result-redelivery"),
            candidate_sha=SHA_A,
        )

        applied = runtime.record_provider_result(request.task_id, result)
        replay = runtime.record_provider_result(request.task_id, result)

        self.assertEqual(applied.run_status, WorkflowRunStatus.TERMINAL)
        self.assertEqual(replay.run_status, WorkflowRunStatus.ALREADY_APPLIED)
        self.assertEqual(replay.reason_code, "provider_result_already_applied")
        self.assertEqual(replay.state_digest, applied.state_digest)
        self.assertEqual(counters.get("provider_subgraph", 0), 0)
        with self.assertRaisesRegex(ValueError, "conflicts with the already applied"):
            runtime.record_provider_result(
                request.task_id,
                ProviderSubgraphResult(
                    provider_attempt_digest=intent.provider_attempt_digest,
                    idempotency_key_digest=intent.idempotency_key_digest,
                    authority_snapshot_digest=intent.authority_snapshot_digest,
                    producer_id=intent.producer_id,
                    disposition=ProviderDisposition.COMPLETED,
                    result_digest=_digest("conflicting-provider-result"),
                    candidate_sha=SHA_B,
                ),
            )
        self.assertEqual(runtime.start(request).state_digest, applied.state_digest)

    def test_exact_effect_result_redelivery_is_already_applied(self):
        counters: dict[str, int] = {}
        runtime = _runtime(
            _handlers(counters),
            auto_execute_effect=False,
        )
        request = _request(
            "DIR-PRIMARY-EFFECT-RESULT-REDELIVERY",
            external_effect_required=True,
            effect_key_digest=_digest("effect-result-redelivery-key"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        paused = runtime.start(request)
        claimed = runtime.resume(_approval(paused, "approval-effect-redelivery"))
        self.assertEqual(claimed.effect_status, EffectStatus.CLAIMED)
        attempt_digest = runtime.prepare_effect_execution(request.task_id)
        result = EffectExecutionResult(
            effect_key_digest=request.effect_key_digest,
            effect_attempt_digest=attempt_digest,
            writer_owner_id=request.writer_owner_id,
            disposition=EffectDisposition.COMPLETED,
            result_digest=_digest("effect-result-redelivery"),
            readback_digest=_digest("effect-readback-redelivery"),
        )

        applied = runtime.record_effect_result(request.task_id, result)
        replay = runtime.record_effect_result(request.task_id, result)

        self.assertEqual(applied.run_status, WorkflowRunStatus.TERMINAL)
        self.assertEqual(replay.run_status, WorkflowRunStatus.ALREADY_APPLIED)
        self.assertEqual(replay.reason_code, "effect_result_already_applied")
        self.assertEqual(replay.state_digest, applied.state_digest)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)
        with self.assertRaisesRegex(ValueError, "conflicts with the already applied"):
            runtime.record_effect_result(
                request.task_id,
                EffectExecutionResult(
                    effect_key_digest=request.effect_key_digest,
                    effect_attempt_digest=attempt_digest,
                    writer_owner_id=request.writer_owner_id,
                    disposition=EffectDisposition.COMPLETED,
                    result_digest=_digest("conflicting-effect-result"),
                    readback_digest=_digest("conflicting-effect-readback"),
                ),
            )
        self.assertEqual(runtime.start(request).state_digest, applied.state_digest)

    def test_mismatched_provider_result_binding_fails_to_reconciliation(self):
        counters: dict[str, int] = {}

        def mismatched_result(context):
            counters["provider_subgraph"] = counters.get("provider_subgraph", 0) + 1
            return ProviderSubgraphResult(
                provider_attempt_digest=context.provider_attempt_digest,
                idempotency_key_digest=_digest("wrong-provider-idempotency"),
                authority_snapshot_digest=context.authority_snapshot_digest,
                producer_id=context.scheduled_producer_id,
                disposition=ProviderDisposition.COMPLETED,
                result_digest=_digest("mismatched-provider-result"),
                candidate_sha=SHA_A,
            )

        handlers = _handlers(counters, provider_subgraph=mismatched_result)
        runtime = _runtime(handlers)
        request = _request("DIR-PRIMARY-PROVIDER-BINDING")

        outcome = runtime.start(request)
        replay = runtime.start(request)

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(
            outcome.provider_dispatch_status,
            ProviderDispatchStatus.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(replay.state_digest, outcome.state_digest)
        self.assertEqual(counters.get("provider_subgraph"), 1)
        self.assertEqual(counters.get("evidence_debate", 0), 0)

    def test_stale_approval_is_rejected_without_consuming_interrupt(self):
        runtime = _runtime()
        request = _request(
            "DIR-PRIMARY-STALE",
            execution_approval_required=True,
        )
        paused = runtime.start(request)
        valid = _approval(paused, "approval-current")
        stale = replace(valid, request_digest=_digest("stale-approval-scope"))

        rejected = runtime.resume(stale)
        still_paused = runtime.recover(request.task_id)
        completed = runtime.resume(valid)

        self.assertEqual(rejected.run_status, WorkflowRunStatus.REJECTED)
        self.assertEqual(rejected.reason_code, "stale_approval")
        self.assertEqual(still_paused.run_status, WorkflowRunStatus.PAUSED)
        self.assertEqual(
            still_paused.pending_approval.request_digest,
            paused.pending_approval.request_digest,
        )
        self.assertEqual(
            completed.terminal_classification, TerminalClassification.SUCCEEDED
        )

    def test_budget_and_cancellation_stop_before_provider_handler(self):
        for suffix, updates, expected in (
            (
                "BUDGET",
                {"budget_limit_units": 0, "provider_cost_units": 1},
                TerminalClassification.BUDGET_EXHAUSTED,
            ),
            (
                "CANCEL",
                {"cancel_requested": True},
                TerminalClassification.CANCELLED,
            ),
        ):
            with self.subTest(suffix=suffix):
                counters: dict[str, int] = {}
                runtime = _runtime(_handlers(counters))
                outcome = runtime.start(_request(f"DIR-PRIMARY-{suffix}", **updates))
                self.assertEqual(outcome.terminal_classification, expected)
                self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_ambiguous_effect_routes_to_reconciliation_and_never_repeats(self):
        counters: dict[str, int] = {}

        def ambiguous(context):
            counters["effect_execution_readback"] = (
                counters.get("effect_execution_readback", 0) + 1
            )
            return EffectExecutionResult(
                context.effect_key_digest,
                context.effect_attempt_digest,
                context.writer_owner_id,
                EffectDisposition.AMBIGUOUS,
                reason_code="transport_ambiguous",
            )

        handlers = _handlers(
            counters,
            effect_execution_readback=ambiguous,
        )
        runtime = _runtime(handlers)
        request = _request(
            "DIR-PRIMARY-AMBIGUOUS",
            external_effect_required=True,
            effect_key_digest=_digest("ambiguous-effect-key"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )

        approval_pause = runtime.start(request)
        self.assertEqual(
            approval_pause.pending_approval.kind, ApprovalKind.EXTERNAL_EFFECT
        )
        reconciled = runtime.resume(
            _approval(approval_pause, "approval-effect-ambiguous")
        )
        replay = runtime.start(request)
        explicit_replay = runtime.execute_pending_effect(request.task_id)

        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(
            reconciled.effect_status, EffectStatus.RECONCILIATION_REQUIRED
        )
        self.assertEqual(replay.state_digest, reconciled.state_digest)
        self.assertEqual(explicit_replay.state_digest, reconciled.state_digest)
        self.assertEqual(counters.get("effect_execution_readback"), 1)

    def test_review_evidence_is_invalidated_when_bound_to_a_different_sha(self):
        counters: dict[str, int] = {}

        def stale_review(_context):
            counters["exact_sha_review"] = counters.get("exact_sha_review", 0) + 1
            return ExactShaReviewResult(
                _digest("stale-review"),
                SHA_B,
                _context.producer_id,
                "cursor",
            )

        handlers = _handlers(counters, exact_sha_review=stale_review)
        runtime = _runtime(handlers)
        request = _request("DIR-PRIMARY-STALE-SHA")

        outcome = runtime.start(request)
        state = runtime.compact_state(request.task_id)

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.EXACT_SHA_INVALID,
        )
        self.assertEqual(outcome.reason_code, "stale_review_sha")
        self.assertIsNone(state.get("review_digest"))
        self.assertIsNone(state.get("reviewed_sha"))
        self.assertEqual(counters.get("ci_security_completion", 0), 0)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_same_provider_review_is_rejected_before_ci_or_effects(self):
        counters: dict[str, int] = {}

        def same_provider_review(context):
            counters["exact_sha_review"] = counters.get("exact_sha_review", 0) + 1
            return ExactShaReviewResult(
                _digest(f"same-provider:{context.candidate_sha}"),
                context.candidate_sha,
                context.producer_id,
                context.producer_id,
            )

        handlers = _handlers(counters, exact_sha_review=same_provider_review)
        runtime = _runtime(handlers)
        request = _request(
            "DIR-PRIMARY-SAME-REVIEWER",
            external_effect_required=True,
            effect_key_digest=_digest("same-reviewer-effect"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )

        outcome = runtime.start(request)
        state = runtime.compact_state(request.task_id)

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.POLICY_BLOCKED,
        )
        self.assertEqual(outcome.reason_code, "same_provider_review")
        self.assertEqual(state["producer_id"], "claude")
        self.assertEqual(state["review_producer_id"], "claude")
        self.assertEqual(state["reviewer_id"], "claude")
        self.assertIsNone(state["review_binding_digest"])
        self.assertEqual(counters.get("ci_security_completion", 0), 0)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_production_acceptance_journal_is_canonical_store(self):
        from unittest.mock import patch
        from director_orchestrator.langgraph_postgres import PostgresConditionalClaimStore
        canonical = object.__new__(PostgresConditionalClaimStore)
        separate = object.__new__(PostgresConditionalClaimStore)
        with tempfile.TemporaryDirectory() as directory:
            sqlite = SqliteAcceptanceJournal(os.path.join(directory, "override.sqlite3"))
            with patch.object(PrimaryWorkflowRuntime, "_is_durable_control_plane", return_value=True), \
                 patch("director_orchestrator.langgraph_primary.build_primary_graph") as build:
                for override in (sqlite, separate):
                    with self.subTest(override=type(override).__name__), self.assertRaisesRegex(ValueError, "canonical"):
                        PrimaryWorkflowRuntime(InMemorySaver(), _handlers(), claim_store=canonical,
                                               acceptance_store=override)
                build.assert_not_called()
                PrimaryWorkflowRuntime(InMemorySaver(), _handlers(), claim_store=canonical)
                self.assertIs(build.call_args.kwargs["acceptance_store"], canonical)
                PrimaryWorkflowRuntime(InMemorySaver(), _handlers(), claim_store=canonical,
                                       acceptance_store=canonical)
                self.assertIs(build.call_args.kwargs["acceptance_store"], canonical)

    def test_acceptance_create_disconnect_requires_reconciliation_without_second_create(self):
        from unittest.mock import patch
        gate = patch("director_orchestrator.primary_transports.ACCEPTANCE_ABSOLUTE_DEADLINE_ENFORCED", True)
        gate.start()
        self.addCleanup(gate.stop)
        import httpx
        import time
        from director_orchestrator.primary_transports import (
            OpenAIAcceptanceTransport, AcceptanceRequestPlan, ACCEPTANCE_RESPONSE_SCHEMA)
        from director_orchestrator.langgraph_primary import _canonical_digest
        calls = []
        def respond(request):
            calls.append(request)
            if request.url.path.endswith("input_tokens"):
                return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 10},
                    headers={"x-request-id": "fixture"})
            raise httpx.ReadTimeout("fixture create outcome unknown")
        transport = OpenAIAcceptanceTransport("fake-key", client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(respond), trust_env=False))
        plan = AcceptanceRequestPlan(model="fixture-model", authority_digest=_digest("authority"),
            tariff_digest=_digest("tariff"), schema_digest=_canonical_digest(ACCEPTANCE_RESPONSE_SCHEMA),
            valid_until=int(time.time()) + 300, max_input=100, max_output=16, max_cache_write=100,
            input_rate="1", output_rate="1", cache_write_rate="1", fee_bound="0", scope="graph-fixture")
        with tempfile.TemporaryDirectory() as directory:
            journal = SqliteAcceptanceJournal(os.path.join(directory, "acceptance.sqlite3"))
            class Handler:
                def prepare_acceptance(self, context):
                    return {"fixture": "transport"}, plan.reservation()
                def execute_acceptance(self, context, store, key, owner):
                    return transport.execute(plan=plan, evidence={"fixture": "evidence"}, context=context,
                        journal=store, key=key, owner=owner, recheck=lambda: None)
            runtime = _runtime(_handlers(openai_acceptance_revision=Handler()), acceptance_store=journal)
            request = _request("DIR-ACCEPTANCE-HTTP-UNRESOLVED")
            outcome = runtime.start(request)
            self.assertEqual(outcome.terminal_classification, TerminalClassification.RECONCILIATION_REQUIRED)
            self.assertEqual(outcome.reason_code, "acceptance_unresolved")
            self.assertEqual(sum(r.url.path == "/v1/responses" for r in calls), 1)
            self.assertEqual(len(calls), 2)

    def test_acceptance_reentry_claim_database_failure_keeps_paid_attempt_unresolved(self):
        import sqlite3
        import psycopg
        from unittest.mock import patch
        from director_orchestrator.langgraph_primary import acceptance_identity
        for error in (sqlite3.OperationalError("readback exit uncertain"),
                      psycopg.OperationalError("connection lost"),
                      psycopg.errors.TransactionResolutionUnknown("commit unknown")):
            for completed in (False, True):
                with self.subTest(error=type(error), completed=completed), tempfile.TemporaryDirectory() as directory:
                    journal = SqliteAcceptanceJournal(os.path.join(directory, "journal.sqlite3"))
                    original_claim = journal.acceptance_claim
                    prior = []
                    executions = []
                    class Handler:
                        def prepare_acceptance(self, context):
                            reservation = dict(cents=5, requests=2, limit_cents=5, limit_requests=2)
                            key, binding = acceptance_identity(context, {})
                            _, row = original_claim(key, binding, **reservation)
                            journal.acceptance_http(key, row["owner"], "create")
                            journal.acceptance_response_id(key, row["owner"], "resp-prior")
                            if completed:
                                journal.acceptance_finish(key, row["owner"], AcceptanceResult(
                                    _digest("prior-paid"), context.candidate_sha, AcceptanceDecision.ACCEPT))
                            prior.append(journal.acceptance_read(key))
                            return {}, reservation
                        def execute_acceptance(self, *args):
                            executions.append(args)
                            raise AssertionError("must not dispatch again")
                    with patch.object(journal, "acceptance_claim", side_effect=error) as claim:
                        runtime = _runtime(_handlers(openai_acceptance_revision=Handler()), acceptance_store=journal)
                        outcome = runtime.start(_request("DIR-REENTRY-DB-UNCERTAIN"))
                    self.assertEqual(outcome.terminal_classification, TerminalClassification.RECONCILIATION_REQUIRED)
                    self.assertEqual(outcome.reason_code, "acceptance_unresolved")
                    self.assertEqual(claim.call_count, 1)
                    self.assertEqual(executions, [])
                    held = journal.acceptance_read(prior[0]["operation_key"])
                    self.assertEqual(held, prior[0])
                    self.assertEqual((held["response_id"], held["reserved_cents"], held["used_requests"]),
                                     ("resp-prior", 5, 1))

    def test_acceptance_finish_database_failures_classified_unresolved(self):
        import sqlite3
        import psycopg
        from unittest.mock import patch
        for error in (sqlite3.OperationalError("exit uncertain"),
                      psycopg.OperationalError("commit disconnected"),
                      psycopg.errors.TransactionResolutionUnknown("commit unknown")):
            for boundary in ("acceptance_response_id", "acceptance_finish"):
                for committed in (False, True):
                    with self.subTest(error=type(error), boundary=boundary, committed=committed), tempfile.TemporaryDirectory() as directory:
                        journal = SqliteAcceptanceJournal(os.path.join(directory, "journal.sqlite3"))
                        original = getattr(journal, boundary)
                        calls = []
                        def uncertain(*args):
                            calls.append(args)
                            if committed:
                                original(*args)
                            raise error
                        class Handler:
                            def prepare_acceptance(self, context):
                                return {}, dict(requests=1, limit_requests=1)
                            def execute_acceptance(self, context, store, key, owner):
                                store.acceptance_http(key, owner, "create")
                                store.acceptance_response_id(key, owner, "resp-fixture")
                                return AcceptanceResult(_digest("paid"), context.candidate_sha, AcceptanceDecision.ACCEPT)
                        with patch.object(journal, boundary, side_effect=uncertain):
                            runtime = _runtime(_handlers(openai_acceptance_revision=Handler()), acceptance_store=journal)
                            outcome = runtime.start(_request("DIR-DB-UNCERTAIN"))
                        self.assertEqual(outcome.terminal_classification, TerminalClassification.RECONCILIATION_REQUIRED)
                        self.assertEqual(outcome.reason_code, "acceptance_unresolved")
                        self.assertEqual(len(calls), 1)
                        with journal.acceptance_transaction() as sql:
                            self.assertEqual(sql.execute("SELECT COUNT(*) FROM acceptance_http WHERE kind='create'").fetchone()[0], 1)

    def test_revision_and_visit_loops_are_bounded(self):
        counters: dict[str, int] = {}

        def always_revise(context):
            counters["openai_acceptance_revision"] = (
                counters.get("openai_acceptance_revision", 0) + 1
            )
            return AcceptanceResult(
                _digest(f"revise:{context.revision_count}"),
                context.candidate_sha,
                AcceptanceDecision.REVISE,
            )

        handlers = _handlers(
            counters,
            openai_acceptance_revision=always_revise,
        )
        store = InProcessConditionalClaimStore()
        runtime = _runtime(handlers, claim_store=store)
        revision_limited = runtime.start(
            _request(
                "DIR-PRIMARY-REVISION-BOUND",
                max_revisions=2,
                budget_limit_units=10,
            )
        )

        self.assertEqual(
            revision_limited.terminal_classification,
            TerminalClassification.REVISION_LIMIT,
        )
        self.assertEqual(revision_limited.revision_count, 2)
        self.assertEqual(counters.get("provider_subgraph"), 3)
        self.assertEqual(counters.get("openai_acceptance_revision"), 3)
        # Each revision is a genuinely new attempt, so it takes a fence of its
        # own rather than colliding with the previous attempt's fence.
        self.assertEqual(len(store.held_keys()), 3)

        visit_counts: dict[str, int] = {}
        visit_runtime = _runtime(_handlers(visit_counts))
        visit_limited = visit_runtime.start(
            _request("DIR-PRIMARY-VISIT-BOUND", max_visits=5)
        )
        self.assertEqual(
            visit_limited.terminal_classification,
            TerminalClassification.VISIT_LIMIT,
        )
        self.assertLessEqual(visit_limited.visit_count, 5)
        self.assertEqual(visit_counts.get("provider_subgraph", 0), 0)

    def test_dual_writer_state_never_calls_effect_handler(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request(
            "DIR-PRIMARY-DUAL-WRITER",
            external_effect_required=True,
            effect_key_digest=_digest("dual-writer-effect"),
            graph_writer_active=True,
            legacy_writer_active=True,
        )

        outcome = runtime.start(request)
        replay = runtime.start(request)

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.OWNER_DECISION_REQUIRED,
        )
        self.assertEqual(outcome.reason_code, "dual_writer")
        self.assertEqual(replay.state_digest, outcome.state_digest)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_expired_timeout_stops_before_any_handler_runs(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request("DIR-PRIMARY-TIMEOUT", timeout_expired=True)

        outcome = runtime.start(request)
        replay = runtime.start(request)

        self.assertEqual(
            outcome.terminal_classification, TerminalClassification.TIMED_OUT
        )
        self.assertEqual(outcome.reason_code, "timeout_expired")
        self.assertEqual(outcome.budget_used_units, 0)
        self.assertEqual(
            outcome.provider_dispatch_status, ProviderDispatchStatus.NOT_STARTED
        )
        self.assertEqual(replay.state_digest, outcome.state_digest)
        self.assertEqual(counters, {})

    def test_bounds_are_evaluated_before_a_result_interrupt_can_consume_one(self):
        """A bound reached at a result boundary must not buy a dropped result."""

        provider_counters: dict[str, int] = {}
        provider_runtime = _runtime(_handlers(provider_counters))
        provider_request = _request("DIR-PRIMARY-PROVIDER-BOUND", max_visits=6)
        provider_bound = provider_runtime.start(provider_request)

        self.assertEqual(
            provider_bound.terminal_classification,
            TerminalClassification.VISIT_LIMIT,
        )
        self.assertNotIn(
            PrimaryPhase.PROVIDER_SUBGRAPH.value, provider_bound.visited_nodes
        )
        self.assertEqual(provider_counters.get("provider_subgraph", 0), 0)
        # The durable pre-call reservation survives; nothing was dispatched for it.
        self.assertEqual(
            provider_bound.provider_dispatch_status, ProviderDispatchStatus.CLAIMED
        )
        self.assertEqual(
            provider_bound.budget_used_units, provider_request.provider_cost_units
        )

        effect_counters: dict[str, int] = {}
        effect_runtime = _runtime(_handlers(effect_counters))
        effect_request = _request(
            "DIR-PRIMARY-EFFECT-BOUND",
            max_visits=13,
            external_effect_required=True,
            effect_key_digest=_digest("effect-bound"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        approval_pause = effect_runtime.start(effect_request)
        effect_bound = effect_runtime.resume(
            _approval(approval_pause, "approval-effect-bound")
        )

        self.assertEqual(
            effect_bound.terminal_classification, TerminalClassification.VISIT_LIMIT
        )
        self.assertNotIn(
            PrimaryPhase.EFFECT_EXECUTION_READBACK.value, effect_bound.visited_nodes
        )
        self.assertEqual(effect_counters.get("effect_execution_readback", 0), 0)
        self.assertEqual(effect_bound.effect_status, EffectStatus.CLAIMED)

    def test_denied_execution_approval_is_recorded_and_never_dispatches(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request("DIR-PRIMARY-DENY", execution_approval_required=True)
        paused = runtime.start(request)
        denial = replace(
            _approval(paused, "approval-denied"), decision=ApprovalDecision.DENY
        )

        denied = runtime.resume(denial)
        replay = runtime.resume(denial)
        state = runtime.compact_state(request.task_id)

        self.assertEqual(
            denied.terminal_classification,
            TerminalClassification.OWNER_DECISION_REQUIRED,
        )
        self.assertEqual(denied.reason_code, "execution_approval_denied")
        self.assertIsNone(denied.pending_approval)
        self.assertEqual(
            state["execution_approval_receipt_digest"], denial.receipt_digest
        )
        self.assertIsNone(state["pending_approval_request_digest"])
        self.assertEqual(replay.run_status, WorkflowRunStatus.ALREADY_APPLIED)
        self.assertEqual(replay.state_digest, denied.state_digest)
        self.assertEqual(counters.get("schedule", 0), 0)
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_inactive_graph_writer_blocks_the_external_effect(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request(
            "DIR-PRIMARY-WRITER-INACTIVE",
            external_effect_required=True,
            effect_key_digest=_digest("writer-inactive-effect"),
            graph_writer_active=False,
            legacy_writer_active=True,
        )

        outcome = runtime.start(request)
        replay = runtime.start(request)

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.OWNER_DECISION_REQUIRED,
        )
        self.assertEqual(outcome.reason_code, "graph_writer_inactive")
        self.assertEqual(outcome.effect_status, EffectStatus.NOT_STARTED)
        self.assertIsNone(outcome.pending_approval)
        self.assertEqual(replay.state_digest, outcome.state_digest)
        self.assertEqual(counters.get("ci_security_completion"), 1)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_a_rebound_request_is_rejected_as_stale_workflow_input(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request(
            "DIR-PRIMARY-STALE-INPUT", execution_approval_required=True
        )
        paused = runtime.start(request)
        rebound = _request(
            "DIR-PRIMARY-STALE-INPUT",
            execution_approval_required=True,
            scope_digest=_digest("rebound-scope"),
        )

        rejected = runtime.start(rebound)
        still_paused = runtime.recover(request.task_id)

        self.assertNotEqual(rebound.input_digest, request.input_digest)
        self.assertEqual(rejected.run_status, WorkflowRunStatus.REJECTED)
        self.assertEqual(rejected.reason_code, "stale_workflow_input")
        self.assertEqual(rejected.state_digest, paused.state_digest)
        self.assertEqual(still_paused.run_status, WorkflowRunStatus.PAUSED)
        self.assertEqual(
            still_paused.pending_approval.request_digest,
            paused.pending_approval.request_digest,
        )
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_a_provider_reservation_has_one_owner_across_runtime_instances(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = InProcessConditionalClaimStore()
        request = _request("DIR-PRIMARY-CLAIM-OWNER")
        owner = _runtime(
            handlers, saver=saver, claim_store=store, auto_execute_provider=False
        )
        owner.start(request)

        intent = owner.prepare_provider_dispatch(request.task_id)
        self.assertEqual(
            owner.compact_state(request.task_id)["provider_dispatch_status"],
            ProviderDispatchStatus.DISPATCHING.value,
        )
        self.assertEqual(
            intent.idempotency_key_digest, request.idempotency_key_digest
        )

        # A dispatch already in flight is never handed to a second owner, and
        # never handed back to the first one either.
        contender = _runtime(handlers, saver=saver, claim_store=store)
        with self.assertRaises(ValueError):
            contender.prepare_provider_dispatch(request.task_id)
        with self.assertRaises(ValueError):
            owner.prepare_provider_dispatch(request.task_id)
        self.assertEqual(len(store.held_keys()), 1)

        reconciled = contender.recover(request.task_id)
        replay = contender.recover(request.task_id)

        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(reconciled.reason_code, "provider_outcome_unknown")
        self.assertEqual(
            reconciled.provider_dispatch_status,
            ProviderDispatchStatus.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(replay.state_digest, reconciled.state_digest)
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_an_effect_reservation_has_one_owner_across_runtime_instances(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = InProcessConditionalClaimStore()
        request = _request(
            "DIR-PRIMARY-EFFECT-OWNER",
            external_effect_required=True,
            effect_key_digest=_digest("effect-owner"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        owner = _runtime(
            handlers, saver=saver, claim_store=store, auto_execute_effect=False
        )
        paused = owner.start(request)
        claimed = owner.resume(_approval(paused, "approval-effect-owner"))
        self.assertEqual(claimed.effect_status, EffectStatus.CLAIMED)

        attempt_digest = owner.prepare_effect_execution(request.task_id)
        state = owner.compact_state(request.task_id)
        self.assertEqual(state["effect_status"], EffectStatus.EXECUTING.value)
        self.assertEqual(attempt_digest, state["effect_attempt_digest"])

        contender = _runtime(handlers, saver=saver, claim_store=store)
        with self.assertRaises(ValueError):
            contender.prepare_effect_execution(request.task_id)
        with self.assertRaises(ValueError):
            owner.prepare_effect_execution(request.task_id)
        # The task holds one provider fence and one effect fence; the claim that
        # matters here is the effect one, and it has exactly one owner.
        effect_claims = store.held_claims(field="effect_status")
        self.assertEqual(len(effect_claims), 1)
        self.assertEqual(effect_claims[0].attempt_digest, attempt_digest)
        self.assertEqual(effect_claims[0].target_state, EffectStatus.EXECUTING.value)
        self.assertEqual(
            len(store.held_claims(field="provider_dispatch_status")), 1
        )
        self.assertEqual(len(store.held_keys()), 2)

        reconciled = contender.recover(request.task_id)
        replay = contender.execute_pending_effect(request.task_id)

        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(reconciled.reason_code, "effect_outcome_unknown")
        self.assertEqual(
            reconciled.effect_status, EffectStatus.RECONCILIATION_REQUIRED
        )
        self.assertEqual(replay.state_digest, reconciled.state_digest)
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_a_provider_dispatch_race_grants_exactly_one_of_many_siblings(self):
        """Real threads, all past the status pre-check, separated only by the fence."""

        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = InProcessConditionalClaimStore()
        barriered = _BarrieredClaimStore(store, CONTENDERS)
        request = _request("DIR-PRIMARY-PROVIDER-RACE")

        starter = _runtime(
            handlers, saver=saver, claim_store=store, auto_execute_provider=False
        )
        pending = starter.start(request)
        self.assertEqual(
            pending.provider_dispatch_status, ProviderDispatchStatus.CLAIMED
        )

        siblings = [
            _runtime(
                handlers,
                saver=saver,
                claim_store=barriered,
                auto_execute_provider=False,
                owner_token=f"sibling-{index}",
            )
            for index in range(CONTENDERS)
        ]
        intents, errors = _race(
            lambda index: siblings[index].prepare_provider_dispatch(request.task_id),
            CONTENDERS,
        )

        # Every sibling read CLAIMED and reached the fence before anyone wrote.
        self.assertEqual(barriered.entered, CONTENDERS)
        self.assertEqual(len(intents), 1)
        self.assertEqual(len(errors), CONTENDERS - 1)
        self.assertTrue(all(isinstance(error, ValueError) for error in errors))
        self.assertEqual(len(store.held_keys()), 1)
        self.assertEqual(
            intents[0].idempotency_key_digest, request.idempotency_key_digest
        )
        self.assertEqual(
            starter.compact_state(request.task_id)["provider_dispatch_status"],
            ProviderDispatchStatus.DISPATCHING.value,
        )
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

        # A loser has exactly one route left, and it never reaches the provider.
        loser = _runtime(handlers, saver=saver, claim_store=store)
        reconciled = loser.recover(request.task_id)
        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(reconciled.reason_code, "provider_outcome_unknown")
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_an_effect_execution_race_grants_exactly_one_of_many_siblings(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = InProcessConditionalClaimStore()
        barriered = _BarrieredClaimStore(store, CONTENDERS)
        request = _request(
            "DIR-PRIMARY-EFFECT-RACE",
            external_effect_required=True,
            effect_key_digest=_digest("effect-race"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )

        starter = _runtime(
            handlers, saver=saver, claim_store=store, auto_execute_effect=False
        )
        paused = starter.start(request)
        claimed = starter.resume(_approval(paused, "approval-effect-race"))
        self.assertEqual(claimed.effect_status, EffectStatus.CLAIMED)

        siblings = [
            _runtime(
                handlers,
                saver=saver,
                claim_store=barriered,
                auto_execute_effect=False,
                owner_token=f"sibling-{index}",
            )
            for index in range(CONTENDERS)
        ]
        digests, errors = _race(
            lambda index: siblings[index].prepare_effect_execution(request.task_id),
            CONTENDERS,
        )

        self.assertEqual(barriered.entered, CONTENDERS)
        self.assertEqual(len(digests), 1)
        self.assertEqual(len(errors), CONTENDERS - 1)
        self.assertTrue(all(isinstance(error, ValueError) for error in errors))
        # Six siblings raced the effect fence; exactly one holds it.  The
        # provider fence this task already took is a different key, so the count
        # that means "one winner" is the per-field one, not the store total.
        effect_claims = store.held_claims(field="effect_status")
        self.assertEqual(len(effect_claims), 1)
        self.assertEqual(effect_claims[0].attempt_digest, digests[0])
        self.assertEqual(effect_claims[0].target_state, EffectStatus.EXECUTING.value)
        self.assertEqual(
            len(store.held_claims(field="provider_dispatch_status")), 1
        )
        self.assertEqual(len(store.held_keys()), 2)
        state = starter.compact_state(request.task_id)
        self.assertEqual(state["effect_status"], EffectStatus.EXECUTING.value)
        self.assertEqual(digests[0], state["effect_attempt_digest"])
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

        loser = _runtime(handlers, saver=saver, claim_store=store)
        reconciled = loser.recover(request.task_id)
        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(reconciled.reason_code, "effect_outcome_unknown")
        self.assertEqual(
            reconciled.effect_status, EffectStatus.RECONCILIATION_REQUIRED
        )
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_a_sql_fenced_runtime_dispatches_once_and_refuses_the_sibling(self):
        """End to end over the same conditional statement Postgres would run."""

        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        store = _SqlConditionalClaimStore()
        self.addCleanup(store.close)
        request = _request("DIR-PRIMARY-SQL-FENCE")

        owner = _runtime(
            handlers,
            saver=saver,
            claim_store=store,
            auto_execute_provider=False,
            owner_token="owner-primary",
        )
        owner.start(request)
        intent = owner.prepare_provider_dispatch(request.task_id)

        sibling = _runtime(
            handlers, saver=saver, claim_store=store, owner_token="owner-sibling"
        )
        with self.assertRaises(ValueError):
            sibling.prepare_provider_dispatch(request.task_id)

        self.assertEqual(store.rows(), 1)
        self.assertEqual(
            intent.idempotency_key_digest, request.idempotency_key_digest
        )
        reconciled = sibling.recover(request.task_id)
        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(reconciled.reason_code, "provider_outcome_unknown")
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_active_runtime_refuses_generic_and_forged_claim_stores(self):
        counters: dict[str, int] = {}
        candidates = (
            ("generic", InProcessConditionalClaimStore()),
            (
                "forged structural store and postgres identity",
                _DurableControlPlane("forged-owner", []),
            ),
        )
        for label, store in candidates:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "durable control plane"):
                    PrimaryWorkflowRuntime(
                        InMemorySaver(),
                        _handlers(counters),
                        claim_store=store,
                    )
        self.assertEqual(counters.get("provider_subgraph", 0), 0)

    def test_active_runtime_reserves_provider_and_effect_before_handler_io(self):
        events: list[str] = []
        counters: dict[str, int] = {}
        defaults = _handlers(counters)

        def provider(context):
            events.append("handler:provider")
            return defaults.provider_subgraph(context)

        def effect(context):
            events.append("handler:effect")
            return defaults.effect_execution_readback(context)

        owner_token = "owner-durable"
        acceptance_directory = tempfile.TemporaryDirectory(prefix="acceptance-ordering-")
        self.addCleanup(acceptance_directory.cleanup)
        class CanonicalTestStore(_DurableControlPlane, SqliteAcceptanceJournal):
            pass
        control_plane = CanonicalTestStore(owner_token, events)
        SqliteAcceptanceJournal.__init__(control_plane,
            os.path.join(acceptance_directory.name, "acceptance.sqlite3"))
        runtime = _DurableTestRuntime(
            InMemorySaver(),
            _handlers(
                counters,
                provider_subgraph=provider,
                effect_execution_readback=effect,
            ),
            claim_store=control_plane,
            acceptance_store=control_plane,
            owner_token=owner_token,
        )
        request = _request(
            "durable-ordering",
            external_effect_required=True,
            effect_key_digest=_digest("durable-effect"),
            effect_cost_units=2,
            graph_writer_active=True,
            legacy_writer_active=False,
        )

        paused = runtime.start(request)
        self.assertEqual(paused.pending_approval.kind, ApprovalKind.EXTERNAL_EFFECT)
        completed = runtime.resume(_approval(paused, "approve-durable-effect"))
        self.assertEqual(completed.terminal_classification, TerminalClassification.SUCCEEDED)
        self.assertEqual(
            events,
            [
                "current_writer",
                "register_budget",
                "claim:provider_dispatch_status",
                "reserve:langgraph-primary",
                "handler:provider",
                "current_writer",
                "register_budget",
                "claim:effect_status",
                "reserve:langgraph-primary",
                "handler:effect",
            ],
        )
        self.assertEqual(control_plane.budgets, {request.task_id: 8})
        self.assertEqual(len(control_plane.reservations), 2)
        reserved_units = sorted(
            reservation.reserved_units
            for reservation in control_plane.reservations.values()
        )
        self.assertEqual(reserved_units, [1, 2])
        self.assertEqual(counters.get("provider_subgraph"), 1)
        self.assertEqual(counters.get("effect_execution_readback"), 1)

        replay = runtime.start(request)
        self.assertEqual(replay.terminal_classification, TerminalClassification.SUCCEEDED)
        self.assertEqual(len(control_plane.reservations), 2)
        self.assertEqual(counters.get("provider_subgraph"), 1)
        self.assertEqual(counters.get("effect_execution_readback"), 1)
        self.assertEqual(events.count("handler:provider"), 1)
        self.assertEqual(events.count("handler:effect"), 1)

    def test_active_runtime_requires_the_current_task_writer(self):
        events: list[str] = []
        counters: dict[str, int] = {}
        control_plane = _DurableControlPlane("different-owner", events)
        runtime = _DurableTestRuntime(
            InMemorySaver(),
            _handlers(counters),
            claim_store=control_plane,
            owner_token="runtime-owner",
        )

        outcome = runtime.start(_request("stale-durable-writer"))

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(outcome.reason_code, "provider_claim_contended")
        self.assertEqual(events, ["current_writer"])
        self.assertEqual(counters.get("provider_subgraph", 0), 0)
        self.assertEqual(control_plane.budgets, {})
        self.assertEqual(control_plane.reservations, {})

    def test_a_provider_dispatch_fails_closed_without_a_conditional_store(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        # Deliberately constructed without a claim store.
        runtime = PrimaryWorkflowRuntime(InMemorySaver(), handlers)
        request = _request("DIR-PRIMARY-NO-STORE")

        outcome = runtime.start(request)

        self.assertEqual(
            outcome.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(outcome.reason_code, "provider_claim_store_unavailable")
        self.assertEqual(
            outcome.provider_dispatch_status,
            ProviderDispatchStatus.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(counters.get("provider_subgraph", 0), 0)
        with self.assertRaises(ClaimStoreUnavailableError):
            runtime.prepare_provider_dispatch(request.task_id)

    def test_an_external_effect_fails_closed_without_a_conditional_store(self):
        counters: dict[str, int] = {}
        handlers = _handlers(counters)
        saver = InMemorySaver()
        request = _request(
            "DIR-PRIMARY-NO-STORE-EFFECT",
            external_effect_required=True,
            effect_key_digest=_digest("no-store-effect"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        owner = _runtime(handlers, saver=saver, auto_execute_effect=False)
        paused = owner.start(request)
        claimed = owner.resume(_approval(paused, "approval-no-store-effect"))
        self.assertEqual(claimed.effect_status, EffectStatus.CLAIMED)

        # A sibling instance with no conditional store cannot execute the
        # effect, and cannot treat the missing store as permission to try.
        storeless = PrimaryWorkflowRuntime(saver, handlers)
        with self.assertRaises(ClaimStoreUnavailableError):
            storeless.prepare_effect_execution(request.task_id)
        reconciled = storeless.execute_pending_effect(request.task_id)

        self.assertEqual(
            reconciled.terminal_classification,
            TerminalClassification.RECONCILIATION_REQUIRED,
        )
        self.assertEqual(reconciled.reason_code, "effect_claim_store_unavailable")
        self.assertEqual(
            reconciled.effect_status, EffectStatus.RECONCILIATION_REQUIRED
        )
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_a_claim_store_without_the_protocol_method_is_rejected(self):
        with self.assertRaises(ValueError):
            PrimaryWorkflowRuntime(
                InMemorySaver(), _handlers(), claim_store=object()
            )

    def test_effect_budget_is_enforced_before_the_effect_is_claimed(self):
        counters: dict[str, int] = {}
        runtime = _runtime(_handlers(counters))
        request = _request(
            "DIR-PRIMARY-EFFECT-BUDGET",
            budget_limit_units=1,
            provider_cost_units=1,
            effect_cost_units=1,
            external_effect_required=True,
            effect_key_digest=_digest("effect-budget"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        paused = runtime.start(request)
        exhausted = runtime.resume(_approval(paused, "approval-effect-budget"))

        self.assertEqual(
            exhausted.terminal_classification,
            TerminalClassification.BUDGET_EXHAUSTED,
        )
        self.assertEqual(exhausted.reason_code, "effect_budget")
        self.assertEqual(exhausted.effect_status, EffectStatus.NOT_STARTED)
        self.assertEqual(
            exhausted.budget_used_units, request.provider_cost_units
        )
        self.assertEqual(counters.get("effect_execution_readback", 0), 0)

    def test_a_failed_effect_is_recorded_without_readback_evidence(self):
        counters: dict[str, int] = {}

        def failed_effect(context):
            counters["effect_execution_readback"] = (
                counters.get("effect_execution_readback", 0) + 1
            )
            return EffectExecutionResult(
                context.effect_key_digest,
                context.effect_attempt_digest,
                context.writer_owner_id,
                EffectDisposition.FAILED,
                result_digest=_digest("effect-rejected"),
                reason_code="effect_rejected_by_target",
            )

        handlers = _handlers(counters, effect_execution_readback=failed_effect)
        runtime = _runtime(handlers)
        request = _request(
            "DIR-PRIMARY-EFFECT-FAILED",
            external_effect_required=True,
            effect_key_digest=_digest("failed-effect"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        paused = runtime.start(request)
        failed = runtime.resume(_approval(paused, "approval-effect-failed"))
        replay = runtime.start(request)
        state = runtime.compact_state(request.task_id)

        self.assertEqual(
            failed.terminal_classification, TerminalClassification.FAILED
        )
        self.assertEqual(failed.reason_code, "effect_rejected_by_target")
        self.assertEqual(failed.effect_status, EffectStatus.FAILED)
        self.assertEqual(state["effect_result_digest"], _digest("effect-rejected"))
        self.assertIsNone(state["effect_readback_digest"])
        self.assertEqual(replay.state_digest, failed.state_digest)
        self.assertEqual(counters.get("effect_execution_readback"), 1)

    def test_checkpoint_state_matches_the_exact_compact_key_allowlist(self):
        runtime = _runtime()
        request = _request("DIR-PRIMARY-ALLOWLIST")
        runtime.start(request)
        state = runtime.compact_state(request.task_id)

        # The effect attempt digest is the one key a run without an external
        # effect never writes.
        self.assertEqual(
            set(state), CHECKPOINT_KEY_ALLOWLIST - {"effect_attempt_digest"}
        )

        effect_runtime = _runtime()
        effect_request = _request(
            "DIR-PRIMARY-ALLOWLIST-EFFECT",
            external_effect_required=True,
            effect_key_digest=_digest("allowlist-effect"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        paused = effect_runtime.start(effect_request)
        effect_runtime.resume(_approval(paused, "approval-allowlist"))
        effect_state = effect_runtime.compact_state(effect_request.task_id)

        self.assertEqual(set(effect_state), CHECKPOINT_KEY_ALLOWLIST)
        for key, value in effect_state.items():
            with self.subTest(key=key):
                if isinstance(value, tuple):
                    self.assertTrue(all(isinstance(item, str) for item in value))
                else:
                    self.assertIsInstance(value, (str, int, bool, type(None)))

    def test_compact_state_is_an_immutable_audit_copy(self):
        runtime = _runtime()
        request = _request("DIR-PRIMARY-AUDIT")
        runtime.start(request)

        state = runtime.compact_state(request.task_id)
        self.assertIsInstance(state, MappingProxyType)
        with self.assertRaises(TypeError):
            state["task_id"] = "DIR-PRIMARY-FORGED"  # type: ignore[index]
        with self.assertRaises(TypeError):
            del state["task_id"]  # type: ignore[attr-defined]

        refetched = runtime.compact_state(request.task_id)
        self.assertIsNot(state, refetched)
        self.assertEqual(dict(state), dict(refetched))
        self.assertEqual(refetched["task_id"], request.task_id)

    def test_nested_compact_state_values_cannot_be_mutated(self):
        """The inverse of a shallow copy: nested mutation must be impossible."""

        runtime = _runtime()
        request = _request("DIR-PRIMARY-AUDIT-NESTED")
        runtime.start(request)

        state = runtime.compact_state(request.task_id)
        recorded = state["visited_nodes"]
        self.assertIsInstance(recorded, tuple)
        self.assertTrue(recorded)

        # The exact escape a shallow copy leaves open no longer exists.
        with self.assertRaises(AttributeError):
            state["visited_nodes"].append("forged_phase")  # type: ignore[attr-defined]
        for mutator in (
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "clear",
            "sort",
            "reverse",
        ):
            with self.subTest(mutator=mutator):
                self.assertFalse(hasattr(recorded, mutator))
        with self.assertRaises(TypeError):
            recorded[0] = "forged_phase"  # type: ignore[index]
        with self.assertRaises(TypeError):
            del recorded[0]  # type: ignore[attr-defined]

        refetched = runtime.compact_state(request.task_id)
        self.assertEqual(refetched["visited_nodes"], recorded)
        self.assertNotIn("forged_phase", refetched["visited_nodes"])

    def test_every_value_reachable_from_compact_state_is_immutable(self):
        effect_runtime = _runtime()
        effect_request = _request(
            "DIR-PRIMARY-AUDIT-DEEP",
            external_effect_required=True,
            effect_key_digest=_digest("audit-deep-effect"),
            graph_writer_active=True,
            legacy_writer_active=False,
        )
        paused = effect_runtime.start(effect_request)
        effect_runtime.resume(_approval(paused, "approval-audit-deep"))
        state = effect_runtime.compact_state(effect_request.task_id)

        mutable_types = (list, dict, set, bytearray)
        immutable_types = (
            str,
            bytes,
            bool,
            int,
            float,
            tuple,
            frozenset,
            MappingProxyType,
            type(None),
        )
        pending: list[Any] = [state]
        inspected = 0
        while pending:
            value = pending.pop()
            inspected += 1
            self.assertNotIsInstance(value, mutable_types)
            self.assertIsInstance(value, immutable_types)
            if isinstance(value, MappingProxyType):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, (tuple, frozenset)):
                pending.extend(value)
        # Every allowlisted key, its value, and every nested visit was walked.
        self.assertGreater(inspected, 2 * len(CHECKPOINT_KEY_ALLOWLIST))


class PrimaryNodeContractTests(unittest.TestCase):
    """Node-level contracts that a facade caller cannot reach."""

    @staticmethod
    def _paused_graph(task_id: str, **updates):
        request = _request(task_id, **updates)
        graph = build_primary_graph(_handlers({}), checkpointer=InMemorySaver())
        config = primary_checkpoint_config(task_id)
        graph.invoke(request.graph_input(), config)
        return graph, config, request

    def test_a_mismatched_provider_result_never_binds_to_the_reservation(self):
        graph, config, request = self._paused_graph("DIR-PRIMARY-NODE-BINDING")
        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, (PrimaryPhase.PROVIDER_SUBGRAPH.value,))

        forged = ProviderSubgraphResult(
            provider_attempt_digest=snapshot.values["provider_attempt_digest"],
            idempotency_key_digest=snapshot.values["idempotency_key_digest"],
            authority_snapshot_digest=snapshot.values["authority_snapshot_digest"],
            producer_id="gemini",
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest("forged-provider-result"),
            candidate_sha=SHA_B,
        )
        graph.invoke(Command(resume=forged.as_resume_payload()), config)
        values = graph.get_state(config).values

        self.assertEqual(
            values["terminal_classification"],
            TerminalClassification.RECONCILIATION_REQUIRED.value,
        )
        self.assertEqual(
            values["transition_reason_code"], "provider_binding_mismatch"
        )
        self.assertEqual(
            values["provider_dispatch_status"],
            ProviderDispatchStatus.RECONCILIATION_REQUIRED.value,
        )
        self.assertIsNone(values["provider_result_digest"])
        self.assertEqual(values["candidate_sha"], request.starting_sha)

    def test_an_invalid_approval_payload_fails_closed_to_owner_decision(self):
        cases = (
            ("non-mapping payload", lambda task_id, digest: "approve"),
            ("unknown fields", lambda task_id, digest: {"unexpected": "shape"}),
            (
                "wrong gate",
                lambda task_id, digest: {
                    "task_id": task_id,
                    "kind": ApprovalKind.EXTERNAL_EFFECT.value,
                    "request_digest": digest,
                    "approval_event_id": "approval-wrong-gate",
                    "decision": ApprovalDecision.APPROVE.value,
                },
            ),
            (
                "stale request digest",
                lambda task_id, digest: {
                    "task_id": task_id,
                    "kind": ApprovalKind.EXECUTION.value,
                    "request_digest": _digest("stale-node-approval"),
                    "approval_event_id": "approval-stale",
                    "decision": ApprovalDecision.APPROVE.value,
                },
            ),
        )
        for index, (label, build_payload) in enumerate(cases):
            with self.subTest(label):
                task_id = f"DIR-PRIMARY-NODE-APPROVAL-{index}"
                graph, config, _ = self._paused_graph(
                    task_id, execution_approval_required=True
                )
                snapshot = graph.get_state(config)
                self.assertEqual(
                    snapshot.next, (PrimaryPhase.APPROVAL_INTERRUPT.value,)
                )
                payload = build_payload(
                    task_id, snapshot.values["pending_approval_request_digest"]
                )

                graph.invoke(Command(resume=payload), config)
                values = graph.get_state(config).values

                self.assertEqual(
                    values["terminal_classification"],
                    TerminalClassification.OWNER_DECISION_REQUIRED.value,
                )
                self.assertEqual(
                    values["transition_reason_code"], "approval_payload_invalid"
                )
                self.assertIsNone(values["pending_approval_kind"])
                self.assertIsNone(values["pending_approval_request_digest"])
                self.assertIsNone(values["execution_approval_receipt_digest"])

    def test_a_bound_reached_in_flight_never_drops_a_paid_result(self):
        graph, config, _ = self._paused_graph("DIR-PRIMARY-NODE-IN-FLIGHT")
        snapshot = graph.get_state(config)
        graph.update_state(
            config,
            {
                "provider_dispatch_status": ProviderDispatchStatus.DISPATCHING.value,
                "cancel_requested": True,
            },
        )
        paid = ProviderSubgraphResult(
            provider_attempt_digest=snapshot.values["provider_attempt_digest"],
            idempotency_key_digest=snapshot.values["idempotency_key_digest"],
            authority_snapshot_digest=snapshot.values["authority_snapshot_digest"],
            producer_id=snapshot.values["scheduled_producer_id"],
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest("paid-provider-result"),
            candidate_sha=SHA_A,
        )

        graph.invoke(Command(resume=paid.as_resume_payload()), config)
        values = graph.get_state(config).values

        # A cancellation arriving while a paid dispatch is in flight is an
        # ambiguity to reconcile, not a clean cancellation.
        self.assertEqual(
            values["terminal_classification"],
            TerminalClassification.RECONCILIATION_REQUIRED.value,
        )
        self.assertEqual(
            values["transition_reason_code"], "provider_dispatch_in_flight"
        )
        self.assertEqual(
            values["provider_dispatch_status"],
            ProviderDispatchStatus.RECONCILIATION_REQUIRED.value,
        )
        self.assertIsNone(values["provider_result_digest"])


class AcceptanceJournalTests(unittest.TestCase):
    """Real SQLite transactions, never provider transports."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="acceptance-journal-")
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "journal.sqlite3")
        self.store = SqliteAcceptanceJournal(self.path)
        self.key = _digest("acceptance-key")
        self.binding = {"task": "task", "candidate": SHA_A, "revision": 0}
        self.result = AcceptanceResult(_digest("accepted"), SHA_A, AcceptanceDecision.ACCEPT)

    def test_concurrent_connections_have_one_non_expiring_winner(self):
        results, errors = _race(lambda _: SqliteAcceptanceJournal(self.path).acceptance_claim(
            self.key, self.binding), CONTENDERS)
        self.assertEqual(errors, [])
        self.assertEqual(sum(won for won, _ in results), 1)
        won, row = SqliteAcceptanceJournal(self.path).acceptance_claim(self.key, self.binding)
        self.assertFalse(won)
        self.assertEqual(row["state"], "CLAIMED")
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        with self.assertRaises(AcceptanceUnresolved):
            self.store.acceptance_replay(row, SHA_A)

    def test_completed_result_survives_restart_and_detects_tampering(self):
        _, row = self.store.acceptance_claim(self.key, self.binding)
        self.store.acceptance_finish(self.key, row["owner"], self.result)
        restarted = SqliteAcceptanceJournal(self.path)
        won, completed = restarted.acceptance_claim(self.key, self.binding)
        self.assertFalse(won)
        self.assertEqual(restarted.acceptance_replay(completed, SHA_A), self.result)
        with self.assertRaises(ValueError):
            restarted.acceptance_replay(completed, SHA_B)
        with self.assertRaises(ValueError):
            restarted.acceptance_replay({**completed, "result_digest": _digest("tampered")}, SHA_A)
        with self.assertRaises(ValueError):
            restarted.acceptance_claim(self.key, {**self.binding, "revision": 1})

    def test_response_id_and_failed_result_commit_cannot_recreate(self):
        from director_orchestrator.langgraph_primary import AcceptanceUnresolved
        _, row = self.store.acceptance_claim(self.key, self.binding, cents=155, requests=5,
            scope="envelope", limit_cents=1000, limit_requests=5)
        self.store.acceptance_http(self.key, row["owner"], "create")
        with self.assertRaises(AcceptanceUnresolved):
            self.store.acceptance_http(self.key, row["owner"], "create")
        self.store.acceptance_response_id(self.key, row["owner"], "resp-one")
        with self.assertRaises(AcceptanceUnresolved):
            self.store.acceptance_finish(self.key, "wrong-owner", self.result)
        held = SqliteAcceptanceJournal(self.path).acceptance_read(self.key)
        self.assertEqual(held["state"], "RESPONSE_ID_RECORDED")
        self.assertEqual(held["response_id"], "resp-one")
        self.assertEqual(held["reserved_cents"], 155)
        self.assertEqual(held["used_requests"], 1)

    def test_aggregate_reservation_race_and_http_exhaustion(self):
        def reserve(index):
            try:
                return self.store.acceptance_claim(_digest(f"cost-{index}"), self.binding,
                    cents=200, requests=1, scope="envelope", limit_cents=1000, limit_requests=5)
            except ValueError:
                return None
        results, errors = _race(reserve, CONTENDERS)
        self.assertEqual(errors, [])
        self.assertEqual(sum(item is not None for item in results), 5)
        for item in results:
            if item is not None:
                _, row = item
                self.store.acceptance_http(row["operation_key"], row["owner"], "retrieve")
                from director_orchestrator.langgraph_primary import AcceptanceUnresolved
                with self.assertRaises(AcceptanceUnresolved):
                    self.store.acceptance_http(row["operation_key"], row["owner"], "cancel")
        with self.store.acceptance_transaction() as sql:
            budget = dict(sql.execute("SELECT * FROM acceptance_budget WHERE scope=?", ("envelope",)).fetchone())
        self.assertEqual((budget["cents"], budget["requests"]), (1000, 5))

    def test_refused_state_and_distinct_candidate_reservations(self):
        _, row = self.store.acceptance_claim(self.key, self.binding)
        rejected = replace(self.result, decision=AcceptanceDecision.REJECT)
        self.store.acceptance_finish(self.key, row["owner"], rejected)
        held = self.store.acceptance_read(self.key)
        self.assertEqual(held["state"], "REFUSED")
        self.assertEqual(self.store.acceptance_replay(held, SHA_A), rejected)
        won, _ = self.store.acceptance_claim(_digest("different-candidate"), {**self.binding, "candidate": SHA_B})
        self.assertTrue(won)

    def test_missing_or_in_process_storage_never_invokes_handler(self):
        from director_orchestrator.langgraph_primary import durable_acceptance, AcceptanceUnresolved
        for store in (None, InProcessConditionalClaimStore()):
            with self.subTest(store=store), self.assertRaises(AcceptanceUnresolved):
                durable_acceptance(lambda _: self.fail("handler must not run"), None, store)


if __name__ == "__main__":
    unittest.main()


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 09:43 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 10:28 UTC | END]]
# [[Cursor Agent | Composer | 2026-08-31 | 10:55 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-31 | 11:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 21:35 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
