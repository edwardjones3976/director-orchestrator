# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
"""Default Director application boundary for the paid-off primary rollout."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Settings
from .langgraph_postgres import PrimaryTaskProjection
from .langgraph_postgres import PostgresCheckpointHandle, open_postgres_control_plane
from .langgraph_runtime import PacketBoundaryAdapter
from .langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    ApprovalDecision,
    ApprovalResume,
    CompletionGateResult,
    EffectExecutionResult,
    EvidenceDebateResult,
    ExactShaReviewResult,
    HandlerContext,
    PhaseResult,
    PlanResult,
    PrimaryHandlers,
    PrimaryRequest,
    ProviderDisposition,
    ProviderSubgraphResult,
    ScheduleResult,
)
from .models import IntakeAuthentication, SlackIntake, TaskRecord, TaskStatus
from .packet_contracts import document_digest, validate_packet
from .packet_routing import DirectorPacketTaskRouter
from .packet_transports import LocalGitAuthoritySnapshotResolver
from .policy import preview_plan
from .primary_coordinator import PrimaryTaskCoordinator
from .primary_completion import (
    CompletionEvidenceResolver,
    ExternalEffectResolver,
    PacketResultResolver,
)
from .primary_provider_activation import PrimaryProviderActivationService
from .primary_providers import CandidateShaResolver, PrimaryProviderBinding


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class DefaultPrimaryAuthority:
    """One already-verified repository authority used for a new intake."""

    starting_sha: str
    authority_snapshot_digest: str


@dataclass(frozen=True, slots=True)
class DefaultPrimaryLiveDependencies:
    """Explicit external dependencies required by the live primary graph."""

    adapters: Mapping[str, PacketBoundaryAdapter]
    candidate_sha_resolver: CandidateShaResolver
    packet_result_resolver: PacketResultResolver
    completion_evidence_resolver: CompletionEvidenceResolver
    external_effect_resolver: ExternalEffectResolver
    evidence_debate_handler: Callable[[HandlerContext], EvidenceDebateResult]
    acceptance_handler: Callable[[HandlerContext], AcceptanceResult]

    def validate(self, required_providers: tuple[str, ...]) -> None:
        expected = set(required_providers)
        actual = set(self.adapters)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("unexpected=" + ",".join(extra))
            raise ValueError(
                "live primary provider adapters must exactly cover required "
                "providers (" + "; ".join(details) + ")"
            )
        for provider, adapter in self.adapters.items():
            if not isinstance(adapter, PacketBoundaryAdapter):
                raise ValueError(
                    f"live primary adapter for {provider} is not a packet boundary"
                )
            if adapter.provider != provider:
                raise ValueError(
                    f"live primary adapter identity does not match {provider}"
                )
        required_callables = {
            "candidate SHA resolver": self.candidate_sha_resolver,
            "durable packet-result resolver": self.packet_result_resolver,
            "durable completion-evidence resolver": self.completion_evidence_resolver,
            "durable external-effect resolver": self.external_effect_resolver,
            "evidence/debate handler": self.evidence_debate_handler,
            "OpenAI acceptance handler": self.acceptance_handler,
        }
        for name, dependency in required_callables.items():
            if not callable(dependency):
                raise ValueError(f"live primary {name} is unavailable")


def resolve_default_primary_authority(settings: Settings) -> DefaultPrimaryAuthority:
    """Bind the configured clean repository to its exact origin-backed head."""

    if len(settings.repository_allowlist) != 1:
        raise ValueError("Default primary activation requires exactly one repository")
    if len(settings.allowed_starting_refs) != 1:
        raise ValueError("Default primary activation requires exactly one starting ref")
    root = Path(settings.repository_snapshot_root).resolve(strict=True)

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-c", f"safe.directory={root}", *arguments),
            cwd=root,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError("Default primary repository authority could not be verified")
        return completed.stdout.strip()

    if git("status", "--porcelain=v2", "--untracked-files=all"):
        raise RuntimeError("Default primary repository authority must be clean")
    configured_repository = settings.repository_allowlist[0].removesuffix(".git").lower()
    remote = git("remote", "get-url", "origin").removesuffix(".git")
    if remote.startswith("git@github.com:"):
        remote = remote.removeprefix("git@github.com:")
    elif "github.com/" in remote:
        remote = remote.split("github.com/", 1)[1]
    if remote.strip("/").lower() != configured_repository:
        raise RuntimeError("Default primary repository remote does not match its allowlist")
    starting_ref = settings.allowed_starting_refs[0]
    starting_sha = git("rev-parse", "--verify", f"{starting_ref}^{{commit}}")
    remote_sha = git(
        "rev-parse",
        "--verify",
        f"refs/remotes/origin/{starting_ref.removeprefix('refs/heads/')}^{{commit}}",
    )
    if starting_sha != remote_sha:
        raise RuntimeError("Default primary starting ref does not match origin")
    tree_sha = git("rev-parse", "--verify", f"{starting_sha}^{{tree}}")
    return DefaultPrimaryAuthority(
        starting_sha=starting_sha,
        authority_snapshot_digest=_digest(
            {
                "repository": configured_repository,
                "starting_ref": starting_ref,
                "starting_sha": starting_sha,
                "tree_sha": tree_sha,
            }
        ),
    )


class DefaultDryRunServices:
    """Bounded real planning services for the paid-off Default cutover.

    The first Default activation deliberately stops at the graph's authenticated
    execution approval interrupt. Provider and external-effect handlers are
    fail-closed and cannot make calls. Raw intake and the owner-visible preview
    live only in the Postgres task projection; graph checkpoints remain digest
    addressed.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        packet_router: DirectorPacketTaskRouter | None = None,
        live_composition: bool = False,
    ) -> None:
        if settings.allow_paid_runs and not live_composition:
            raise ValueError("Default dry-run primary services require paid runs off")
        self.settings = settings
        self._live_composition = live_composition
        if packet_router is None:
            if len(settings.repository_allowlist) != 1:
                raise ValueError("Default packet planning requires one repository")
            packet_router = DirectorPacketTaskRouter(
                authority_snapshots=LocalGitAuthoritySnapshotResolver(
                    settings.repository_allowlist[0],
                    settings.repository_snapshot_root,
                ),
                boundary_adapters={},
                writer_owner_id="langgraph-primary-default",
                writer_secret=(
                    settings.langgraph_primary_owner_token
                    or "default-plan-packet-binding-only"
                ),
                timeout_seconds=settings.provider_timeout_seconds,
            )
        self._packet_router = packet_router
        self._records: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()

    def register(self, record: TaskRecord) -> None:
        with self._lock:
            self._records[record.task_id] = record.model_copy(deep=True)

    def discard(self, task_id: str) -> None:
        with self._lock:
            self._records.pop(task_id, None)

    def hydrate(self, projection: PrimaryTaskProjection) -> None:
        """Rehydrate bounded service input from the graph-owned task projection."""

        application = projection.projection.get("application")
        if not isinstance(application, Mapping):
            raise RuntimeError("primary recovery projection lacks application state")
        raw = application.get("task_record")
        if not isinstance(raw, Mapping):
            raise RuntimeError("primary recovery projection lacks its task record")
        self.register(TaskRecord.model_validate(dict(raw)))

    def _record(self, task_id: str) -> TaskRecord:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise RuntimeError("Default primary task application state is unavailable")
            return record

    def application_projection(self, task_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            return {
                "schema_version": "director-default-task-view/v1",
                "task_record": record.model_dump(mode="json"),
            }

    def authenticate_scope(self, context: HandlerContext) -> PhaseResult:
        record = self._record(context.task_id)
        return PhaseResult(
            _digest(
                {
                    "task_id": record.task_id,
                    "intake_authentication": record.intake_authentication.value,
                    "channel_id": record.intake.channel_id,
                    "user_id": record.intake.user_id,
                    "scope_digest": context.scope_digest,
                }
            )
        )

    def canonicalize(self, context: HandlerContext) -> PhaseResult:
        record = self._record(context.task_id)
        return PhaseResult(_digest(record.intake.model_dump(mode="json")))

    def plan(self, context: HandlerContext) -> PlanResult:
        record = self._record(context.task_id)
        record.plan = preview_plan(record.intake.text, self.settings)
        record.plan_generation += 1
        if not record.plan.assignments:
            raise RuntimeError("Default primary plan has no packet assignment")
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        work_packet = validate_packet(
            "work", self._packet_router.work_packet(record, assignment)
        )
        record.status = TaskStatus.OWNER_DECISION_REQUIRED
        record.message = (
            "LangGraph created a live-authorized plan and stopped at its "
            "execution approval interrupt."
            if self._live_composition
            else "LangGraph created a paid-off preview and stopped at its execution "
            "approval interrupt; no provider or external effect was called."
        )
        return PlanResult(
            plan_digest=_digest(record.plan.model_dump(mode="json")),
            work_packet_digest=document_digest(work_packet),
        )

    def policy_budget(self, context: HandlerContext) -> PhaseResult:
        record = self._record(context.task_id)
        if record.plan is None:
            return PhaseResult(
                _digest({"task_id": context.task_id, "reason": "plan_missing"}),
                passed=False,
                reason_code="plan_missing",
            )
        return PhaseResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "paid_execution": self._live_composition,
                    "estimated_costs": [
                        assignment.estimated_cost_usd
                        for assignment in record.plan.assignments
                    ],
                    "max_task_budget_usd": self.settings.max_task_budget_usd,
                }
            )
        )

    def schedule(self, context: HandlerContext) -> ScheduleResult:
        record = self._record(context.task_id)
        if record.plan is None or not record.plan.assignments:
            return ScheduleResult(
                _digest({"task_id": context.task_id, "reason": "schedule_empty"}),
                "owner",
                passed=False,
                reason_code="schedule_empty",
            )
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        return ScheduleResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "sequence": assignment.sequence,
                    "provider": assignment.agent.value,
                }
            ),
            assignment.agent.value,
        )

    @staticmethod
    def _external_boundary_disabled(_: HandlerContext) -> Any:
        raise RuntimeError(
            "paid-off Default primary activation cannot cross an external boundary"
        )

    def handlers(self) -> PrimaryHandlers:
        return PrimaryHandlers(
            authenticate_scope=self.authenticate_scope,
            canonicalize=self.canonicalize,
            plan=self.plan,
            policy_budget=self.policy_budget,
            schedule=self.schedule,
            provider_subgraph=self._external_boundary_disabled,
            evidence_debate=self._external_boundary_disabled,
            openai_acceptance_revision=self._external_boundary_disabled,
            exact_sha_review=self._external_boundary_disabled,
            ci_security_completion=self._external_boundary_disabled,
            effect_execution_readback=self._external_boundary_disabled,
        )


