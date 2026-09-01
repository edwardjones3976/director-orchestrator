# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:31 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:26 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:47 UTC | BEGIN]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 09:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
from __future__ import annotations

import asyncio
import hmac
import secrets
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse

from . import __version__
from .approvals import (
    StaleApproval,
    apply_ambiguous_dispatch_resolution,
    apply_failed_predispatch_retry,
    apply_oracle_recovery,
    apply_owner_approval,
    apply_owner_clarification,
    clarification_required,
    owner_identity_matches,
    reset_for_owner_clarification,
    reserve_task_effect,
    SLACK_INTAKE_ISSUED_AT_HEADER,
    SLACK_INTAKE_NONCE_HEADER,
    SLACK_INTAKE_SIGNATURE_HEADER,
    task_effect_key,
    verify_slack_intake_attestation,
    verify_slack_gateway_proof,
)
from .callbacks import (
    CallbackConflict,
    apply_claude_callback,
    apply_manual_run_completion,
    bearer_matches,
)
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
from .chatgpt_bridge import (
    BridgeRejected,
    ChatGPTBridgeDeliveryAck,
    ChatGPTBridgeDeliveryAckResponse,
    ChatGPTBridgeDeliveryAmbiguity,
    ChatGPTBridgeDeliveryAmbiguityResponse,
    ChatGPTBridgeRequest,
    ChatGPTBridgeResponse,
    ChatGPTBridgeService,
    ChatGPTBridgeStore,
)
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]
from .config import Settings
from .engine import OrchestrationEngine
from .models import (
    AmbiguousDispatchResolution,
    ClaudeCallback,
    IntakeAuthentication,
    ManualRunCompletion,
    OracleRecoveryApproval,
    OwnerApproval,
    OwnerApprovalSource,
    SlackIntake,
    TaskRecord,
    TaskStatus,
    UsageCheckRecord,
    UsageCheckRunStatus,
)
from .oracle_merge import (
    GitHubOracleGateway,
    OracleMergePolicy,
    OracleMergeService,
    OracleMergeStore,
)
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
from .runtime_activation import RuntimeActivation
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
from .store import PrivacyCleanupResume, SlackIntakeReplayConflict, TaskStore
from .usage import UsageMonitor
from .packet_results import DirectorPacketResultConsumer
from .packet_routing import DirectorPacketTaskRouter
from .packet_transports import (
    GitHubReadOnlyRepositoryEffectVerifier,
    GitHubRepositoryEffectVerifier,
    LocalGitAuthoritySnapshotResolver,
    build_primary_packet_adapters,
)
from .providers import provider_registry
from .parallel_dispatch import (
    DailyTaskBudgetLedger,
)
from .primary_commands import (
    ApprovalResumeRequest,
    CancellationRequest,
    EffectReconciliationRequest,
    PrimaryCommandBindingError,
    PrimaryCommandOperation,
    PrimaryCommandResponse,
    ProviderResultRequest,
    RecoveryRequest,
)
from .primary_activation_commands import ProviderActivationAdvanceRequest
from .workload_policy import EnforcementMode


def _build_github_effect_verifier(
    current_settings: Settings,
) -> GitHubRepositoryEffectVerifier | None:
    """Inject GET-only effect corroboration, or preserve fail-closed absence."""

    if not current_settings.oracle_github_credential_configured():
        return None
    timeout_seconds = min(15.0, float(current_settings.provider_timeout_seconds))
    return GitHubReadOnlyRepositoryEffectVerifier.from_settings(
        current_settings,
        request_timeout_seconds=timeout_seconds,
    )


settings = Settings()
settings.validate_workload_policy_configuration()
# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
settings.validate_langgraph_configuration()
settings.validate_langgraph_primary_configuration()
# [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
store = TaskStore(settings.sqlite_path)
primary_default_activation = None
if settings.langgraph_primary_enabled:
    # Lazy import preserves the default-off startup boundary. Enabling primary
    # mode must construct its real Postgres application router or fail startup.
    from .primary_default import DefaultPrimaryActivation

    primary_default_activation = DefaultPrimaryActivation(settings)
settings.validate_parallel_dispatch_configuration()
providers = provider_registry(settings)
packet_task_router = None
packet_result_consumer = None
if settings.langgraph_runtime_readiness()["live_provider_dispatch"]:
    if len(settings.repository_allowlist) != 1:
        raise RuntimeError(
            "live packet dispatch requires exactly one repository allowlist entry"
        )
    packet_task_router = DirectorPacketTaskRouter(
        authority_snapshots=LocalGitAuthoritySnapshotResolver(
            settings.repository_allowlist[0],
            settings.repository_snapshot_root,
            max_files=settings.langgraph_primary_snapshot_max_files,
        ),
        boundary_adapters=build_primary_packet_adapters(
            providers,
            task_store=store,
            poll_seconds=settings.provider_poll_seconds,
            github_effect_verifier=_build_github_effect_verifier(settings),
            github_effect_timeout_seconds=min(
                15.0, float(settings.provider_timeout_seconds)
            ),
        ),
        writer_owner_id=settings.langgraph_runtime_writer_owner_id,
        writer_secret=settings.langgraph_runtime_writer_secret,
        timeout_seconds=settings.provider_timeout_seconds,
    )
    packet_result_consumer = DirectorPacketResultConsumer()
