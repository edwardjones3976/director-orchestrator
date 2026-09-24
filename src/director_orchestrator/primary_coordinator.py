# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:02 UTC | BEGIN]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | BEGIN]]
# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | BEGIN]]
# Administrative attribution only; material implementation remains Cursor-authored.
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 17:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 16:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:50 UTC | BEGIN]]
"""Postgres-owned application boundary for the primary LangGraph runtime."""

from __future__ import annotations

import hmac
from datetime import datetime
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Mapping

from .langgraph_postgres import (
    INITIAL_FENCING_GENERATION,
    IntakeAuthorityLease,
    PostgresCheckpointBinding,
    PostgresCheckpointHandle,
    PostgresConditionalClaimStore,
    PostgresControlPlaneError,
    EffectReservation,
    PrimaryCommandReceipt,
    PrimaryTaskProjection,
    TaskWriterLease,
)
from .langgraph_primary import (
    ApprovalResume,
    EffectExecutionResult,
    PrimaryHandlers,
    PrimaryOutcome,
    PrimaryRequest,
    PrimaryWorkflowRuntime,
    ProviderSubgraphResult,
)
from .packet_contracts import document_digest
from .primary_parallel import (
    ParallelAggregate,
    ParallelAssignment,
    ParallelAssignmentStatus,
    ParallelDispatchPolicy,
    ParallelDispatchOutcome,
    ParallelReservation,
    ProviderAttemptReservation,
    PostgresParallelDispatch,
    completion_deadline,
)
from .primary_effects import DeterministicExternalEffectExecutor
from .primary_completion import CompletionEvidence


class PrimaryCoordinatorError(RuntimeError):
    """A coordinator command failed closed before external I/O."""


@dataclass(frozen=True, slots=True)
class PrimaryCoordinatorReadiness:
    primary_graph_constructed: bool
    primary_router_wired: bool
    checkpoint_backend_postgres: bool
    checkpoint_schema_current: bool
    claim_store_ready: bool
    effect_ledger_ready: bool
    effect_executor_ready: bool
    writer_lease_ready: bool
    reconciliation_worker_ready: bool
    primary_runtime_active: bool
    new_intake_owner: str
    legacy_new_intake_disabled: bool
    intake_fencing_generation: int
    graph_task_count: int
    ambiguous_effect_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _outcome_projection(outcome: PrimaryOutcome) -> dict[str, Any]:
    return {
        "schema_version": "director-primary-projection/v1",
        "task_id": outcome.task_id,
        "run_status": outcome.run_status.value,
        "phase": outcome.phase,
        "terminal_classification": (
            None
            if outcome.terminal_classification is None
            else outcome.terminal_classification.value
        ),
        "reason_code": outcome.reason_code,
        "pending_approval": (
            None
            if outcome.pending_approval is None or (
                outcome.terminal_classification is not None
                and outcome.terminal_classification.value != "OWNER_DECISION_REQUIRED"
            )
            else {
                "kind": outcome.pending_approval.kind.value,
                "request_digest": outcome.pending_approval.request_digest,
            }
        ),
        "provider_dispatch_status": outcome.provider_dispatch_status.value,
        "effect_status": outcome.effect_status.value,
        "state_digest": outcome.state_digest,
        "visit_count": outcome.visit_count,
        "visited_nodes": list(outcome.visited_nodes),
        "revision_count": outcome.revision_count,
        "budget_used_units": outcome.budget_used_units,
        "parallel_plan_digest": outcome.parallel_plan_digest,
        "parallel_aggregate_digest": outcome.parallel_aggregate_digest,
        "parallel_terminal": outcome.parallel_terminal,
        "parallel_succeeded": outcome.parallel_succeeded,
        "parallel_ambiguous_slot_count": outcome.parallel_ambiguous_slot_count,
        "setup_hold_reasons": list(outcome.setup_hold_reasons),
    }


