from __future__ import annotations

import asyncio
import hmac
import secrets
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException

from . import __version__
from .config import Settings
from .engine import OrchestrationEngine
from .models import ClaudeCallback, OwnerApproval, ProviderName, SlackIntake, TaskRecord, TaskStatus
from .store import TaskStore


settings = Settings()
store = TaskStore(settings.sqlite_path)
engine = OrchestrationEngine(settings, store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    recovery_tasks = [asyncio.create_task(engine.run(record.task_id)) for record in store.list_recoverable()]
    yield
    for task in recovery_tasks:
        if not task.done():
            task.cancel()


app = FastAPI(title="Director Orchestrator", version=__version__, lifespan=lifespan)


def require_auth(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.shared_secret}"
    if not settings.shared_secret or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def owner_identity_matches(provided: str, configured: str) -> bool:
    """Constant-time owner check that fails closed when no owner is configured.

    hmac.compare_digest("", "") is True, so an unset SLACK_OWNER_USER_ID would
    otherwise let any caller approve gated assignments by sending an empty
    owner_user_id. require_auth fails closed the same way on a missing secret.
    """
    if not configured or not provided:
        return False
    return hmac.compare_digest(provided, configured)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "environment": settings.app_env,
        "paid_runs_enabled": settings.allow_paid_runs,
        "providers": settings.readiness(),
    }


@app.post("/v1/intake", response_model=TaskRecord, status_code=202, dependencies=[Depends(require_auth)])
def intake(payload: SlackIntake, background: BackgroundTasks) -> TaskRecord:
    task_id = f"DIR-{secrets.token_hex(5).upper()}"
    record = TaskRecord(task_id=task_id, status=TaskStatus.QUEUED, intake=payload)
    stored, created = store.create(record)
    if created:
        background.add_task(engine.run, stored.task_id)
    return stored


@app.get("/v1/tasks/{task_id}", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def get_task(task_id: str) -> TaskRecord:
    record = store.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return record


@app.post("/v1/tasks/{task_id}/approve", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def approve(task_id: str, approval: OwnerApproval, background: BackgroundTasks) -> TaskRecord:
    if not owner_identity_matches(approval.owner_user_id, settings.slack_owner_user_id):
        raise HTTPException(status_code=403, detail="Owner identity mismatch")
    record = get_task(task_id)
    if record.status != TaskStatus.OWNER_DECISION_REQUIRED:
        raise HTTPException(status_code=409, detail="Task is not awaiting an owner decision")
    assignments = sorted(record.plan.assignments, key=lambda item: item.sequence) if record.plan else []
    owner_assignment = (
        record.next_assignment_index < len(assignments)
        and assignments[record.next_assignment_index].agent == ProviderName.OWNER
    )
    if record.plan and (record.plan.owner_question or owner_assignment):
        if not approval.note.strip() or approval.note == "Approved":
            raise HTTPException(status_code=409, detail="This task needs a written clarification, not a bare approval")
        record.intake.text += f"\n\nOwner clarification: {approval.note.strip()}"
        record.plan = None
        record.next_assignment_index = 0
        record.provider_runs = []
        record.owner_approved = False
    else:
        record.owner_approved = True
    record.status = TaskStatus.QUEUED
    record.message = approval.note
    store.save(record)
    background.add_task(engine.run, task_id)
    return record


@app.post("/v1/callbacks/claude/{task_id}", response_model=TaskRecord, dependencies=[Depends(require_auth)])
def claude_callback(task_id: str, callback: ClaudeCallback, background: BackgroundTasks) -> TaskRecord:
    record = get_task(task_id)
    matching = [run for run in record.provider_runs if run.provider.value == "claude"]
    if not matching:
        raise HTTPException(status_code=409, detail="No Claude run is awaiting a callback")
    matching[-1].status = callback.status
    matching[-1].result = callback.result
    matching[-1].url = callback.session_url or matching[-1].url
    record.status = TaskStatus.REVIEWING
    stored = store.save(record)
    background.add_task(engine.run, task_id)
    return stored