# Startup fails closed on an enabled-but-unisolated coordinator or gate store.
# RuntimeActivation re-runs both validations and builds only what is enabled.
runtime_activation = RuntimeActivation(
    settings,
    packet_task_router=packet_task_router,
    packet_result_consumer=packet_result_consumer,
)
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
oracle_merge_policy = OracleMergePolicy(settings)
oracle_merge_store = OracleMergeStore(settings.sqlite_path)
oracle_github = (
    GitHubOracleGateway.from_settings(settings)
    if oracle_merge_policy.enabled
    and not oracle_merge_policy.kill_switch
    and settings.oracle_github_credential_configured()
    else None
)
oracle_merge_service = OracleMergeService(
    oracle_merge_policy, oracle_merge_store, oracle_github
)
engine = OrchestrationEngine(
    settings,
    store,
    oracle_merge_service,
    runtime_activation=runtime_activation,
    providers=providers,
)
usage_monitor = UsageMonitor(settings, store)
daily_task_budget = (
    DailyTaskBudgetLedger(settings.sqlite_path) if settings.allow_paid_runs else None
)
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
chatgpt_bridge = ChatGPTBridgeService(
    settings,
    ChatGPTBridgeStore(
        settings.sqlite_path,
        gateway_secret=settings.slack_gateway_secret,
        shared_secret=settings.shared_secret,
    ),
)
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]
_PRIVACY_CLEANUP_RETRY_SECONDS = 2.0
_RECOVERABLE_SCAN_SECONDS = 30.0
_CHATGPT_BRIDGE_EXPIRY_SCAN_SECONDS = 60.0


async def _resume_privacy_cleanup_waits_once() -> None:
    resumes = store.claim_privacy_cleanup_resumes()
    if resumes:
        await asyncio.gather(
            *(_resume_privacy_cleanup_wait(resume) for resume in resumes),
            return_exceptions=True,
        )


async def _resume_privacy_cleanup_wait(resume: PrivacyCleanupResume) -> None:
    record = store.get(resume.task_id)
    if record is None:
        store.acknowledge_privacy_cleanup_resume(resume)
        return
    try:
        if resume.effect_kind == "slack_intent":
            recovery_message = (
                f"*{record.task_id} — {record.status.value}*\n"
                "A delayed Director status delivery resumed after local privacy cleanup."
            )
            # This transport identity is derived from the original blocked
            # effect, so crash recovery always retries the same Slack post.
            client_msg_id = engine._slack_client_id(
                record.task_id, "privacy-recovery", resume.effect_key
            )
            recovery_effect_key = task_effect_key(
                record,
                phase="slack_delivery",
                identity={"client_msg_id": client_msg_id},
            )
            recovery_record = record.model_copy(deep=True)
            recovery_effect = reserve_task_effect(
                recovery_record,
                effect_key=recovery_effect_key,
                kind="slack_intent",
                phase="slack_delivery",
                replay_safe=True,
            )
            disposition = store.prepare_privacy_slack_recovery(
                resume, recovery_effect
            )
            if disposition in {
                "original",
                "completed",
                "pending",
                "stale_lease",
            }:
                return
            # Reload after the atomic reservation so _status_post sees the
            # authoritative recovery intent rather than trying to create it.
            record = store.get(resume.task_id)
            if record is None:
                return
            await engine._status_post(
                record,
                recovery_message,
                client_msg_id=client_msg_id,
            )
        else:
            recovery_effect_key = None
            await engine.run(record.task_id)
    except Exception:
        # The durable lease remains and becomes reclaimable. Raw provider or
        # Slack errors are intentionally not logged from this boundary.
        return
    if store.privacy_cleanup_pending():
        return
    current = store.get(resume.task_id)
    effect_recorded = bool(
        current
        and any(
            item.effect_key == resume.effect_key
            and item.kind == resume.effect_kind
            for item in current.task_effect_ledger
        )
    )
    slack_terminal = bool(
        current
        and recovery_effect_key
        and any(
            item.effect_key == recovery_effect_key
            and item.kind == "slack_observation"
            and item.outcome in {"delivered", "ambiguous"}
            for item in current.task_effect_ledger
        )
    )
    if current is None or effect_recorded or slack_terminal:
        store.acknowledge_privacy_cleanup_resume(resume)