class PrimaryTaskCoordinator:
    """Sole application router for tasks whose immutable owner is LangGraph.

    Construction alone grants no authority. ``activate_new_intake`` performs
    the deployment-wide fenced transition. Every task creation re-reads that
    observed row and acquires a separate immutable task-writer lease before the
    graph can reach a provider or effect boundary.
    """

    def __init__(
        self,
        handle: PostgresCheckpointHandle,
        handlers: PrimaryHandlers,
        *,
        owner_token: str,
        checkpoint_namespace: str,
        auto_execute_provider: bool = False,
        auto_execute_effect: bool = False,
        effect_executor: DeterministicExternalEffectExecutor | None = None,
        application_projection_provider: (
            Callable[[str], Mapping[str, Any] | None] | None
        ) = None,
    ) -> None:
        binding = PostgresCheckpointBinding.from_handle(handle)
        if not owner_token.strip():
            raise ValueError("owner_token is required")
        self._handle = handle
        self._store: PostgresConditionalClaimStore = binding.claim_store
        self._owner_token = owner_token
        self._intake_generation: int | None = None
        self._active_task_ids: set[str] = set()
        self._application_projection_provider = application_projection_provider
        self._effect_executor = effect_executor
        self.parallel = PostgresParallelDispatch(binding.claim_store)
        runtime_handlers = (
            handlers
            if effect_executor is None
            else replace(
                handlers,
                effect_execution_readback=effect_executor,
            )
        )
        self._runtime = PrimaryWorkflowRuntime(
            binding.checkpointer,
            runtime_handlers,
            checkpoint_namespace=checkpoint_namespace,
            claim_store=binding.claim_store,
            owner_token=owner_token,
            auto_execute_provider=auto_execute_provider,
            auto_execute_effect=(auto_execute_effect or effect_executor is not None),
            reconcile_executing_effect=effect_executor is not None,
        )

    def activate_new_intake(
        self, *, expected_generation: int
    ) -> IntakeAuthorityLease:
        lease = self._store.acquire_intake_authority(
            intake_owner="langgraph",
            owner_token=self._owner_token,
            expected_generation=expected_generation,
        )
        self._intake_generation = lease.fencing_generation
        return lease

    def adopt_or_activate_new_intake(self) -> IntakeAuthorityLease:
        """Start or restart this exact deployment owner without dual intake."""

        observed = self._store.current_intake_authority()
        if (
            observed.intake_owner == "langgraph"
            and observed.legacy_new_intake_disabled
            and observed.lease_live
            and hmac.compare_digest(observed.owner_token, self._owner_token)
        ):
            self._intake_generation = observed.fencing_generation
            return self.renew_new_intake()
        return self.activate_new_intake(
            expected_generation=observed.fencing_generation
        )

    def renew_new_intake(self) -> IntakeAuthorityLease:
        generation = self._require_active_generation()
        return self._store.renew_intake_authority(
            intake_owner="langgraph",
            owner_token=self._owner_token,
            fencing_generation=generation,
        )

    def _require_active_generation(self) -> int:
        observed = self._store.current_intake_authority()
        generation = self._intake_generation
        if (
            generation is None
            or observed.intake_owner != "langgraph"
            or not observed.legacy_new_intake_disabled
            or not observed.lease_live
            or observed.fencing_generation != generation
            or not hmac.compare_digest(observed.owner_token, self._owner_token)
        ):
            raise PrimaryCoordinatorError(
                "LangGraph does not hold the current live new-intake authority"
            )
        return generation

    def _task_writer(
        self, task_id: str, *, deadline: float | None = None
    ) -> TaskWriterLease:
        """Resolve the current writer, optionally inside a caller's bound.

        Reading, renewing and reacquiring are each real database work that can
        wait on the writer row or its advisory lock. A caller that already has
        an absolute deadline passes it here so those waits are inside it; a
        caller without one keeps the previous unbounded behaviour exactly.
        """

        # An unbounded caller calls the store exactly as it always did.
        bound = {} if deadline is None else {"deadline": deadline}
        current = self._store.current_task_writer(task_id, **bound)
        if current is None:
            writer = self._store.acquire_task_writer(
                task_id,
                engine_owner="langgraph",
                owner_token=self._owner_token,
                expected_generation=INITIAL_FENCING_GENERATION,
                **bound,
            )
            self._mark_active_task(task_id)
            return writer
        if current.engine_owner != "langgraph":
            raise PrimaryCoordinatorError("task engine_owner is not LangGraph")
        if current.lease_live:
            if not hmac.compare_digest(current.owner_token, self._owner_token):
                raise PrimaryCoordinatorError(
                    "another process owns the task writer lease"
                )
            writer = self._store.renew_task_writer(
                task_id,
                owner_token=self._owner_token,
                fencing_generation=current.fencing_generation,
                **bound,
            )
            self._mark_active_task(task_id)
            return writer
        # An expired row is durable fencing history, not renewable authority.
        # Reacquire it through the store's exact-generation CAS so any
        # concurrent successor makes this observation stale and fails closed.
        writer = self._store.acquire_task_writer(
            task_id,
            engine_owner="langgraph",
            owner_token=self._owner_token,
            expected_generation=current.fencing_generation,
            **bound,
        )
        self._mark_active_task(task_id)
        return writer

    def _mark_active_task(self, task_id: str) -> None:
        active = getattr(self, "_active_task_ids", None)
        if active is None:
            active = set()
            self._active_task_ids = active
        active.add(task_id)

    def heartbeat_active_task_writers(self, *, on_renewal_outcome: Callable[[bool], None] | None = None) -> int:
        """Renew only tasks this process actually touched since startup."""

        renewed = 0
        failed = False
        active = getattr(self, "_active_task_ids", set())
        for task_id in tuple(active):
            current = self._store.current_task_writer(task_id)
            if (
                current is None
                or not current.lease_live
                or not hmac.compare_digest(current.owner_token, self._owner_token)
            ):
                failed = True
                active.discard(task_id)
                continue
            try:
                self._store.renew_task_writer(
                    task_id,
                    owner_token=self._owner_token,
                    fencing_generation=current.fencing_generation,
                )
            except PostgresControlPlaneError:
                failed = True
                active.discard(task_id)
                continue
            renewed += 1
        if on_renewal_outcome is not None:
            on_renewal_outcome(not failed)
        return renewed

    # Told (task_id, classification) after a terminal other than SUCCEEDED is
    # persisted; the Default application records a standing grant's trip.
    terminal_observer: Callable[[str, str], None] | None = None

    def _persist_outcome(
        self, outcome: PrimaryOutcome, writer: TaskWriterLease,
        *, deadline: float | None = None,
    ) -> PrimaryTaskProjection:
        """Publish one outcome projection, optionally under a completion cutoff."""

        payload = _outcome_projection(outcome)
        current = self._store.read_primary_task_projection(
            outcome.task_id, deadline=deadline)
        application = (
            self._application_projection_provider(outcome.task_id)
            if self._application_projection_provider is not None
            else None
        )
        if application is not None:
            payload["application"] = dict(application)
        elif current is not None and "application" in current.projection:
            payload["application"] = current.projection["application"]
        if (
            current is not None
            and current.projection.get("state_digest") == outcome.state_digest
        ):
            return current
        persisted = self._store.update_primary_task_projection(
            task_id=outcome.task_id,
            owner_token=self._owner_token,
            fencing_generation=writer.fencing_generation,
            projection=payload,
            deadline=deadline,
        )
        classification = payload.get("terminal_classification")
        if self.terminal_observer is not None and classification not in (None, "SUCCEEDED"):
            try:
                self.terminal_observer(outcome.task_id, classification)
            except Exception:
                # Never fails the persisted outcome; the durable derivation
                # at the next exercise and at recovery remains.
                pass
        return persisted

    def create_intake(self, command: PrimaryRequest) -> PrimaryTaskProjection:
        """Create or replay one new LangGraph-owned task without dual intake."""

        generation = self._require_active_generation()
        if not command.graph_writer_active or command.legacy_writer_active:
            raise PrimaryCoordinatorError(
                "primary intake requires graph writer active and legacy writer inactive"
            )
        initial = {
            "schema_version": "director-primary-projection/v1",
            "task_id": command.task_id,
            "run_status": "QUEUED",
            "phase": "intake",
            "terminal_classification": None,
            "reason_code": "intake_reserved",
            "pending_approval": None,
            "provider_dispatch_status": "NOT_STARTED",
            "effect_status": "NOT_STARTED",
            "state_digest": command.input_digest,
            "visit_count": 0,
            "visited_nodes": [],
            "revision_count": 0,
            "budget_used_units": 0,
        }
        if self._application_projection_provider is not None:
            application = self._application_projection_provider(command.task_id)
            if application is not None:
                initial["application"] = dict(application)
        reserved, created = self._store.create_primary_task_projection(
            task_id=command.task_id,
            input_digest=command.input_digest,
            idempotency_key_digest=command.idempotency_key_digest,
            dedup_payload_digest=(
                command.dedup_payload_digest or command.intake_digest
            ),
            owner_token=self._owner_token,
            intake_fencing_generation=generation,
            projection=initial,
        )
        if not created:
            return reserved
        writer = self._task_writer(command.task_id)
        try:
            outcome = self._runtime.start(command)
        except Exception as exc:
            raise PrimaryCoordinatorError("primary graph intake failed") from exc
        return self._persist_outcome(outcome, writer)

    def claim_authenticated_intake(
        self,
        *,
        nonce_digest: str,
        idempotency_key_digest: str,
        payload_digest: str,
        task_id: str,
    ) -> tuple[str, bool]:
        """Reserve the authenticated replay identity before any graph exists."""

        self._require_active_generation()
        return self._store.claim_authenticated_primary_intake(
            nonce_digest=nonce_digest,
            idempotency_key_digest=idempotency_key_digest,
            payload_digest=payload_digest,
            task_id=task_id,
        )

    def resolve_slack_thread_binding(
        self, *, channel_id: str, thread_ts: str
    ) -> tuple[str, ...]:
        """Resolve task identity from the persisted intake binding only."""

        return self._store.resolve_primary_slack_thread_binding(
            channel_id=channel_id,
            thread_ts=thread_ts,
        )

    def record_slack_status_receipt(
        self,
        *,
        source_event_id: str,
        receipt_id: str,
        task_id: str,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        text_digest: str,
    ) -> tuple[str, bool, datetime]:
        """Record one digest-only status receipt without graph side effects."""

        return self._store.record_primary_slack_status_receipt(
            source_event_id=source_event_id,
            receipt_id=receipt_id,
            task_id=task_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            text_digest=text_digest,
        )

    def resume_approval(self, command: ApprovalResume) -> PrimaryTaskProjection:
        writer = self._task_writer(command.task_id)
        return self._persist_outcome(
            self._runtime.resume_approval(command), writer
        )

    def record_provider_result(
        self, task_id: str, command: ProviderSubgraphResult
    ) -> PrimaryTaskProjection:
        writer = self._task_writer(task_id)
        return self._persist_outcome(
            self._runtime.record_provider_result(task_id, command), writer
        )

    def reconcile_effect(
        self, task_id: str, command: EffectExecutionResult
    ) -> PrimaryTaskProjection:
        writer = self._task_writer(task_id)
        reservation = self._store.read_effect_reservation(
            task_id=task_id,
            effect_attempt_digest=command.effect_attempt_digest,
            owner_token=self._owner_token,
        )
        if reservation is None:
            raise PrimaryCoordinatorError(
                "effect result has no current-writer Postgres reservation"
            )
        evidence_digest = document_digest(command.as_resume_payload())
        if command.disposition.value == "AMBIGUOUS":
            self._store.record_reconciliation_event(
                claim_key=reservation.claim_key,
                owner_token=self._owner_token,
                state="AMBIGUOUS",
                evidence_digest=evidence_digest,
            )
        else:
            self._store.record_terminal_evidence(
                claim_key=reservation.claim_key,
                owner_token=self._owner_token,
                disposition=command.disposition.value,
                evidence_digest=evidence_digest,
            )
        return self._persist_outcome(
            self._runtime.record_effect_result(task_id, command), writer
        )

    def prepare_external_effect(self, task_id: str) -> EffectReservation:
        """Reserve one effect in Postgres before an executor performs any I/O."""

        self._task_writer(task_id)
        attempt_digest = self._runtime.prepare_effect_execution(task_id)
        reservation = self._store.read_effect_reservation(
            task_id=task_id,
            effect_attempt_digest=attempt_digest,
            owner_token=self._owner_token,
        )
        if reservation is None:
            raise PrimaryCoordinatorError("effect reservation disappeared after claim")
        return reservation

    def bind_external_effect_remote(
        self,
        *,
        reservation: EffectReservation,
        remote_system: str,
        remote_identifier: str,
    ) -> None:
        """Bind a remote identifier after I/O; collisions fail closed."""

        self._task_writer(reservation.task_id)
        self._store.bind_remote_effect(
            reservation_key=reservation.reservation_key,
            owner_token=self._owner_token,
            remote_system=remote_system,
            remote_identifier=remote_identifier,
        )

    def read_external_effect_remote(
        self, reservation: EffectReservation
    ) -> Mapping[str, str] | None:
        """Authenticated current-writer readback, including during ambiguity."""

        self._task_writer(reservation.task_id)
        return self._store.read_remote_binding(
            reservation_key=reservation.reservation_key,
            owner_token=self._owner_token,
        )

    def register_parallel_plan(
        self,
        *,
        task_id: str,
        plan_digest: str,
        assignments: tuple[ParallelAssignment, ...],
        policy: ParallelDispatchPolicy,
    ) -> str:
        writer = self._task_writer(task_id)
        digest = self.parallel.register_plan(
            task_id=task_id,
            plan_digest=plan_digest,
            assignments=assignments,
            policy=policy,
            writer=writer,
        )
        aggregate = self.parallel.aggregate(task_id)
        self._persist_outcome(
            self._runtime.record_parallel_aggregate(
                task_id,
                plan_digest=digest,
                aggregate_digest=aggregate.aggregate_digest,
                terminal=aggregate.terminal,
                succeeded=aggregate.succeeded,
                ambiguous_slot_count=aggregate.ambiguous_slot_count,
            ),
            writer,
        )
        return digest

    def reserve_parallel_ready(
        self, *, task_id: str, policy: ParallelDispatchPolicy
    ) -> tuple[ParallelReservation, ...]:
        writer = self._task_writer(task_id)
        return self.parallel.reserve_ready(task_id=task_id, writer=writer, policy=policy)

    def parallel_aggregate(self, task_id: str) -> ParallelAggregate:
        """Read the deterministic all-assignment aggregate without mutation."""

        return self.parallel.aggregate(task_id)

    def reserve_provider_attempt(
        self,
        *,
        reservation: ParallelReservation,
        attempt_ordinal: int,
        format_roster: tuple[tuple[str, str], ...],
        idempotency_key_digest: str,
        budget_evidence_digest: str,
        team_admission=None,
        budget_admission=None,
        budget_limits=None,
        deadline: float | None = None,
        expected_authority_digest: str | None = None,
        expected_writer_generation: int | None = None,
        revalidate_selection=None,
    ) -> ProviderAttemptReservation:
        # Resolving the writer can itself read, renew or reacquire a lease;
        # that work belongs inside the same bound as the admission it is
        # for, not ahead of it.
        writer = self._task_writer(reservation.task_id, deadline=deadline)
        if (expected_writer_generation is not None
                and writer.fencing_generation != expected_writer_generation):
            # The writer this admission would actually run under is not the
            # one the caller's readiness selection was checked under: the
            # lease was reacquired or taken over between that check and this
            # resolution. A new fencing generation is a different selection,
            # not a renewal, and the writer dispatch read when it began proves
            # nothing about the one resolved here. Refused before the
            # admission transaction, so no monetary, workload or STARTED row
            # is attempted and no provider call can follow.
            from .primary_workload import WorkloadAdmissionHold
            raise WorkloadAdmissionHold("readiness_writer_changed_before_admission")
        return self.parallel.reserve_provider_attempt(
            reservation=reservation,
            writer=writer,
            attempt_ordinal=attempt_ordinal,
            format_roster=format_roster,
            idempotency_key_digest=idempotency_key_digest,
            budget_evidence_digest=budget_evidence_digest,
            team_admission=team_admission,
            budget_admission=budget_admission,
            budget_limits=budget_limits,
            deadline=deadline,
            expected_authority_digest=expected_authority_digest,
            revalidate_selection=revalidate_selection,
        )

    def record_provider_attempt(
        self,
        *,
        attempt: ProviderAttemptReservation,
        status: ParallelAssignmentStatus,
        result_digest: str | None,
        reason_code: str,
        deadline: float | None = None,
    ) -> None:
        """Record one attempt's outcome under the caller's completion cutoff.

        Resolving the writer can itself read, renew or reacquire a lease, so it
        is inside the same bound as the write it is for.
        """

        self.parallel.record_provider_attempt(
            attempt=attempt,
            writer=self._task_writer(attempt.task_id, deadline=deadline),
            status=status,
            result_digest=result_digest,
            reason_code=reason_code,
            deadline=deadline,
        )

    def record_parallel_result(
        self,
        *,
        plan_digest: str,
        reservation: ParallelReservation,
        status: ParallelAssignmentStatus,
        result_digest: str | None,
        reason_code: str,
    ) -> ParallelAggregate:
        writer = self._task_writer(reservation.task_id)
        self.parallel.record_result(
            reservation=reservation,
            writer=writer,
            status=status,
            result_digest=result_digest,
            reason_code=reason_code,
        )
        aggregate = self.parallel.aggregate(reservation.task_id)
        self._persist_outcome(
            self._runtime.record_parallel_aggregate(
                reservation.task_id,
                plan_digest=plan_digest,
                aggregate_digest=aggregate.aggregate_digest,
                terminal=aggregate.terminal,
                succeeded=aggregate.succeeded,
                ambiguous_slot_count=aggregate.ambiguous_slot_count,
            ),
            writer,
        )
        return aggregate

    def record_parallel_callback(
        self,
        *,
        task_id: str,
        assignment_id: str,
        provider_id: str,
        attempt_digest: str,
        provider_run_id_digest: str,
        status: ParallelAssignmentStatus,
        result_digest: str | None,
        reason_code: str,
    ) -> ParallelAggregate:
        """Settle only the callback's exact assignment/attempt ledger slot."""

        writer = self._task_writer(task_id)
        self.parallel.record_callback_result(
            task_id=task_id,
            assignment_id=assignment_id,
            provider_id=provider_id,
            attempt_digest=attempt_digest,
            provider_run_id_digest=provider_run_id_digest,
            writer=writer,
            status=status,
            result_digest=result_digest,
            reason_code=reason_code,
        )
        state = self._runtime.compact_state(task_id)
        plan_digest = state.get("parallel_plan_digest")
        if not isinstance(plan_digest, str):
            raise PrimaryCoordinatorError("callback has no graph-bound parallel plan")
        aggregate = self.parallel.aggregate(task_id)
        self._persist_outcome(
            self._runtime.record_parallel_aggregate(
                task_id,
                plan_digest=plan_digest,
                aggregate_digest=aggregate.aggregate_digest,
                terminal=aggregate.terminal,
                succeeded=aggregate.succeeded,
                ambiguous_slot_count=aggregate.ambiguous_slot_count,
            ),
            writer,
        )
        return aggregate

    def execute_parallel_ready(
        self,
        *,
        task_id: str,
        plan_digest: str,
        policy: ParallelDispatchPolicy,
        dispatcher: Callable[[ParallelReservation], ParallelDispatchOutcome],
    ) -> ParallelAggregate:
        """Run one dependency-ready wave concurrently, then checkpoint once.

        The dispatcher's published bound is read once, here, before this
        wave's *first* writer acquisition, and the same absolute value is then
        used by that acquisition, by readiness and by everything downstream.
        Nothing later derives a second one from a clock that has already moved
        through those waits.
        """

        published = getattr(dispatcher, "readiness_deadline", None)
        deadline = published() if callable(published) else None
        writer = self._task_writer(task_id, deadline=deadline)
        self.parallel.execute_ready(
            task_id=task_id,
            writer=writer,
            policy=policy,
            dispatcher=dispatcher,
            deadline=deadline,
        )
        # Execution is over here, in either direction. Aggregating what the
        # wave did, checkpointing it and publishing the projection are
        # recording work, so they draw on this operation's single completion
        # allowance - computed once from the same absolute deadline - instead
        # of running unbounded or minting a fresh window.
        completion = completion_deadline(deadline)
        aggregate = self.parallel.aggregate(task_id, deadline=completion)
        self._persist_outcome(
            self._runtime.record_parallel_aggregate(
                task_id,
                plan_digest=plan_digest,
                aggregate_digest=aggregate.aggregate_digest,
                terminal=aggregate.terminal,
                succeeded=aggregate.succeeded,
                ambiguous_slot_count=aggregate.ambiguous_slot_count,
                setup_hold_reasons=aggregate.setup_hold_reasons,
                deadline=completion,
            ),
            writer,
            deadline=completion,
        )
        return aggregate

    def engage_parallel_kill_switch(self, task_id: str) -> None:
        self.parallel.engage_kill_switch(
            task_id=task_id, writer=self._task_writer(task_id)
        )

    def resume_parallel_aggregate(
        self,
        *,
        task_id: str,
        aggregate: ParallelAggregate,
        candidate_sha: str,
    ) -> PrimaryTaskProjection:
        """Return terminal ledger evidence to the sole graph lifecycle."""

        writer = self._task_writer(task_id)
        observed = self.parallel.aggregate(task_id)
        if observed != aggregate:
            raise PrimaryCoordinatorError("parallel aggregate changed before graph resume")
        if not aggregate.terminal or aggregate.ambiguous_slot_count:
            raise PrimaryCoordinatorError("parallel aggregate is not resumable")
        if aggregate.workload_hold_reasons:
            return self._persist_outcome(
                self._runtime.record_parallel_workload_hold(
                    task_id, aggregate_digest=aggregate.aggregate_digest,
                    reason_code=aggregate.workload_hold_reasons[0],
                ), writer,
            )
        return self._persist_outcome(
            self._runtime.resume_parallel_aggregate(
                task_id,
                aggregate_digest=aggregate.aggregate_digest,
                succeeded=aggregate.succeeded,
                candidate_sha=candidate_sha,
            ),
            writer,
        )

    def _authorized_cancellation_writer(self, task_id: str, generation: int) -> TaskWriterLease:
        # Never reacquire after an authenticated cancellation has selected its
        # exact fence; a successor or an expired proof execution fails closed.
        writer = self._store.renew_task_writer(
            task_id, owner_token=self._owner_token, fencing_generation=generation)
        self._mark_active_task(task_id)
        return writer

    def authorize_cancellation(self, **authorization: Any) -> TaskWriterLease:
        writer = self._store.authorize_primary_cancellation(
            **authorization, owner_token=self._owner_token)
        self._mark_active_task(writer.task_id)
        return writer

    def cancel(self, task_id: str, *, expected_fencing_generation: int | None = None) -> PrimaryTaskProjection:
        writer = (self._task_writer(task_id) if expected_fencing_generation is None else
                  self._authorized_cancellation_writer(task_id, expected_fencing_generation))
        # New reservations must be fenced before the graph records cancellation.
        # Existing ACTIVE rows remain occupied and are reconciled rather than
        # being freed for an ambiguous duplicate dispatch.
        self.parallel.engage_kill_switch(task_id=task_id, writer=writer)
        stranded = self.parallel.reconcile_stranded_active(
            task_id=task_id, writer=writer
        )
        if stranded:
            aggregate = self.parallel.aggregate(task_id)
            state = self._runtime.compact_state(task_id)
            plan_digest = state.get("parallel_plan_digest")
            if isinstance(plan_digest, str):
                self._runtime.record_parallel_aggregate(
                    task_id,
                    plan_digest=plan_digest,
                    aggregate_digest=aggregate.aggregate_digest,
                    terminal=aggregate.terminal,
                    succeeded=aggregate.succeeded,
                    ambiguous_slot_count=aggregate.ambiguous_slot_count,
                )
        projection = self._persist_outcome(self._runtime.cancel(task_id), writer)
        self.parallel.reconcile_cancelled_pending(task_id=task_id, writer=writer)
        return projection

    def reconcile_cancelled_pending(self, task_id: str) -> int:
        """Repair historical cancelled rows without accepting a new command."""
        projection = self.get_projection(task_id)
        if (projection is None or projection.projection.get("run_status") != "TERMINAL"
                or projection.projection.get("terminal_classification") != "CANCELLED"
                or projection.projection.get("parallel_plan_digest") is None):
            return 0
        if not self.parallel.has_pending_assignments(task_id):
            return 0  # Do not keep renewing completed tasks on every worker pass.
        writer = self._task_writer(task_id)
        try:
            return self.parallel.reconcile_cancelled_pending(task_id=task_id, writer=writer)
        finally:
            self._active_task_ids.discard(task_id)

    def recover(self, task_id: str) -> PrimaryTaskProjection:
        writer = self._task_writer(task_id)
        stranded = self.parallel.reconcile_stranded_active(
            task_id=task_id, writer=writer
        )
        if stranded:
            aggregate = self.parallel.aggregate(task_id)
            state = self._runtime.compact_state(task_id)
            plan_digest = state.get("parallel_plan_digest")
            if not isinstance(plan_digest, str):
                raise PrimaryCoordinatorError(
                    "stranded parallel reservations lack a graph plan binding"
                )
            self._runtime.record_parallel_aggregate(
                task_id,
                plan_digest=plan_digest,
                aggregate_digest=aggregate.aggregate_digest,
                terminal=aggregate.terminal,
                succeeded=aggregate.succeeded,
                ambiguous_slot_count=aggregate.ambiguous_slot_count,
            )
        return self._persist_outcome(self._runtime.recover(task_id), writer)

    def get_projection(self, task_id: str) -> PrimaryTaskProjection | None:
        return self._store.read_primary_task_projection(task_id)

    def read_qualification_evidence(self, task_id: str) -> dict | None:
        """Forward one bounded READ ONLY qualification readback; no graph, lease or write."""

        return self._store.read_primary_qualification_evidence(task_id)

    def read_command_receipt(
        self, *, task_id: str, operation: str, command_id: str
    ) -> PrimaryCommandReceipt | None:
        """Resolve exact command redelivery without consulting current projection."""

        return self._store.read_primary_command_receipt(
            task_id=task_id, operation=operation, command_id=command_id
        )

    def record_command_receipt(
        self,
        *,
        task_id: str,
        operation: str,
        command_id: str,
        command_digest: str,
        projection: PrimaryTaskProjection,
        expected_fencing_generation: int | None = None,
    ) -> PrimaryCommandReceipt:
        """Append one sanitized command identity under the current task fence."""

        if not hmac.compare_digest(task_id, projection.task_id):
            raise PrimaryCoordinatorError(
                "command receipt projection belongs to another task"
            )
        if expected_fencing_generation is not None and operation != "cancellation":
            raise PrimaryCoordinatorError("an authorized cancellation fence is cancellation-only")
        writer = (self._task_writer(task_id) if expected_fencing_generation is None else
                  self._authorized_cancellation_writer(task_id, expected_fencing_generation))
        return self._store.record_primary_command_receipt(
            task_id=task_id,
            operation=operation,
            command_id=command_id,
            command_digest=command_digest,
            resulting_projection_digest=projection.projection_digest,
            resulting_projection_version=projection.projection_version,
            owner_token=self._owner_token,
            fencing_generation=writer.fencing_generation,
        )

    def claim_command_authorization(self, **authorization: Any) -> bool:
        """Durably claim a verified short-lived ingress proof."""

        return self._store.claim_primary_command_authorization(**authorization)

    def pending_effect_key(self, task_id: str) -> str:
        """Return the exact graph effect key awaiting owner approval."""

        state = self._runtime.compact_state(task_id)
        effect_key = state.get("effect_key_digest")
        if not isinstance(effect_key, str):
            raise PrimaryCoordinatorError(
                "external effect key is not durably bound in the graph checkpoint"
            )
        return effect_key

    def record_completion_evidence(
        self,
        *,
        task_id: str,
        candidate_sha: str,
        provider_result_digest: str,
        evidence: CompletionEvidence,
    ) -> None:
        """Persist authenticated completion evidence under the live writer fence."""

        writer = self._task_writer(task_id)
        self._store.record_completion_evidence(
            task_id=task_id,
            candidate_sha=candidate_sha,
            provider_result_digest=provider_result_digest,
            evidence=evidence,
            owner_token=self._owner_token,
            fencing_generation=writer.fencing_generation,
        )

    def get_task_writer(self, task_id: str) -> TaskWriterLease | None:
        """Expose the observed writer fence for command binding, never its token."""

        return self._store.current_task_writer(task_id)

    def recover_all(self, *, limit: int = 1000) -> tuple[PrimaryTaskProjection, ...]:
        """Reconcile one bounded startup/worker batch from durable task identity."""

        recovered: list[PrimaryTaskProjection] = []
        for task_id in self._store.list_primary_task_ids(limit=limit):
            recovered.append(self.recover(task_id))
        return tuple(recovered)

    def list_projections(self, *, limit: int = 1000) -> tuple[PrimaryTaskProjection, ...]:
        """Return one bounded recovery batch from the authoritative projection."""

        projections: list[PrimaryTaskProjection] = []
        for task_id in self._store.list_primary_task_ids(limit=limit):
            projection = self._store.read_primary_task_projection(task_id)
            if projection is None:
                raise PrimaryCoordinatorError(
                    "task identity disappeared during recovery enumeration"
                )
            projections.append(projection)
        return tuple(projections)

    def readiness(self) -> PrimaryCoordinatorReadiness:
        try:
            observed = self._store.current_intake_authority()
        except PostgresControlPlaneError:
            return PrimaryCoordinatorReadiness(
                primary_graph_constructed=True,
                primary_router_wired=True,
                checkpoint_backend_postgres=True,
                checkpoint_schema_current=False,
                claim_store_ready=False,
                effect_ledger_ready=False,
                effect_executor_ready=False,
                writer_lease_ready=False,
                reconciliation_worker_ready=False,
                primary_runtime_active=False,
                new_intake_owner="unknown",
                legacy_new_intake_disabled=False,
                intake_fencing_generation=0,
                graph_task_count=0,
                ambiguous_effect_count=0,
            )
        metrics = self._store.primary_control_plane_metrics()
        owned = bool(
            self._intake_generation is not None
            and observed.lease_live
            and observed.intake_owner == "langgraph"
            and observed.legacy_new_intake_disabled
            and observed.fencing_generation == self._intake_generation
            and hmac.compare_digest(observed.owner_token, self._owner_token)
        )
        return PrimaryCoordinatorReadiness(
            primary_graph_constructed=True,
            primary_router_wired=True,
            checkpoint_backend_postgres=True,
            checkpoint_schema_current=True,
            claim_store_ready=True,
            effect_ledger_ready=True,
            effect_executor_ready=bool(
                self._effect_executor is not None and self._effect_executor.ready
            ),
            writer_lease_ready=owned,
            reconciliation_worker_ready=False,
            primary_runtime_active=False,
            new_intake_owner=observed.intake_owner,
            legacy_new_intake_disabled=observed.legacy_new_intake_disabled,
            intake_fencing_generation=observed.fencing_generation,
            graph_task_count=metrics.graph_task_count,
            ambiguous_effect_count=metrics.ambiguous_effect_count,
        )


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:33 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 06:45 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 07:30 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-04 | 23:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 16:46 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-10 | 18:17 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:04 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:30 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 17:11 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | END]]

# [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]]
# [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-21 | 20:23 UTC | END]]

# [[Codex | GPT-6 Astra | 2026-09-21 | 20:46 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-21 | 10:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 11:19 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 12:57 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 18:52 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:29 UTC | END]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:02 UTC | END]]
