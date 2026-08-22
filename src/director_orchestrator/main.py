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
    verify_slack_gateway_proof,
)
from .callbacks import (
    CallbackConflict,
    apply_claude_callback,
    apply_manual_run_completion,
    bearer_matches,
)
from .config import Settings
from .engine import OrchestrationEngine
from .models import (
    AmbiguousDispatchResolution,
    ClaudeCallback,
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
from .store import TaskStore
from .usage import UsageMonitor


settings = Settings()
store = TaskStore(settings.sqlite_path)
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
engine = OrchestrationEngine(settings, store, oracle_merge_service)
usage_monitor = UsageMonitor(settings, store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    recovery_tasks = [asyncio.create_task(engine.run(record.task_id)) for record in store.list_recoverable()]
    monitor_task = asyncio.create_task(usage_monitor.run_forever())
    tasks = [*recovery_tasks, monitor_task]
    try:
        yield
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
    return {
        "ok": True,
        "version": __version__,
        "environment": settings.app_env,
        "paid_runs_enabled": settings.allow_paid_runs,
        "providers": settings.readiness(),
        "execution_profile": settings.execution_profile_readiness(),
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


@app.post("/v1/intake", response_model=TaskRecord, status_code=202, dependencies=[Depends(require_auth)])
def intake(payload: SlackIntake, background: BackgroundTasks) -> TaskRecord:
    task_id = f"DIR-{secrets.token_hex(5).upper()}"
    record = TaskRecord(task_id=task_id, status=TaskStatus.QUEUED, intake=payload)
    stored, created = store.create(record)
    if created:
        background.add_task(engine.run, stored.task_id)
    return stored


@app.get("/v1/tasks/{task_id}", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def get_task(task_id: str, response: Response) -> TaskRecord:
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    record, version = versioned
    response.headers["X-Task-Record-Version"] = str(version)
    return record


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
    if approval.source in {
        OwnerApprovalSource.SLACK_BUTTON,
        OwnerApprovalSource.SLACK_COMMAND,
    }:
        gateway_nonce_digest, gateway_nonce_expires_at = verify_slack_gateway_proof(
            settings.slack_gateway_secret,
            settings.shared_secret,
            current,
            expected_version,
            approval,
            channel_id=settings.slack_channel_id,
            path=f"/v1/tasks/{task_id}/approve",
        )
    elif approval.slack_gateway_proof is not None:
        raise StaleApproval("Slack gateway proof cannot authorize an API request")
    elif current.status == TaskStatus.FAILED:
        raise StaleApproval(
            "Failed retry requires an authenticated Slack gateway proof"
        )
    stored = task_store.mutate_if_version(
        task_id,
        expected_version,
        lambda current: _apply_owner_decision(current, approval),
        gateway_nonce_digest=gateway_nonce_digest,
        gateway_nonce_expires_at=gateway_nonce_expires_at,
    )
    if stored is None:
        raise StaleApproval("Owner decision changed or was already consumed")
    return stored


@app.post("/v1/tasks/{task_id}/approve", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def approve(task_id: str, approval: OwnerApproval, background: BackgroundTasks) -> TaskRecord:
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
    versioned = store.get_versioned(task_id)
    if versioned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    record, expected_version = versioned
    try:
        if not apply_claude_callback(record.model_copy(deep=True), callback):
            # Authenticated redelivery of the exact accepted callback is a
            # read-only success. It must not consume a task version or queue
            # another engine pass.
            return record
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