async def _privacy_cleanup_recovery_loop() -> None:
    while True:
        await _resume_privacy_cleanup_waits_once()
        await asyncio.sleep(_PRIVACY_CLEANUP_RETRY_SECONDS)


async def _recoverable_tasks_once() -> None:
    records = store.list_recoverable()
    if records:
        await asyncio.gather(
            *(engine.run(record.task_id) for record in records),
            return_exceptions=True,
        )


async def _recoverable_task_loop() -> None:
    while True:
        await asyncio.sleep(_RECOVERABLE_SCAN_SECONDS)
        await _recoverable_tasks_once()


async def _chatgpt_bridge_expiry_loop() -> None:
    while True:
        await asyncio.sleep(_CHATGPT_BRIDGE_EXPIRY_SCAN_SECONDS)
        try:
            chatgpt_bridge.store.sweep_expired_answers()
        except Exception:
            # The next bounded scan retries local cleanup without exposing data.
            continue


async def _primary_intake_lease_loop() -> None:
    while True:
        await asyncio.sleep(10.0)
        if primary_default_activation is None:
            continue
        try:
            primary_default_activation.renew()
        except Exception:
            # Intake re-checks observed Postgres authority and fails closed. The
            # public health surface exposes the lost writer lease.
            continue


async def _primary_reconciliation_loop() -> None:
    while True:
        await asyncio.sleep(30.0)
        if primary_default_activation is None:
            continue
        try:
            primary_default_activation.reconcile_once()
        except Exception:
            continue


@asynccontextmanager
async def lifespan(_: FastAPI):
    if primary_default_activation is not None:
        primary_default_activation.start()
        primary_default_activation.reconcile_once()
    # Startup fails closed if stale encrypted recovery material cannot be swept.
    chatgpt_bridge.store.sweep_expired_answers()
    initial_resumes = store.claim_privacy_cleanup_resumes()
    resumed_ids = {resume.task_id for resume in initial_resumes}
    initial_task_ids = {
        record.task_id
        for record in store.list_recoverable()
        if record.task_id not in resumed_ids
    }
    recovery_tasks = [
        asyncio.create_task(engine.run(task_id)) for task_id in initial_task_ids
    ] + [
        asyncio.create_task(_resume_privacy_cleanup_wait(resume))
        for resume in initial_resumes
    ]
    monitor_task = asyncio.create_task(usage_monitor.run_forever())
    privacy_recovery_task = asyncio.create_task(_privacy_cleanup_recovery_loop())
    recoverable_task = asyncio.create_task(_recoverable_task_loop())
    chatgpt_bridge_expiry_task = asyncio.create_task(
        _chatgpt_bridge_expiry_loop()
    )
    primary_intake_lease_task = asyncio.create_task(
        _primary_intake_lease_loop()
    )
    primary_reconciliation_task = asyncio.create_task(
        _primary_reconciliation_loop()
    )
    tasks = [
        *recovery_tasks,
        monitor_task,
        privacy_recovery_task,
        recoverable_task,
        chatgpt_bridge_expiry_task,
        primary_intake_lease_task,
        primary_reconciliation_task,
    ]
    try:
        yield
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
        runtime_activation.close()
        if primary_default_activation is not None:
            primary_default_activation.close()
        # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]