class DefaultOfflineSimulationServices(DefaultDryRunServices):
    """Full graph services backed only by deterministic, paid-off doubles."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider_doubles: Mapping[str, Callable[[HandlerContext], str]] | None = None,
        **kwargs: Any,
    ) -> None:
        if not settings.langgraph_primary_offline_simulation_enabled:
            raise ValueError("offline simulation services require their explicit switch")
        super().__init__(settings, **kwargs)
        if provider_doubles is None:
            provider_doubles = {
                provider: (
                    lambda context, selected=provider: _digest(
                        {
                            "task_id": context.task_id,
                            "provider": selected,
                            "mode": "offline-provider-double",
                        }
                    )
                )
                for provider in settings.langgraph_primary_required_providers
            }
        if set(provider_doubles) != set(settings.langgraph_primary_required_providers):
            raise ValueError("offline doubles must exactly cover required providers")
        if any(not callable(handler) for handler in provider_doubles.values()):
            raise ValueError("every offline provider double must be callable")
        self._provider_doubles = dict(provider_doubles)
        self._provider_calls = {provider: 0 for provider in provider_doubles}
        self._provider_call_lock = threading.Lock()

    @property
    def provider_call_counts(self) -> Mapping[str, int]:
        with self._provider_call_lock:
            return dict(self._provider_calls)

    @property
    def provider_calls(self) -> int:
        return sum(self.provider_call_counts.values())

    def provider_subgraph(self, context: HandlerContext) -> ProviderSubgraphResult:
        if context.provider_attempt_digest is None or context.scheduled_producer_id is None:
            raise RuntimeError("offline provider attempt is not durably bound")
        if context.work_packet_digest is None:
            raise RuntimeError("offline provider packet is not post-plan bound")
        def invoke(provider: str) -> tuple[str, str]:
            with self._provider_call_lock:
                self._provider_calls[provider] += 1
            result_digest = self._provider_doubles[provider](context)
            if not isinstance(result_digest, str) or not result_digest.startswith(
                "sha256:"
            ):
                raise RuntimeError("offline provider double returned an invalid digest")
            return provider, result_digest

        with ThreadPoolExecutor(
            max_workers=len(self._provider_doubles),
            thread_name_prefix="director-offline-provider",
        ) as executor:
            results = dict(executor.map(invoke, sorted(self._provider_doubles)))
        return ProviderSubgraphResult(
            provider_attempt_digest=context.provider_attempt_digest,
            idempotency_key_digest=context.idempotency_key_digest,
            authority_snapshot_digest=context.authority_snapshot_digest,
            producer_id=context.scheduled_producer_id,
            disposition=ProviderDisposition.COMPLETED,
            result_digest=_digest(
                {
                    "task_id": context.task_id,
                    "provider_attempt_digest": context.provider_attempt_digest,
                    "work_packet_digest": context.work_packet_digest,
                    "provider_results": results,
                    "mode": "offline-full-team-simulation",
                }
            ),
            candidate_sha=context.starting_sha,
            reason_code="offline_provider_double_completed",
        )

    @staticmethod
    def evidence_debate(context: HandlerContext) -> EvidenceDebateResult:
        return EvidenceDebateResult(
            _digest({"task_id": context.task_id, "kind": "offline-evidence"}),
            _digest({"task_id": context.task_id, "kind": "offline-debate"}),
            context.candidate_sha,
            reason_code="offline_evidence_completed",
        )

    @staticmethod
    def openai_acceptance_revision(context: HandlerContext) -> AcceptanceResult:
        return AcceptanceResult(
            _digest({"task_id": context.task_id, "kind": "offline-acceptance"}),
            context.candidate_sha,
            AcceptanceDecision.ACCEPT,
            reason_code="offline_acceptance_completed",
        )

    @staticmethod
    def exact_sha_review(context: HandlerContext) -> ExactShaReviewResult:
        if context.producer_id is None:
            raise RuntimeError("offline exact-SHA review lacks a producer")
        reviewer = "claude" if context.producer_id == "cursor" else "cursor"
        return ExactShaReviewResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "candidate_sha": context.candidate_sha,
                    "producer": context.producer_id,
                    "reviewer": reviewer,
                }
            ),
            context.candidate_sha,
            context.producer_id,
            reviewer,
            reason_code="offline_exact_sha_review_completed",
        )

    @staticmethod
    def ci_security_completion(context: HandlerContext) -> CompletionGateResult:
        return CompletionGateResult(
            _digest({"task_id": context.task_id, "gate": "offline-ci"}),
            _digest({"task_id": context.task_id, "gate": "offline-security"}),
            _digest({"task_id": context.task_id, "gate": "offline-completion"}),
            context.candidate_sha,
            reason_code="offline_completion_gates_passed",
        )

    def handlers(self) -> PrimaryHandlers:
        handlers = super().handlers()
        return PrimaryHandlers(
            authenticate_scope=handlers.authenticate_scope,
            canonicalize=handlers.canonicalize,
            plan=handlers.plan,
            policy_budget=handlers.policy_budget,
            schedule=handlers.schedule,
            provider_subgraph=self.provider_subgraph,
            evidence_debate=self.evidence_debate,
            openai_acceptance_revision=self.openai_acceptance_revision,
            exact_sha_review=self.exact_sha_review,
            ci_security_completion=self.ci_security_completion,
            effect_execution_readback=handlers.effect_execution_readback,
        )


class DefaultPrimaryApplication:
    """Route Default Director intake and reads through one primary coordinator."""

    def __init__(
        self,
        settings: Settings,
        coordinator: PrimaryTaskCoordinator,
        services: DefaultDryRunServices,
        authority: DefaultPrimaryAuthority,
    ) -> None:
        self.settings = settings
        self.coordinator = coordinator
        self.services = services
        self.authority = authority

    def _request(self, task_id: str, intake: SlackIntake) -> PrimaryRequest:
        intake_payload = intake.model_dump(mode="json")
        intake_digest = _digest(intake_payload)
        scope_digest = _digest(
            {
                "app_env": self.settings.app_env,
                "projects": self.settings.project_allowlist,
                "repositories": self.settings.repository_allowlist,
                "starting_refs": self.settings.allowed_starting_refs,
            }
        )
        idempotency_key_digest = _digest(
            {
                "source_event_id": intake.source_event_id,
                "channel_id": intake.channel_id,
                "thread_ts": intake.thread_ts,
            }
        )
        dedup_payload_digest = _digest(
            {
                "source_event_id": intake.source_event_id,
                "channel_id": intake.channel_id,
                "thread_ts": intake.thread_ts,
                "user_id": intake.user_id,
                "text": intake.text,
                "permalink": intake.permalink,
            }
        )
        return PrimaryRequest(
            task_id=task_id,
            source_event_id="source-" + idempotency_key_digest.removeprefix("sha256:")[:24],
            correlation_id="correlation-" + secrets.token_hex(12),
            intake_digest=intake_digest,
            scope_digest=scope_digest,
            authority_snapshot_digest=self.authority.authority_snapshot_digest,
            # Compatibility input only. The graph quarantines it until plan()
            # returns the digest of a validated model-work-packet-v1 document.
            work_packet_digest=_digest(
                {
                    "intake_digest": intake_digest,
                    "scope_digest": scope_digest,
                    "authority_snapshot_digest": (
                        self.authority.authority_snapshot_digest
                    ),
                    "starting_sha": self.authority.starting_sha,
                }
            ),
            idempotency_key_digest=idempotency_key_digest,
            starting_sha=self.authority.starting_sha,
            dedup_payload_digest=dedup_payload_digest,
            writer_owner_id="langgraph",
            budget_limit_units=0,
            provider_cost_units=0,
            effect_cost_units=0,
            execution_approval_required=True,
            external_effect_required=False,
            graph_writer_active=True,
            legacy_writer_active=False,
        )

    @staticmethod
    def _record_from_projection(projection: PrimaryTaskProjection) -> TaskRecord:
        application = projection.projection.get("application")
        if not isinstance(application, Mapping):
            raise RuntimeError("primary task projection lacks application state")
        raw = application.get("task_record")
        if not isinstance(raw, Mapping):
            raise RuntimeError("primary task projection lacks its task record")
        record = TaskRecord.model_validate(dict(raw))
        classification = projection.projection.get("terminal_classification")
        if classification == "SUCCEEDED":
            record.status = TaskStatus.DONE
        elif classification == "OWNER_DECISION_REQUIRED":
            record.status = TaskStatus.OWNER_DECISION_REQUIRED
        elif classification in {"RECONCILIATION_REQUIRED", "POLICY_BLOCKED"}:
            record.status = TaskStatus.BLOCKED
        elif classification is not None:
            record.status = TaskStatus.FAILED
        elif projection.projection.get("run_status") == "RUNNING":
            record.status = TaskStatus.RUNNING
        return record

    def create_intake(
        self,
        intake: SlackIntake,
        *,
        intake_authentication: IntakeAuthentication,
    ) -> TaskRecord:
        task_id = "DIR-LG-" + secrets.token_hex(5).upper()
        record = TaskRecord(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=intake_authentication,
        )
        self.services.register(record)
        try:
            projection = self.coordinator.create_intake(
                self._request(task_id, intake)
            )
            return self._record_from_projection(projection)
        finally:
            self.services.discard(task_id)

    def get_task(self, task_id: str) -> TaskRecord | None:
        projection = self.coordinator.get_projection(task_id)
        return (
            None
            if projection is None
            else self._record_from_projection(projection)
        )

    def resume_approval(self, command: ApprovalResume) -> TaskRecord:
        projection = self.coordinator.get_projection(command.task_id)
        if projection is None:
            raise KeyError(command.task_id)
        if (
            command.decision == ApprovalDecision.APPROVE
            and not self.settings.langgraph_primary_offline_simulation_enabled
        ):
            raise RuntimeError(
                "paid-off Default primary activation cannot approve provider execution"
            )
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.resume_approval(command)
        finally:
            self.services.discard(command.task_id)
        return self._record_from_projection(updated)

    def cancel(self, task_id: str) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.cancel(task_id)
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)

    def record_provider_result(
        self, task_id: str, command: ProviderSubgraphResult
    ) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.record_provider_result(task_id, command)
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)

    def reconcile_effect(
        self, task_id: str, command: EffectExecutionResult
    ) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.reconcile_effect(task_id, command)
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)

    def recover(self, task_id: str) -> TaskRecord:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            updated = self.coordinator.recover(task_id)
        finally:
            self.services.discard(task_id)
        return self._record_from_projection(updated)


class DefaultPrimaryActivation:
    """Own the Default profile's Postgres handle, lease, and application router."""

    def __init__(
        self,
        settings: Settings,
        *,
        live_dependencies: DefaultPrimaryLiveDependencies | None = None,
    ) -> None:
        self.settings = settings
        self.handle: PostgresCheckpointHandle | None = None
        self.coordinator: PrimaryTaskCoordinator | None = None
        self.application: DefaultPrimaryApplication | None = None
        self.services: DefaultDryRunServices | None = None
        self.provider_activation: PrimaryProviderActivationService | None = None
        self._reconciliation_worker_ready = False
        self._live_dependencies_ready = False
        if not settings.langgraph_primary_enabled:
            return
        live_requested = settings.langgraph_primary_live_provider_dispatch_enabled
        if settings.allow_paid_runs and not live_requested:
            raise ValueError(
                "ALLOW_PAID_RUNS is enabled but "
                "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED is disabled"
            )
        if live_requested and live_dependencies is None:
            raise ValueError(
                "live LangGraph primary startup requires explicit provider adapters, "
                "durable result/completion/effect resolvers, exact-SHA readback, "
                "evidence/debate, and OpenAI acceptance dependencies"
            )
        if live_dependencies is not None:
            live_dependencies.validate(settings.langgraph_primary_required_providers)
        authority = resolve_default_primary_authority(settings)
        handle = open_postgres_control_plane(
            settings.langgraph_primary_postgres_dsn,
            schema=settings.langgraph_primary_postgres_schema,
            allow_insecure_local_transport=(
                settings.langgraph_primary_allow_insecure_local_postgres
            ),
        )
        try:
            bindings = settings.primary_provider_activation_bindings()
            provider_activation = (
                PrimaryProviderActivationService(
                    store=handle.claim_store,
                    bindings=bindings,
                    required_providers=settings.langgraph_primary_required_providers,
                )
                if bindings
                else None
            )
            if live_requested:
                assert live_dependencies is not None
                if provider_activation is None:
                    raise ValueError(
                        "live LangGraph primary startup requires exact provider "
                        "activation bindings"
                    )
                if not provider_activation.health()["combined_live_ready"]:
                    raise ValueError(
                        "live LangGraph primary startup requires current LIVE_OK "
                        "evidence for every required provider app and runtime adapter"
                    )
                services = DefaultDryRunServices(settings, live_composition=True)

                def write_work_packet(
                    context: HandlerContext,
                    packet: Mapping[str, Any],
                    packet_digest: str,
                ) -> str:
                    writer = handle.claim_store.current_task_writer(context.task_id)
                    if (
                        writer is None
                        or writer.engine_owner != "langgraph"
                        or writer.owner_token != settings.langgraph_primary_owner_token
                    ):
                        raise RuntimeError(
                            "live work-packet persistence lacks current writer authority"
                        )
                    artifact = handle.claim_store.record_work_packet_artifact(
                        task_id=context.task_id,
                        assignment_id=str(packet["assignment_id"]),
                        packet=packet,
                        owner_token=writer.owner_token,
                        fencing_generation=writer.fencing_generation,
                    )
                    if artifact.document_digest != packet_digest:
                        raise RuntimeError(
                            "durable work-packet digest does not match planned packet"
                        )
                    return artifact.document_digest

                def resolve_work_packet(
                    context: HandlerContext,
                ) -> PrimaryProviderBinding:
                    record = services._record(context.task_id)
                    if record.plan is None or not record.plan.assignments:
                        raise RuntimeError("live provider binding has no assignment")
                    assignment = min(
                        record.plan.assignments, key=lambda item: item.sequence
                    )
                    assignment_id = services._packet_router._assignment_id(
                        record, assignment
                    )
                    artifact = handle.claim_store.read_work_packet_artifact(
                        task_id=context.task_id, assignment_id=assignment_id
                    )
                    if artifact is None:
                        raise RuntimeError("durable work-packet artifact is unavailable")
                    if context.provider_attempt_digest is None:
                        raise RuntimeError("provider attempt is not bound")
                    producer = context.scheduled_producer_id
                    if producer is None or producer != assignment.agent.value:
                        raise RuntimeError("scheduled provider binding changed")
                    return PrimaryProviderBinding(
                        provider=producer,
                        producer_id=producer,
                        provider_attempt_digest=context.provider_attempt_digest,
                        writer_token_digest=(
                            services._packet_router.writer_token(record).digest
                        ),
                        work_packet=artifact.packet,
                    )

                from .primary_live import DefaultLiveServices

                live_services = DefaultLiveServices(
                    planning_services=services,
                    adapters=live_dependencies.adapters,
                    work_packet_artifact_writer=write_work_packet,
                    work_packet_binding_resolver=resolve_work_packet,
                    candidate_sha_resolver=live_dependencies.candidate_sha_resolver,
                    live_provider_activation=lambda provider: (
                        provider_activation.provider_ready(provider, live=True)
                    ),
                    packet_result_resolver=live_dependencies.packet_result_resolver,
                    completion_evidence_resolver=(
                        live_dependencies.completion_evidence_resolver
                    ),
                    external_effect_resolver=(
                        live_dependencies.external_effect_resolver
                    ),
                    evidence_debate_handler=live_dependencies.evidence_debate_handler,
                    acceptance_handler=live_dependencies.acceptance_handler,
                )
                handlers = live_services.handlers()
                self._live_dependencies_ready = True
            else:
                services = (
                    DefaultOfflineSimulationServices(settings)
                    if settings.langgraph_primary_offline_simulation_enabled
                    else DefaultDryRunServices(settings)
                )
                handlers = services.handlers()
            coordinator = PrimaryTaskCoordinator(
                handle,
                handlers,
                owner_token=settings.langgraph_primary_owner_token,
                checkpoint_namespace=(
                    settings.langgraph_primary_checkpoint_namespace
                ),
                auto_execute_provider=(
                    settings.langgraph_primary_offline_simulation_enabled
                    or live_requested
                ),
                application_projection_provider=(
                    services.application_projection
                ),
            )
        except Exception:
            handle.close()
            raise
        self.handle = handle
        self.coordinator = coordinator
        self.services = services
        self.provider_activation = provider_activation
        self.application = DefaultPrimaryApplication(
            settings, coordinator, services, authority
        )

    @property
    def enabled(self) -> bool:
        return self.application is not None

    def start(self) -> None:
        if not self.enabled:
            return
        if not self.settings.langgraph_primary_new_intake_enabled:
            return
        assert self.coordinator is not None
        self.coordinator.adopt_or_activate_new_intake()

    def renew(self) -> None:
        if not self.enabled or not self.settings.langgraph_primary_new_intake_enabled:
            return
        assert self.coordinator is not None
        self.coordinator.renew_new_intake()

    def reconcile_once(self) -> int:
        if not self.enabled:
            return 0
        assert self.coordinator is not None
        assert self.services is not None
        recovered = []
        for projection in self.coordinator.list_projections():
            self.services.hydrate(projection)
            try:
                recovered.append(self.coordinator.recover(projection.task_id))
            finally:
                self.services.discard(projection.task_id)
        self._reconciliation_worker_ready = True
        return len(recovered)

    def health(self) -> dict[str, Any]:
        configured = self.settings.langgraph_primary_configuration_readiness()
        provider_activation = (
            self.provider_activation.health()
            if self.provider_activation is not None
            else {
                "app_dry_run_ready": False,
                "app_live_ready": False,
                "runtime_adapter_dry_run_ready": False,
                "runtime_adapter_live_ready": False,
                "combined_dry_run_ready": False,
                "combined_live_ready": False,
            }
        )
        if self.coordinator is None:
            return {
                **configured,
                "provider_activation": provider_activation,
                "primary_graph_constructed": False,
                "primary_router_wired": False,
                "checkpoint_backend_postgres": False,
                "checkpoint_schema_current": False,
                "claim_store_ready": False,
                "effect_ledger_ready": False,
                "writer_lease_ready": False,
                "reconciliation_worker_ready": False,
                "primary_runtime_active": False,
                "new_intake_owner": "legacy",
                "legacy_new_intake_disabled": False,
                "intake_fencing_generation": 0,
                "graph_task_count": 0,
                "ambiguous_effect_count": 0,
            }
        observed = self.coordinator.readiness().as_dict()
        observed["reconciliation_worker_ready"] = (
            self._reconciliation_worker_ready
        )
        observed["paid_off_primary_intake_active"] = bool(
            observed["primary_router_wired"]
            and observed["checkpoint_backend_postgres"]
            and observed["claim_store_ready"]
            and observed["writer_lease_ready"]
            and observed["legacy_new_intake_disabled"]
            and self._reconciliation_worker_ready
            and not self.settings.allow_paid_runs
        )
        observed["primary_runtime_active"] = bool(
            self.settings.langgraph_primary_live_provider_dispatch_enabled
            and self._live_dependencies_ready
            and provider_activation["combined_live_ready"]
            and observed["primary_router_wired"]
            and observed["checkpoint_backend_postgres"]
            and observed["claim_store_ready"]
            and observed["effect_ledger_ready"]
            and observed["writer_lease_ready"]
            and observed["legacy_new_intake_disabled"]
            and self._reconciliation_worker_ready
        )
        return {
            **configured,
            **observed,
            "provider_activation": provider_activation,
        }

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None




# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
