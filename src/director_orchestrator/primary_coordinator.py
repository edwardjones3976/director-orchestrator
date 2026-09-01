# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:50 UTC | BEGIN]]
"""Postgres-owned application boundary for the primary LangGraph runtime."""

from __future__ import annotations

import hmac
from dataclasses import asdict, dataclass
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
    PostgresParallelDispatch,
)


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
            if outcome.pending_approval is None
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
        self._application_projection_provider = application_projection_provider
        self.parallel = PostgresParallelDispatch(binding.claim_store)
        self._runtime = PrimaryWorkflowRuntime(
            binding.checkpointer,
            handlers,
            checkpoint_namespace=checkpoint_namespace,
            claim_store=binding.claim_store,
            owner_token=owner_token,
            auto_execute_provider=auto_execute_provider,
            auto_execute_effect=auto_execute_effect,
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

    def _task_writer(self, task_id: str) -> TaskWriterLease:
        current = self._store.current_task_writer(task_id)
        if current is None:
            return self._store.acquire_task_writer(
                task_id,
                engine_owner="langgraph",
                owner_token=self._owner_token,
                expected_generation=INITIAL_FENCING_GENERATION,
            )
        if current.engine_owner != "langgraph":
            raise PrimaryCoordinatorError("task engine_owner is not LangGraph")
        if current.lease_live:
            if not hmac.compare_digest(current.owner_token, self._owner_token):
                raise PrimaryCoordinatorError(
                    "another process owns the task writer lease"
                )
            return self._store.renew_task_writer(
                task_id,
                owner_token=self._owner_token,
                fencing_generation=current.fencing_generation,
            )
        # An expired row is durable fencing history, not renewable authority.
        # Reacquire it through the store's exact-generation CAS so any
        # concurrent successor makes this observation stale and fails closed.
        return self._store.acquire_task_writer(
            task_id,
            engine_owner="langgraph",
            owner_token=self._owner_token,
            expected_generation=current.fencing_generation,
        )

    def _persist_outcome(
        self, outcome: PrimaryOutcome, writer: TaskWriterLease
    ) -> PrimaryTaskProjection:
        payload = _outcome_projection(outcome)
        current = self._store.read_primary_task_projection(outcome.task_id)
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
        return self._store.update_primary_task_projection(
            task_id=outcome.task_id,
            owner_token=self._owner_token,
            fencing_generation=writer.fencing_generation,
            projection=payload,
        )

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

    def execute_parallel_ready(
        self,
        *,
        task_id: str,
        plan_digest: str,
        policy: ParallelDispatchPolicy,
        dispatcher: Callable[[ParallelReservation], ParallelDispatchOutcome],
    ) -> ParallelAggregate:
        """Run one dependency-ready wave concurrently, then checkpoint once."""

        writer = self._task_writer(task_id)
        self.parallel.execute_ready(
            task_id=task_id,
            writer=writer,
            policy=policy,
            dispatcher=dispatcher,
        )
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

    def engage_parallel_kill_switch(self, task_id: str) -> None:
        self.parallel.engage_kill_switch(
            task_id=task_id, writer=self._task_writer(task_id)
        )

    def cancel(self, task_id: str) -> PrimaryTaskProjection:
        writer = self._task_writer(task_id)
        return self._persist_outcome(self._runtime.cancel(task_id), writer)

    def recover(self, task_id: str) -> PrimaryTaskProjection:
        writer = self._task_writer(task_id)
        return self._persist_outcome(self._runtime.recover(task_id), writer)

    def get_projection(self, task_id: str) -> PrimaryTaskProjection | None:
        return self._store.read_primary_task_projection(task_id)

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
    ) -> PrimaryCommandReceipt:
        """Append one sanitized command identity under the current task fence."""

        if not hmac.compare_digest(task_id, projection.task_id):
            raise PrimaryCoordinatorError(
                "command receipt projection belongs to another task"
            )
        writer = self._task_writer(task_id)
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