app = FastAPI(title="Director Orchestrator", version=__version__, lifespan=lifespan)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.shared_secret}"
    if not settings.shared_secret or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_claude_callback_auth(
    authorization: str | None = Header(default=None),
) -> None:
    if not bearer_matches(settings.claude_callback_secret, authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health() -> dict:
    effective_workload_mode = store.effective_workload_mode(
        configured_mode=EnforcementMode(settings.workload_policy_mode),
        shadow_assignment_limit=settings.workload_policy_shadow_assignments,
    )
    return {
        "ok": True,
        "version": __version__,
        "environment": settings.app_env,
        "paid_runs_enabled": settings.allow_paid_runs,
        "providers": settings.readiness(),
        # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
        "chatgpt_bridge": settings.chatgpt_bridge_readiness(),
        # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]
        "execution_profile": settings.execution_profile_readiness(),
        "workload_policy": settings.workload_policy_readiness(
            effective_mode=effective_workload_mode.value
        ),
        # [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]
        "langgraph": settings.langgraph_shadow_readiness(),
        # Configuration is deliberately separate from observed authority. Until
        # the production coordinator is attached to these command routes, every
        # operational primary claim remains false even if an operator supplies
        # otherwise valid PostgreSQL settings.
        "langgraph_primary": (
            primary_default_activation.health()
            if primary_default_activation is not None
            else {
                **settings.langgraph_primary_configuration_readiness(),
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
                "provider_activation": {
                    "app_dry_run_ready": False,
                    "app_live_ready": False,
                    "runtime_adapter_dry_run_ready": False,
                    "runtime_adapter_live_ready": False,
                    "combined_dry_run_ready": False,
                    "combined_live_ready": False,
                },
            }
        ),
        # [[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]
        # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | BEGIN]]
        # Booleans and one transport identifier only: no path, namespace value,
        # secret, or allowlist entry is exposed here.
        **runtime_activation.health_readiness(),
        "parallel_dispatch": {
            **settings.parallel_dispatch_health_readiness(),
            "ledger_configured": engine.parallel_dispatch is not None,
            "daily_task_budget_configured": daily_task_budget is not None,
        },
        # [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
        "oracle_merge": settings.oracle_merge_readiness(),
        "usage_monitor": usage_monitor.health(),
        "privacy_cleanup_pending": store.privacy_cleanup_pending(),
    }


@app.get(
    "/v1/usage-checks/latest",
    response_model=list[UsageCheckRecord],
    dependencies=[Depends(require_auth)],
)
def latest_usage_checks() -> list[UsageCheckRecord]:
    return store.latest_usage_checks()


@app.post("/v1/usage-checks/run", dependencies=[Depends(require_auth)])
async def run_usage_checks(background: BackgroundTasks) -> JSONResponse:
    if not settings.usage_check_enabled:
        raise HTTPException(status_code=409, detail="Periodic usage checks are disabled")
    if not settings.allow_paid_runs:
        raise HTTPException(status_code=409, detail="Paid provider execution is disabled")
    cycle_id = f"USAGE-{secrets.token_hex(5).upper()}"
    plan = await usage_monitor.reserve_manual_cycle(cycle_id)
    payload = plan.model_dump(mode="json")
    if plan.status == UsageCheckRunStatus.ACCEPTED:
        background.add_task(usage_monitor.run_once, True, cycle_id)
        return JSONResponse(status_code=202, content=payload)
    if plan.status == UsageCheckRunStatus.NOT_DUE:
        # Explicit cooldown response: never advertise an accepted cycle with zero records.
        return JSONResponse(status_code=200, content=payload)
    return JSONResponse(status_code=409, content=payload)


# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
@app.post(
    "/v1/chatgpt-bridge",
    response_model=ChatGPTBridgeResponse,
    dependencies=[Depends(require_auth)],
)
async def chatgpt_bridge_reply(payload: ChatGPTBridgeRequest) -> ChatGPTBridgeResponse:
    try:
        return await chatgpt_bridge.reply(payload)
    except BridgeRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="ChatGPT bridge request failed") from exc


@app.post(
    "/v1/chatgpt-bridge/delivery-ack",
    response_model=ChatGPTBridgeDeliveryAckResponse,
    dependencies=[Depends(require_auth)],
)
def chatgpt_bridge_delivery_ack(
    payload: ChatGPTBridgeDeliveryAck,
) -> ChatGPTBridgeDeliveryAckResponse:
    try:
        return chatgpt_bridge.acknowledge_delivery(payload)
    except BridgeRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="ChatGPT bridge delivery acknowledgement failed"
        ) from exc


@app.post(
    "/v1/chatgpt-bridge/delivery-ambiguous",
    response_model=ChatGPTBridgeDeliveryAmbiguityResponse,
    dependencies=[Depends(require_auth)],
)
def chatgpt_bridge_delivery_ambiguous(
    payload: ChatGPTBridgeDeliveryAmbiguity,
) -> ChatGPTBridgeDeliveryAmbiguityResponse:
    try:
        return chatgpt_bridge.mark_delivery_ambiguous(payload)
    except BridgeRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="ChatGPT bridge delivery ambiguity failed"
        ) from exc
# [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]


@app.post("/v1/intake", response_model=TaskRecord, status_code=202, dependencies=[Depends(require_auth)])
def intake(
    payload: SlackIntake,
    background: BackgroundTasks,
    intake_issued_at: str | None = Header(
        default=None, alias=SLACK_INTAKE_ISSUED_AT_HEADER
    ),
    intake_nonce: str | None = Header(default=None, alias=SLACK_INTAKE_NONCE_HEADER),
    intake_signature: str | None = Header(
        default=None, alias=SLACK_INTAKE_SIGNATURE_HEADER
    ),
) -> TaskRecord:
    if primary_default_activation is not None:
        if not settings.langgraph_primary_new_intake_enabled:
            raise HTTPException(
                status_code=503,
                detail="LangGraph primary intake is not activated",
            )
        assert primary_default_activation.application is not None
        try:
            return primary_default_activation.application.create_intake(
                payload,
                intake_authentication=IntakeAuthentication.BEARER_DRY_RUN,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="LangGraph primary intake is unavailable",
            ) from exc
    intake_authentication = IntakeAuthentication.BEARER_DRY_RUN
    replay_identity: tuple[str, str] | None = None
    if settings.allow_paid_runs:
        try:
            replay_identity = verify_slack_intake_attestation(
                settings.slack_gateway_secret,
                settings.shared_secret,
                payload,
                expected_owner_user_id=settings.slack_owner_user_id,
                expected_channel_id=settings.slack_channel_id,
                issued_at=intake_issued_at,
                nonce=intake_nonce,
                signature=intake_signature,
            )
        except StaleApproval as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        intake_authentication = IntakeAuthentication.SLACK_GATEWAY_ATTESTED
    task_id = f"DIR-{secrets.token_hex(5).upper()}"
    record = TaskRecord(
        task_id=task_id,
        status=TaskStatus.QUEUED,
        intake=payload,
        intake_authentication=intake_authentication,
    )
    try:
        stored, created = (
            store.create_signed_slack_intake(
                record,
                nonce_digest=replay_identity[0],
                payload_digest=replay_identity[1],
            )
            if replay_identity is not None
            else store.create(record)
        )
    except SlackIntakeReplayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created:
        background.add_task(engine.run, stored.task_id)
    return stored


