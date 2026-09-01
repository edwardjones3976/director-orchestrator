# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | BEGIN]]
"""Default Director application boundary for the paid-off primary rollout."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import Settings
from .langgraph_postgres import EffectReservation, PrimaryTaskProjection
from .langgraph_postgres import PostgresCheckpointHandle, open_postgres_control_plane
from .langgraph_runtime import (
    PacketBoundaryAdapter,
    RuntimeExecutionOutcome,
    RuntimeExecutionStatus,
)
from .langgraph_primary import (
    AcceptanceDecision,
    AcceptanceResult,
    ApprovalDecision,
    ApprovalResume,
    CompletionGateResult,
    EffectDisposition,
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
from .models import IntakeAuthentication, ProviderName, SlackIntake, TaskRecord, TaskStatus
from .packet_contracts import document_digest, validate_packet
from .packet_routing import DirectorPacketTaskRouter
from .packet_transports import LocalGitAuthoritySnapshotResolver
from .policy import preview_plan
from .primary_coordinator import PrimaryTaskCoordinator
from .primary_completion import (
    CompletionEvidence,
    CompletionEvidenceResolver,
    ExternalEffectResolver,
    PacketResultBinding,
    PacketResultResolver,
    ExternalEffectEvidence,
)
from .primary_provider_activation import PrimaryProviderActivationService
from .host_provider_bridge import (
    HostProviderBridgeError,
    challenge_host_provider_bridge,
)
from .primary_providers import CandidateShaResolver, PrimaryProviderBinding
from .primary_transports import (
    Microsoft365CopilotCapabilityEvidence,
    build_graph_native_packet_adapters,
    discover_local_primary_command_transports,
    provider_lane_external_blockers,
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _primary_provider_lane(provider: ProviderName) -> str:
    """Map only the typed Gemini assignment backend to its Antigravity lane."""

    return "antigravity" if provider == ProviderName.GEMINI else provider.value


def _bounded_secret_file(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError("delegated token cache reference is not a bounded file")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("delegated token cache contains an invalid bearer")
    return value


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


def build_automatic_primary_live_dependencies(
    settings: Settings,
    handle: PostgresCheckpointHandle,
    services: "DefaultDryRunServices",
) -> DefaultPrimaryLiveDependencies:
    """Discover installed exact lanes and compose graph-owned durable resolvers.

    Authentication comes from configured secret presence or a documented,
    non-secret local status/receipt probe. Native keychain state without such a
    contract is never inferred. Microsoft 365 Copilot remains unavailable behind
    an exact typed blocker until production Work IQ evidence is complete.
    """

    bridge_secret = settings.langgraph_primary_host_bridge_secret
    if bridge_secret and bridge_secret in {
        settings.shared_secret,
        settings.slack_gateway_secret,
        settings.langgraph_primary_adapter_callback_secret,
        settings.langgraph_primary_effect_reconciliation_secret,
    }:
        raise ValueError("host provider bridge secret must be distinct")

    authenticated = {
        "openai": bool(settings.openai_api_key),
        "claude": bool(settings.claude_routine_token),
        "cursor": bool(settings.cursor_api_key),
        "grok": bool(settings.grok_api_key),
        "antigravity": bool(settings.gemini_api_key),
        "perplexity": bool(settings.perplexity_api_key),
        "firecrawl": bool(os.environ.get("FIRECRAWL_API_KEY", "").strip()),
        "github-copilot": bool(
            os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()
            or os.environ.get("GH_TOKEN", "").strip()
            or os.environ.get("GITHUB_TOKEN", "").strip()
        ),
        "microsoft-365-copilot": False,
    }
    bridge_challenge = None
    if settings.langgraph_primary_host_bridge_endpoint and bridge_secret:
        try:
            bridge_challenge = challenge_host_provider_bridge(
                settings.langgraph_primary_host_bridge_endpoint,
                bridge_secret,
            )
        except HostProviderBridgeError:
            # Configuration is not liveness or command-map evidence. Discovery
            # remains fail-closed and reports the affected lanes unavailable.
            bridge_challenge = None
    transports, capabilities = discover_local_primary_command_transports(
        cwd=settings.repository_snapshot_root,
        authenticated=authenticated,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
        openai_reasoning_effort=settings.openai_reasoning_effort,
        perplexity_max_results=settings.perplexity_max_results,
        host_bridge_endpoint=settings.langgraph_primary_host_bridge_endpoint,
        host_bridge_secret=bridge_secret,
        host_bridge_writer_owner_token_digest=(
            "sha256:" + hashlib.sha256(
                settings.langgraph_primary_owner_token.encode("utf-8")
            ).hexdigest()
            if bridge_secret
            else ""
        ),
        host_bridge_challenge=bridge_challenge,
        microsoft_365_copilot_access_token=_bounded_secret_file(
            settings.microsoft_365_copilot_delegated_token_file
        ),
        microsoft_365_copilot_evidence=(
            Microsoft365CopilotCapabilityEvidence(
                conversation_id=settings.microsoft_365_copilot_conversation_id,
                endpoint="https://workiq.svc.cloud.microsoft/rest",
                tenant_id=settings.microsoft_365_copilot_tenant_id,
                issuer=settings.microsoft_365_copilot_issuer,
                delegated_scope="api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask",
                admin_consent_observed=settings.microsoft_365_copilot_admin_consent_observed,
                tenant_enabled_observed=settings.microsoft_365_copilot_tenant_enabled_observed,
                billing_entitlement_observed=(
                    settings.microsoft_365_copilot_billing_entitlement_observed
                ),
                challenge_id=settings.microsoft_365_copilot_challenge_id,
                expires_at=settings.microsoft_365_copilot_capability_expires_at,
                receipt_digest=settings.microsoft_365_copilot_capability_receipt_digest,
            )
            if settings.microsoft_365_copilot_capability_receipt_digest
            else None
        ),
    )
    adapters = build_graph_native_packet_adapters(
        transports=transports,
        capabilities=capabilities,
        required_providers=settings.langgraph_primary_required_providers,
    )
    missing = sorted(set(settings.langgraph_primary_required_providers) - set(adapters))
    if missing:
        blockers = provider_lane_external_blockers(capabilities)
        blocked_missing = [
            f"{provider_id}:{blockers[provider_id]}"
            for provider_id in missing
            if provider_id in blockers
        ]
        raise ValueError(
            "automatic graph-native live dependencies are unavailable: missing="
            + ",".join(missing)
            + (
                "; external_blockers=" + ",".join(blocked_missing)
                if blocked_missing
                else ""
            )
        )

    def candidate_sha(
        _work_packet: Mapping[str, Any], result_packet: Mapping[str, Any]
    ) -> str:
        candidates: set[str] = set()
        for artifact in result_packet.get("artifacts", []):
            if not isinstance(artifact, Mapping) or artifact.get("kind") != "git_commit":
                continue
            location = str(artifact.get("location", ""))
            value = location.rsplit("@", 1)[-1]
            if re.fullmatch(r"[0-9a-f]{40}", value):
                candidates.add(value)
        if len(candidates) != 1:
            raise RuntimeError("authenticated result does not bind one exact candidate SHA")
        return next(iter(candidates))

    def packet_result(context: HandlerContext) -> PacketResultBinding | None:
        record = services._record(context.task_id)
        if record.plan is None or context.scheduled_producer_id is None:
            return None
        assignment = next(
            (
                item for item in record.plan.assignments
                if _primary_provider_lane(item.agent) == context.scheduled_producer_id
            ),
            None,
        )
        if assignment is None:
            return None
        assignment_id = services._packet_router._assignment_id(record, assignment)
        work = handle.claim_store.read_work_packet_artifact(
            task_id=context.task_id, assignment_id=assignment_id
        )
        result = handle.claim_store.read_result_packet_artifact(
            task_id=context.task_id, assignment_id=assignment_id
        )
        if work is None or result is None:
            return None
        packet = dict(result.packet)
        status = (
            RuntimeExecutionStatus.COMPLETED
            if packet.get("status") == "SUCCEEDED"
            else RuntimeExecutionStatus.OWNER_DECISION_REQUIRED
            if packet.get("status") == "OWNER_DECISION_REQUIRED"
            else RuntimeExecutionStatus.REJECTED
        )
        return PacketResultBinding(
            record=record,
            assignment=assignment,
            work_packet=dict(work.packet),
            outcome=RuntimeExecutionOutcome(
                status=status,
                reason="authenticated_primary_result_artifact",
                result_packet=packet,
                result_packet_digest=result.document_digest,
            ),
        )

    def authenticated_result(context: HandlerContext) -> Mapping[str, Any] | None:
        binding = packet_result(context)
        if binding is None or binding.outcome.result_packet is None:
            return None
        if binding.outcome.result_packet_digest != context.provider_result_digest:
            return None
        return binding.outcome.result_packet

    def evidence_from_result(context: HandlerContext) -> EvidenceDebateResult:
        packet = authenticated_result(context)
        digest = _digest(
            {
                "task": context.task_id,
                "candidate_sha": context.candidate_sha,
                "provider_result_digest": context.provider_result_digest,
                "phase": "authenticated_evidence_debate",
            }
        )
        passed = bool(
            packet is not None
            and packet.get("status") == "SUCCEEDED"
            and not packet.get("blockers")
            and not packet.get("requested_decisions")
        )
        return EvidenceDebateResult(
            digest,
            _digest({"evidence_digest": digest, "provider": context.producer_id}),
            context.candidate_sha,
            passed=passed,
            reason_code=(
                "authenticated_provider_evidence_recorded"
                if passed
                else "owner_decision_required"
            ),
        )

    def acceptance_from_result(context: HandlerContext) -> AcceptanceResult:
        packet = authenticated_result(context)
        acceptance = packet.get("acceptance", []) if packet is not None else []
        accepted = bool(
            packet is not None
            and packet.get("status") == "SUCCEEDED"
            and acceptance
            and all(
                isinstance(item, Mapping) and item.get("status") == "PASS"
                for item in acceptance
            )
            and not packet.get("risks")
            and not packet.get("blockers")
            and not packet.get("requested_decisions")
        )
        return AcceptanceResult(
            _digest(
                {
                    "task": context.task_id,
                    "candidate_sha": context.candidate_sha,
                    "provider_result_digest": context.provider_result_digest,
                    "accepted": accepted,
                }
            ),
            context.candidate_sha,
            AcceptanceDecision.ACCEPT if accepted else AcceptanceDecision.REJECT,
            reason_code=(
                "authenticated_result_acceptance_passed"
                if accepted
                else "owner_decision_required"
            ),
        )

    def external_effect(context: HandlerContext) -> ExternalEffectEvidence | None:
        if context.effect_attempt_digest is None or context.effect_key_digest is None:
            return None
        reservation = handle.claim_store.read_effect_reservation(
            task_id=context.task_id,
            effect_attempt_digest=context.effect_attempt_digest,
            owner_token=settings.langgraph_primary_owner_token,
        )
        if reservation is None:
            return None
        binding = handle.claim_store.read_remote_binding(
            reservation_key=reservation.reservation_key,
            owner_token=settings.langgraph_primary_owner_token,
        )
        if binding is None:
            return None
        packet = authenticated_result(context)
        if packet is None:
            return None
        matching = [
            effect
            for effect in packet.get("side_effects", [])
            if isinstance(effect, Mapping)
            and effect.get("status") == "COMPLETED"
            and effect.get("external_id") == binding["remote_identifier"]
        ]
        if len(matching) != 1:
            return None
        return ExternalEffectEvidence(
            effect_key_digest=context.effect_key_digest,
            effect_attempt_digest=context.effect_attempt_digest,
            writer_owner_id=context.writer_owner_id,
            disposition=EffectDisposition.COMPLETED,
            result=dict(matching[0]),
            readback=dict(binding),
            reason_code="authenticated_effect_readback_completed",
        )

    def completion_evidence(context: HandlerContext) -> CompletionEvidence | None:
        artifact = handle.claim_store.read_completion_evidence(task_id=context.task_id)
        if artifact is None:
            return None
        if (
            artifact.candidate_sha != context.candidate_sha
            or artifact.provider_result_digest != context.provider_result_digest
        ):
            return None
        return artifact.evidence

    return DefaultPrimaryLiveDependencies(
        adapters=adapters,
        candidate_sha_resolver=candidate_sha,
        packet_result_resolver=packet_result,
        completion_evidence_resolver=completion_evidence,
        external_effect_resolver=external_effect,
        evidence_debate_handler=evidence_from_result,
        acceptance_handler=acceptance_from_result,
    )


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
    pinned_commit = settings.langgraph_primary_pinned_commit_sha.strip().lower()
    pinned_tree = settings.langgraph_primary_pinned_tree_sha.strip().lower()
    if bool(pinned_commit) != bool(pinned_tree) or any(
        re.fullmatch(r"[0-9a-f]{40}", item) is None
        for item in (pinned_commit, pinned_tree)
        if item
    ):
        raise ValueError(
            "Default primary immutable commit and tree pins must be supplied "
            "together as 40-character hexadecimal values"
        )
    if pinned_commit:
        head_sha = git("rev-parse", "--verify", "HEAD^{commit}")
        if head_sha != pinned_commit or starting_sha != pinned_commit:
            raise RuntimeError(
                "Default primary HEAD and starting ref must match the pinned commit"
            )
    else:
        remote_sha = git(
            "rev-parse",
            "--verify",
            f"refs/remotes/origin/{starting_ref.removeprefix('refs/heads/')}^{{commit}}",
        )
        if starting_sha != remote_sha:
            raise RuntimeError("Default primary starting ref does not match origin")
    tree_sha = git("rev-parse", "--verify", f"{starting_sha}^{{tree}}")
    if pinned_tree and tree_sha != pinned_tree:
        raise RuntimeError("Default primary computed tree does not match its pin")
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
                    pinned_commit_sha=settings.langgraph_primary_pinned_commit_sha,
                    pinned_tree_sha=settings.langgraph_primary_pinned_tree_sha,
                    max_files=settings.langgraph_primary_snapshot_max_files,
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
        # Intake pre-validates and freezes this deterministic plan so graph budget
        # units and the dispatched packet are derived from the same immutable input.
        if record.plan is None:
            record.plan = preview_plan(record.intake.text, self.settings)
        record.plan_generation += 1
        if not record.plan.assignments:
            raise RuntimeError("Default primary plan has no packet assignment")
        assignment = min(record.plan.assignments, key=lambda item: item.sequence)
        plan_digest = _digest(record.plan.model_dump(mode="json"))
        work_packet = validate_packet(
            "work", self._packet_router.planned_work_packet(record, assignment)
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
            plan_digest=plan_digest,
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
        provider_lane = (
            _primary_provider_lane(assignment.agent)
            if self._live_composition
            else assignment.agent.value
        )
        return ScheduleResult(
            _digest(
                {
                    "task_id": context.task_id,
                    "sequence": assignment.sequence,
                    "provider": provider_lane,
                }
            ),
            provider_lane,
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

    def _request(self, record: TaskRecord) -> PrimaryRequest:
        task_id = record.task_id
        intake = record.intake
        if record.plan is None or not record.plan.assignments:
            raise RuntimeError("primary intake requires a validated non-empty plan")
        provider_units = sum(
            max(1, assignment.effort_points) for assignment in record.plan.assignments
        )
        effect_units = sum(
            1 for assignment in record.plan.assignments if assignment.allow_draft_pr
        )
        task_units = provider_units + effect_units
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
            budget_limit_units=task_units,
            provider_cost_units=provider_units,
            effect_cost_units=effect_units,
            execution_approval_required=True,
            external_effect_required=effect_units > 0,
            effect_key_digest=(
                _digest(
                    {
                        "task_id": task_id,
                        "plan": record.plan.model_dump(mode="json"),
                        "effect": "authorized_assignment_effect",
                    }
                )
                if effect_units > 0
                else None
            ),
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
        authenticated_replay_identity: tuple[str, str] | None = None,
    ) -> TaskRecord:
        task_id = "DIR-LG-" + secrets.token_hex(5).upper()
        record = TaskRecord(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            intake=intake,
            intake_authentication=intake_authentication,
        )
        record.plan = preview_plan(record.intake.text, self.settings)
        request = self._request(record)
        if authenticated_replay_identity is not None:
            claimed_task_id, created = self.coordinator.claim_authenticated_intake(
                nonce_digest="sha256:" + authenticated_replay_identity[0],
                idempotency_key_digest=request.idempotency_key_digest,
                payload_digest="sha256:" + authenticated_replay_identity[1],
                task_id=task_id,
            )
            task_id = claimed_task_id
            if not created:
                existing = self.coordinator.get_projection(task_id)
                if existing is not None:
                    return self._record_from_projection(existing)
            record.task_id = task_id
            request = self._request(record)
        self.services.register(record)
        try:
            projection = self.coordinator.create_intake(request)
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
            and not self.settings.langgraph_primary_live_provider_dispatch_enabled
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

    def prepare_external_effect(self, task_id: str) -> EffectReservation:
        projection = self.coordinator.get_projection(task_id)
        if projection is None:
            raise KeyError(task_id)
        self.services.hydrate(projection)
        try:
            return self.coordinator.prepare_external_effect(task_id)
        finally:
            self.services.discard(task_id)

    def bind_external_effect_remote(
        self,
        *,
        reservation: EffectReservation,
        remote_system: str,
        remote_identifier: str,
    ) -> None:
        self.coordinator.bind_external_effect_remote(
            reservation=reservation,
            remote_system=remote_system,
            remote_identifier=remote_identifier,
        )

    def read_external_effect_remote(
        self, reservation: EffectReservation
    ) -> Mapping[str, str] | None:
        return self.coordinator.read_external_effect_remote(reservation)

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
                if provider_activation is None:
                    raise ValueError(
                        "live LangGraph primary startup requires exact provider "
                        "activation bindings"
                    )
                services = DefaultDryRunServices(settings, live_composition=True)
                if live_dependencies is None:
                    live_dependencies = build_automatic_primary_live_dependencies(
                        settings, handle, services
                    )
                live_dependencies.validate(settings.langgraph_primary_required_providers)
                if not provider_activation.health()["combined_live_ready"]:
                    raise ValueError(
                        "live LangGraph primary startup requires current LIVE_OK "
                        "evidence for every required provider app and runtime adapter"
                    )
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
                    if producer is None or producer != _primary_provider_lane(
                        assignment.agent
                    ):
                        raise RuntimeError("scheduled provider binding changed")
                    return PrimaryProviderBinding(
                        provider=producer,
                        producer_id=producer,
                        provider_attempt_digest=context.provider_attempt_digest,
                        writer_token_digest=(
                            services._packet_router.writer_token(record).digest
                        ),
                        fencing_generation=artifact.writer_fencing_generation,
                        work_packet=artifact.packet,
                    )

                def revalidate_writer_fence(
                    context: HandlerContext, binding: PrimaryProviderBinding
                ) -> int:
                    writer = handle.claim_store.current_task_writer(context.task_id)
                    if (
                        writer is None
                        or writer.engine_owner != "langgraph"
                        or writer.owner_token != settings.langgraph_primary_owner_token
                        or writer.fencing_generation != binding.fencing_generation
                    ):
                        raise RuntimeError("live provider I/O has a stale writer fence")
                    return writer.fencing_generation

                def write_result_packet(
                    context: HandlerContext,
                    binding: PrimaryProviderBinding,
                    packet: Mapping[str, Any],
                    packet_digest: str,
                ) -> str:
                    artifact = handle.claim_store.record_result_packet_artifact(
                        task_id=context.task_id,
                        assignment_id=str(packet["assignment_id"]),
                        packet=packet,
                        owner_token=settings.langgraph_primary_owner_token,
                        fencing_generation=binding.fencing_generation,
                    )
                    if artifact.document_digest != packet_digest:
                        raise RuntimeError(
                            "durable result-packet digest does not match provider result"
                        )
                    return artifact.document_digest

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
                    writer_fence_resolver=revalidate_writer_fence,
                    result_artifact_writer=write_result_packet,
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




# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:50 UTC | BEGIN]]
# Live approvals may cross the authenticated graph interrupt; plan-derived budget
# units, current writer fencing, and Postgres result artifacts remain mandatory.
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:50 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:55 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:24 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:17 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 10:04 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 09:44 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