@app.get("/v1/tasks/{task_id}", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def get_task(task_id: str, response: Response) -> TaskRecord:
    if primary_default_activation is not None:
        assert primary_default_activation.application is not None
        primary_record = primary_default_activation.application.get_task(task_id)
        if primary_record is not None:
            assert primary_default_activation.coordinator is not None
            projection = primary_default_activation.coordinator.get_projection(task_id)
            writer = primary_default_activation.coordinator.get_task_writer(task_id)
            if projection is None or writer is None:
                raise HTTPException(
                    status_code=503,
                    detail="LangGraph primary projection is unavailable",
                )
            response.headers["X-Task-Engine-Owner"] = "langgraph"
            response.headers["X-Primary-Projection-Digest"] = (
                projection.projection_digest
            )
            response.headers["X-Primary-Projection-Version"] = str(
                projection.projection_version
            )
            response.headers["X-Primary-Writer-Generation"] = str(
                writer.fencing_generation
            )
            return primary_record
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    record, version = versioned
    response.headers["X-Task-Record-Version"] = str(version)
    return record


def _primary_command_projection(
    task_id: str,
    request: object,
    operation: PrimaryCommandOperation,
):
    if primary_default_activation is None:
        raise HTTPException(status_code=404, detail="LangGraph primary task not found")
    coordinator = primary_default_activation.coordinator
    if coordinator is None:
        raise HTTPException(status_code=503, detail="LangGraph primary is unavailable")
    try:
        request.validate_path(task_id)
        receipt = coordinator.read_command_receipt(
            task_id=task_id,
            operation=operation.value,
            command_id=request.command_id,
        )
        if receipt is not None:
            return coordinator, None, None, PrimaryCommandResponse.from_receipt(
                request=request,
                operation=operation,
                receipt=receipt,
            )
    except PrimaryCommandBindingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    projection = coordinator.get_projection(task_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="LangGraph primary task not found")
    writer = coordinator.get_task_writer(task_id)
    if writer is None:
        raise HTTPException(status_code=409, detail="LangGraph task writer lease is unavailable")
    try:
        request.validate_path_and_fence(
            path_task_id=task_id,
            current_fencing_generation=writer.fencing_generation,
        )
        try:
            request.validate_projection(projection.projection_digest)
        except PrimaryCommandBindingError:
            if not request.runtime_replay_is_evidenced(projection.projection):
                raise
    except PrimaryCommandBindingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return coordinator, projection, writer, None


def _require_primary_owner_actor(actor_id: str) -> None:
    if not owner_identity_matches(actor_id, settings.slack_owner_user_id):
        raise HTTPException(status_code=403, detail="Owner identity mismatch")


@app.post(
    "/v1/primary/provider-activations/{provider_id}/advance",
    response_model=dict[str, bool],
    dependencies=[Depends(require_auth)],
)
def primary_advance_provider_activation(
    provider_id: str, request: ProviderActivationAdvanceRequest
) -> dict[str, bool]:
    _require_primary_owner_actor(request.actor_id)
    if primary_default_activation is None:
        raise HTTPException(status_code=503, detail="LangGraph primary is unavailable")
    activation = primary_default_activation.provider_activation
    if activation is None:
        raise HTTPException(
            status_code=409, detail="Provider activation bindings are unavailable"
        )
    try:
        activation.record(
            provider_id=provider_id,
            surface=request.surface,
            stage=request.stage,
            evidence_id=request.evidence_id,
            evidence_digest=request.evidence_digest,
            observed_at=request.observed_at,
            expires_at=request.expires_at,
        )
        return activation.health()
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _primary_command_response(
    request: object,
    operation: PrimaryCommandOperation,
) -> PrimaryCommandResponse:
    assert primary_default_activation is not None
    assert primary_default_activation.coordinator is not None
    projection = primary_default_activation.coordinator.get_projection(request.task_id)
    writer = primary_default_activation.coordinator.get_task_writer(request.task_id)
    if projection is None or writer is None:
        raise HTTPException(status_code=409, detail="LangGraph command result is unavailable")
    return PrimaryCommandResponse.from_projection(
        request=request,
        operation=operation,
        projection=projection,
        fencing_generation=writer.fencing_generation,
    )


def _record_primary_command(
    request: object,
    operation: PrimaryCommandOperation,
) -> PrimaryCommandResponse:
    assert primary_default_activation is not None
    coordinator = primary_default_activation.coordinator
    assert coordinator is not None
    projection = coordinator.get_projection(request.task_id)
    if projection is None:
        raise HTTPException(status_code=409, detail="LangGraph command result is unavailable")
    coordinator.record_command_receipt(
        task_id=request.task_id,
        operation=operation.value,
        command_id=request.command_id,
        command_digest=request.command_digest,
        projection=projection,
    )
    return _primary_command_response(request, operation)


@app.post(
    "/v1/primary/tasks/{task_id}/approvals/resume",
    response_model=PrimaryCommandResponse,
    dependencies=[Depends(require_auth)],
)
def primary_resume_approval(
    task_id: str, request: ApprovalResumeRequest
) -> PrimaryCommandResponse:
    _require_primary_owner_actor(request.actor_id)
    _, _, _, replay = _primary_command_projection(
        task_id, request, PrimaryCommandOperation.APPROVAL_RESUME
    )
    if replay is not None:
        return replay
    assert primary_default_activation is not None
    assert primary_default_activation.application is not None
    try:
        primary_default_activation.application.resume_approval(request.to_command())
        return _record_primary_command(
            request, PrimaryCommandOperation.APPROVAL_RESUME
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/primary/tasks/{task_id}/provider-results",
    response_model=PrimaryCommandResponse,
    dependencies=[Depends(require_auth)],
)
def primary_provider_result(
    task_id: str, request: ProviderResultRequest
) -> PrimaryCommandResponse:
    if request.actor_id != request.producer_id:
        raise HTTPException(status_code=403, detail="Provider actor binding mismatch")
    _, _, _, replay = _primary_command_projection(
        task_id, request, PrimaryCommandOperation.PROVIDER_RESULT
    )
    if replay is not None:
        return replay
    assert primary_default_activation is not None
    assert primary_default_activation.application is not None
    try:
        primary_default_activation.application.record_provider_result(
            task_id, request.to_command()
        )
        return _record_primary_command(
            request, PrimaryCommandOperation.PROVIDER_RESULT
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/primary/tasks/{task_id}/effects/reconcile",
    response_model=PrimaryCommandResponse,
    dependencies=[Depends(require_auth)],
)
def primary_reconcile_effect(
    task_id: str, request: EffectReconciliationRequest
) -> PrimaryCommandResponse:
    if request.actor_id != request.writer_owner_id:
        raise HTTPException(status_code=403, detail="Effect actor binding mismatch")
    _, _, _, replay = _primary_command_projection(
        task_id, request, PrimaryCommandOperation.EFFECT_RECONCILIATION
    )
    if replay is not None:
        return replay
    assert primary_default_activation is not None
    assert primary_default_activation.application is not None
    try:
        primary_default_activation.application.reconcile_effect(
            task_id, request.to_command()
        )
        return _record_primary_command(
            request, PrimaryCommandOperation.EFFECT_RECONCILIATION
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/primary/tasks/{task_id}/cancel",
    response_model=PrimaryCommandResponse,
    dependencies=[Depends(require_auth)],
)
def primary_cancel(
    task_id: str, request: CancellationRequest
) -> PrimaryCommandResponse:
    _require_primary_owner_actor(request.actor_id)
    _, _, _, replay = _primary_command_projection(
        task_id, request, PrimaryCommandOperation.CANCELLATION
    )
    if replay is not None:
        return replay
    assert primary_default_activation is not None
    assert primary_default_activation.application is not None
    try:
        primary_default_activation.application.cancel(task_id)
        return _record_primary_command(request, PrimaryCommandOperation.CANCELLATION)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/primary/tasks/{task_id}/recover",
    response_model=PrimaryCommandResponse,
    dependencies=[Depends(require_auth)],
)
def primary_recover(
    task_id: str, request: RecoveryRequest
) -> PrimaryCommandResponse:
    _require_primary_owner_actor(request.actor_id)
    _, _, _, replay = _primary_command_projection(
        task_id, request, PrimaryCommandOperation.RECOVERY
    )
    if replay is not None:
        return replay
    assert primary_default_activation is not None
    assert primary_default_activation.application is not None
    try:
        primary_default_activation.application.recover(task_id)
        return _record_primary_command(request, PrimaryCommandOperation.RECOVERY)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _apply_owner_decision(record: TaskRecord, approval: OwnerApproval) -> None:
    if record.status == TaskStatus.FAILED:
        apply_failed_predispatch_retry(record, approval)
        return
    if record.status != TaskStatus.OWNER_DECISION_REQUIRED:
        raise StaleApproval("Task is not awaiting an owner decision")
    if clarification_required(record):
        apply_owner_clarification(record, approval)
    else:
        apply_owner_approval(record, approval)
    record.status = TaskStatus.QUEUED
    record.message = approval.note


def _consume_owner_decision(task_store: TaskStore, task_id: str, approval: OwnerApproval) -> TaskRecord:
    versioned = task_store.get_versioned(task_id)
    if versioned is None:
        raise KeyError(task_id)
    current, expected_version = versioned
    gateway_nonce_digest: str | None = None
    gateway_nonce_expires_at: int | None = None
    verified_decision_kind: str | None = None
    is_paid_rearm = False
    if approval.source in {
        OwnerApprovalSource.SLACK_BUTTON,
        OwnerApprovalSource.SLACK_COMMAND,
    }:
        (
            verified_decision_kind,
            gateway_nonce_digest,
            gateway_nonce_expires_at,
        ) = verify_slack_gateway_proof(
            settings.slack_gateway_secret,
            settings.shared_secret,
            current,
            expected_version,
            approval,
            channel_id=settings.slack_channel_id,
            path=f"/v1/tasks/{task_id}/approve",
            allow_paid_runs=settings.allow_paid_runs,
        )
        is_paid_rearm = verified_decision_kind == "paid_rearm"
        if is_paid_rearm and approval.source != OwnerApprovalSource.SLACK_COMMAND:
            raise StaleApproval(
                "Paid rearm requires a fresh authenticated Slack command"
            )
    elif approval.slack_gateway_proof is not None:
        raise StaleApproval("Slack gateway proof cannot authorize an API request")
    elif current.status == TaskStatus.FAILED:
        raise StaleApproval(
            "Failed retry requires an authenticated Slack gateway proof"
        )
    elif (
        settings.allow_paid_runs
        and current.intake_authentication
        != IntakeAuthentication.SLACK_GATEWAY_ATTESTED
    ):
        raise StaleApproval(
            "Paid rearm requires a fresh authenticated Slack gateway proof"
        )

    def apply_verified_owner_decision(record: TaskRecord) -> None:
        if is_paid_rearm:
            reset_for_owner_clarification(record)
            record.intake_authentication = (
                IntakeAuthentication.SLACK_GATEWAY_ATTESTED
            )
            record.status = TaskStatus.QUEUED
            record.message = (
                "Authenticated paid rearm accepted; a fresh live plan is required."
            )
            return
        _apply_owner_decision(record, approval)

    stored = task_store.mutate_if_version(
        task_id,
        expected_version,
        apply_verified_owner_decision,
        gateway_nonce_digest=gateway_nonce_digest,
        gateway_nonce_expires_at=gateway_nonce_expires_at,
    )
    if stored is None:
        raise StaleApproval("Owner decision changed or was already consumed")
    return stored


@app.post("/v1/tasks/{task_id}/approve", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def approve(task_id: str, approval: OwnerApproval, background: BackgroundTasks) -> TaskRecord:
    if (
        primary_default_activation is not None
        and primary_default_activation.application is not None
        and primary_default_activation.application.get_task(task_id) is not None
    ):
        raise HTTPException(
            status_code=409,
            detail="Paid-off LangGraph task cannot cross its execution approval interrupt",
        )
    if not owner_identity_matches(approval.owner_user_id, settings.slack_owner_user_id):
        raise HTTPException(status_code=403, detail="Owner identity mismatch")
    try:
        stored = _consume_owner_decision(store, task_id, approval)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except StaleApproval as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(engine.run, task_id)
    return stored


@app.post(
    "/v1/tasks/{task_id}/oracle-recovery",
    response_model=TaskRecord,
    dependencies=[Depends(require_auth)],
)
def recover_oracle_stop(
    task_id: str,
    approval: OracleRecoveryApproval,
    background: BackgroundTasks,
) -> TaskRecord:
    if (
        primary_default_activation is not None
        and primary_default_activation.application is not None
        and primary_default_activation.application.get_task(task_id) is not None
    ):
        raise HTTPException(status_code=409, detail="Command is not valid for this LangGraph task")
    if not owner_identity_matches(approval.owner_user_id, settings.slack_owner_user_id):
        raise HTTPException(status_code=403, detail="Owner identity mismatch")
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    current, expected_version = versioned
    oracle_attempt_status = None
    oracle_retry_not_before = None
    if (
        approval.action == "retry_recoverable"
        and current.oracle_recovery is not None
        and current.oracle_recovery.artifact_id is not None
    ):
        (
            oracle_attempt_status,
            oracle_retry_not_before,
        ) = oracle_merge_store.attempt_recovery_state(
            current.oracle_recovery.artifact_id
        )
    try:
        stored = store.mutate_if_version(
            task_id,
            expected_version,
            lambda current: apply_oracle_recovery(
                current,
                approval,
                oracle_attempt_status=oracle_attempt_status,
                oracle_retry_not_before=oracle_retry_not_before,
            ),
        )
    except StaleApproval as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if stored is None:
        raise HTTPException(
            status_code=409,
            detail="Oracle recovery decision changed or was already consumed",
        )
    if stored.status == TaskStatus.QUEUED:
        background.add_task(engine.run, task_id)
    return stored


@app.post(
    "/v1/tasks/{task_id}/dispatch-reconciliation",
    response_model=TaskRecord,
    dependencies=[Depends(require_auth)],
)
def reconcile_dispatch(
    task_id: str,
    resolution: AmbiguousDispatchResolution,
    background: BackgroundTasks,
) -> TaskRecord:
    if (
        primary_default_activation is not None
        and primary_default_activation.application is not None
        and primary_default_activation.application.get_task(task_id) is not None
    ):
        raise HTTPException(status_code=409, detail="Use LangGraph effect reconciliation")
    if not owner_identity_matches(resolution.owner_user_id, settings.slack_owner_user_id):
        raise HTTPException(status_code=403, detail="Owner identity mismatch")
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    _, expected_version = versioned
    try:
        stored = store.mutate_if_version(
            task_id,
            expected_version,
            lambda record: apply_ambiguous_dispatch_resolution(record, resolution),
        )
    except StaleApproval as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if stored is None:
        raise HTTPException(status_code=409, detail="Task changed during reconciliation")
    background.add_task(engine.run, task_id)
    return stored


@app.post(
    "/v1/callbacks/claude/{task_id}",
    response_model=TaskRecord,
    dependencies=[Depends(require_claude_callback_auth)],
)
def claude_callback(task_id: str, callback: ClaudeCallback, background: BackgroundTasks) -> TaskRecord:
    if (
        primary_default_activation is not None
        and primary_default_activation.application is not None
        and primary_default_activation.application.get_task(task_id) is not None
    ):
        raise HTTPException(status_code=409, detail="LangGraph task has no pending Claude callback")
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    record, expected_version = versioned
    try:
        if not apply_claude_callback(record.model_copy(deep=True), callback):
            # Authenticated redelivery of the exact accepted callback is a
            # read-only success. It must not consume a task version or queue
            # another engine pass.
            authoritative = store.get_versioned(task_id)
            if authoritative is None:
                raise HTTPException(status_code=404, detail="Task not found")
            latest, latest_version = authoritative
            if latest_version == expected_version:
                return latest
            try:
                if not apply_claude_callback(
                    latest.model_copy(deep=True), callback
                ):
                    return latest
            except CallbackConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(
                status_code=409,
                detail="Task changed during callback replay validation",
            )
        stored = store.mutate_if_version(
            task_id,
            expected_version,
            lambda current: apply_claude_callback(current, callback),
        )
    except CallbackConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if stored is None:
        # A concurrent identical callback may have won the CAS. Re-evaluate
        # against fresh authority so exact redelivery remains idempotent while
        # any different callback or workflow transition still conflicts.
        latest = store.get(task_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            if not apply_claude_callback(latest.model_copy(deep=True), callback):
                return latest
        except CallbackConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=409, detail="Task changed during callback processing")
    background.add_task(engine.run, task_id)
    return stored


@app.post(
    "/v1/tasks/{task_id}/runs/{run_id}/complete",
    response_model=TaskRecord,
    dependencies=[Depends(require_auth)],
)
def complete_run(
    task_id: str,
    run_id: str,
    completion: ManualRunCompletion,
    background: BackgroundTasks,
) -> TaskRecord:
    if (
        primary_default_activation is not None
        and primary_default_activation.application is not None
        and primary_default_activation.application.get_task(task_id) is not None
    ):
        raise HTTPException(status_code=409, detail="Use the LangGraph provider-result command")
    if not owner_identity_matches(completion.owner_user_id, settings.slack_owner_user_id):
        raise HTTPException(status_code=403, detail="Owner identity mismatch")
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    _, expected_version = versioned
    try:
        stored = store.mutate_if_version(
            task_id,
            expected_version,
            lambda record: apply_manual_run_completion(record, run_id, completion),
        )
    except CallbackConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if stored is None:
        raise HTTPException(status_code=409, detail="Task changed during run completion")
    background.add_task(engine.run, task_id)
    return stored
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 22:31 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-23 | 21:04 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 01:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 09:57 UTC | END]]
# [[Codex Security | GPT-5.6 | 2026-08-25 | 11:10 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:47 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 14:26 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 17:28 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:09 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 06:39 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:42 UTC | END]]
