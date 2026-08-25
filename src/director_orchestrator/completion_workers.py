# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 02:14 UTC | BEGIN]]
"""Production boundary for isolated Completion Gate worker execution.

This module owns durable job identity and policy verification.  It deliberately
does not grant completion, merge, deployment, or provider authority: a successful
worker outcome is only sanitized gate evidence for the separately authenticated
Completion Gate validator/store.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import stat
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

from .completion_gates import canonical_json_bytes, domain_digest
from .process_isolation import trusted_neutral_cwd


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PINNED_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$"
)
_NON_ROOT_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


class CompletionWorkerError(RuntimeError):
    """A worker request, binding, or durable transition failed closed."""


class WorkerIdentityConflict(CompletionWorkerError):
    """An idempotency identity was reused for different immutable work."""


class WorkerTier(StrEnum):
    AUDITED_HOST = "audited_host"
    DISPOSABLE_CONTAINER = "disposable_container"


class WorkerJobStatus(StrEnum):
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    timeout_seconds: int = 300
    output_limit_bytes: int = 1_048_576
    memory_limit_bytes: int = 536_870_912
    cpu_limit: float = 1.0
    pids_limit: int = 64

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 3_600:
            raise CompletionWorkerError("worker timeout is outside the bounded range")
        if not 1 <= self.output_limit_bytes <= 8_388_608:
            raise CompletionWorkerError("worker output limit is outside the bounded range")
        if not 16_777_216 <= self.memory_limit_bytes <= 8_589_934_592:
            raise CompletionWorkerError("worker memory limit is outside the bounded range")
        if not 0.1 <= self.cpu_limit <= 8:
            raise CompletionWorkerError("worker CPU limit is outside the bounded range")
        if not 1 <= self.pids_limit <= 512:
            raise CompletionWorkerError("worker PID limit is outside the bounded range")


@dataclass(frozen=True, slots=True)
class AuditedHostProfile:
    spec_id: str
    runner_id: str
    registry_digest: str
    runner_definition_digest: str
    parameter_profile_id: str
    parameter_profile_digest: str
    launcher_profile_id: str
    fixed_argv: tuple[str, ...]
    expected_exit_code: int = 0
    limits: WorkerLimits = WorkerLimits()
    network_mode: str = "none"
    inherited_environment_names: tuple[str, ...] = ()
    spec_digest: str = ""
    tier: WorkerTier = WorkerTier.AUDITED_HOST

    @classmethod
    def build(
        cls,
        *,
        spec_id: str,
        runner_id: str,
        registry_digest: str,
        runner_definition_digest: str,
        parameter_profile_id: str,
        parameter_profile_digest: str,
        launcher_profile_id: str,
        fixed_argv: Sequence[str],
        expected_exit_code: int = 0,
        limits: WorkerLimits | None = None,
        inherited_environment_names: Sequence[str] = (),
    ) -> "AuditedHostProfile":
        argv = tuple(fixed_argv)
        inherited = tuple(sorted(set(inherited_environment_names)))
        value = {
            "tier": WorkerTier.AUDITED_HOST.value,
            "spec_id": spec_id,
            "runner_id": runner_id,
            "registry_digest": registry_digest,
            "runner_definition_digest": runner_definition_digest,
            "parameter_profile_id": parameter_profile_id,
            "parameter_profile_digest": parameter_profile_digest,
            "launcher_profile_id": launcher_profile_id,
            "fixed_argv": list(argv),
            "expected_exit_code": expected_exit_code,
            "limits": asdict(limits or WorkerLimits()),
            "network_mode": "none",
            "inherited_environment_names": list(inherited),
        }
        item = cls(
            spec_id=spec_id,
            runner_id=runner_id,
            registry_digest=registry_digest,
            runner_definition_digest=runner_definition_digest,
            parameter_profile_id=parameter_profile_id,
            parameter_profile_digest=parameter_profile_digest,
            launcher_profile_id=launcher_profile_id,
            fixed_argv=argv,
            expected_exit_code=expected_exit_code,
            limits=limits or WorkerLimits(),
            inherited_environment_names=inherited,
            spec_digest=domain_digest("director-completion-worker-spec/v1", value),
        )
        item._validate()
        return item

    def _validate(self) -> None:
        _validate_common_spec(self)
        if not _STABLE_ID.fullmatch(self.launcher_profile_id):
            raise CompletionWorkerError("host launcher profile identifier is invalid")
        if self.network_mode != "none":
            raise CompletionWorkerError("audited host workers default to no network")
        if any(
            not _STABLE_ID.fullmatch(name) or name.upper() != name
            for name in self.inherited_environment_names
        ):
            raise CompletionWorkerError("host environment allowlist is invalid")


@dataclass(frozen=True, slots=True)
class DisposableContainerSpec:
    spec_id: str
    runner_id: str
    registry_digest: str
    runner_definition_digest: str
    parameter_profile_id: str
    parameter_profile_digest: str
    image: str
    fixed_argv: tuple[str, ...]
    working_directory: str = "/workspace"
    expected_exit_code: int = 0
    limits: WorkerLimits = WorkerLimits()
    network_mode: str = "none"
    read_only_root: bool = True
    repository_read_only: bool = True
    no_new_privileges: bool = True
    drop_all_capabilities: bool = True
    run_as_user: str = "65532:65532"
    spec_digest: str = ""
    tier: WorkerTier = WorkerTier.DISPOSABLE_CONTAINER

    @classmethod
    def build(
        cls,
        *,
        spec_id: str,
        runner_id: str,
        registry_digest: str,
        runner_definition_digest: str,
        parameter_profile_id: str,
        parameter_profile_digest: str,
        image: str,
        fixed_argv: Sequence[str],
        working_directory: str = "/workspace",
        expected_exit_code: int = 0,
        limits: WorkerLimits | None = None,
        run_as_user: str = "65532:65532",
    ) -> "DisposableContainerSpec":
        argv = tuple(fixed_argv)
        value = {
            "tier": WorkerTier.DISPOSABLE_CONTAINER.value,
            "spec_id": spec_id,
            "runner_id": runner_id,
            "registry_digest": registry_digest,
            "runner_definition_digest": runner_definition_digest,
            "parameter_profile_id": parameter_profile_id,
            "parameter_profile_digest": parameter_profile_digest,
            "image": image,
            "fixed_argv": list(argv),
            "working_directory": working_directory,
            "expected_exit_code": expected_exit_code,
            "limits": asdict(limits or WorkerLimits()),
            "network_mode": "none",
            "read_only_root": True,
            "repository_read_only": True,
            "no_new_privileges": True,
            "drop_all_capabilities": True,
            "run_as_user": run_as_user,
        }
        item = cls(
            spec_id=spec_id,
            runner_id=runner_id,
            registry_digest=registry_digest,
            runner_definition_digest=runner_definition_digest,
            parameter_profile_id=parameter_profile_id,
            parameter_profile_digest=parameter_profile_digest,
            image=image,
            fixed_argv=argv,
            working_directory=working_directory,
            expected_exit_code=expected_exit_code,
            limits=limits or WorkerLimits(),
            run_as_user=run_as_user,
            spec_digest=domain_digest("director-completion-worker-spec/v1", value),
        )
        item._validate()
        return item

    def _validate(self) -> None:
        _validate_common_spec(self)
        if not _PINNED_IMAGE.fullmatch(self.image):
            raise CompletionWorkerError("container image must be pinned by sha256 digest")
        if self.working_directory != "/workspace":
            raise CompletionWorkerError("container working directory must be /workspace")
        if self.network_mode != "none":
            raise CompletionWorkerError("container workers default to no network")
        if not all(
            (
                self.read_only_root,
                self.repository_read_only,
                self.no_new_privileges,
                self.drop_all_capabilities,
            )
        ):
            raise CompletionWorkerError("container isolation controls are mandatory")
        if not _NON_ROOT_USER.fullmatch(self.run_as_user):
            raise CompletionWorkerError(
                "container worker must use a numeric non-root uid:gid"
            )


WorkerSpec = AuditedHostProfile | DisposableContainerSpec


def _validate_common_spec(spec: WorkerSpec) -> None:
    for value, label in (
        (spec.spec_id, "worker spec"),
        (spec.runner_id, "runner"),
        (spec.parameter_profile_id, "parameter profile"),
    ):
        if not _STABLE_ID.fullmatch(value):
            raise CompletionWorkerError(f"{label} identifier is invalid")
    for value, label in (
        (spec.registry_digest, "registry digest"),
        (spec.runner_definition_digest, "runner definition digest"),
        (spec.parameter_profile_digest, "parameter profile digest"),
        (spec.spec_digest, "worker spec digest"),
    ):
        if not _DIGEST.fullmatch(value):
            raise CompletionWorkerError(f"{label} is invalid")
    if not spec.fixed_argv or any(
        not isinstance(part, str) or not part or "\x00" in part
        for part in spec.fixed_argv
    ):
        raise CompletionWorkerError("worker argv must be fixed, non-empty strings")


class CompletionWorkerRegistry:
    def __init__(self, specs: Sequence[WorkerSpec]):
        mapped = {item.spec_id: item for item in specs}
        if not mapped or len(mapped) != len(specs):
            raise CompletionWorkerError("worker specs must be non-empty and unique")
        registry_digests = {item.registry_digest for item in specs}
        if len(registry_digests) != 1:
            raise CompletionWorkerError("worker specs must bind one runner registry")
        self._specs = mapped
        self.runner_registry_digest = next(iter(registry_digests))
        self.registry_digest = domain_digest(
            "director-completion-worker-registry/v1",
            {
                "runner_registry_digest": self.runner_registry_digest,
                "specs": {
                    key: mapped[key].spec_digest for key in sorted(mapped)
                },
            },
        )

    @property
    def tiers(self) -> frozenset[WorkerTier]:
        return frozenset(item.tier for item in self._specs.values())

    def resolve(self, spec_id: str) -> WorkerSpec:
        try:
            return self._specs[spec_id]
        except KeyError as exc:
            raise CompletionWorkerError("unknown completion worker spec") from exc


@dataclass(frozen=True, slots=True)
class CompletionWorkerRequest:
    job_id: str
    idempotency_key: str
    task_id: str
    assignment_id: str
    gate_id: str
    ledger_digest: str
    expected_head_sha: str
    runner_registry_digest: str
    worker_registry_digest: str
    worker_spec_id: str
    worker_spec_digest: str
    repository_root: str

    def validate(self) -> None:
        for value, label in (
            (self.job_id, "job"),
            (self.idempotency_key, "idempotency key"),
            (self.task_id, "task"),
            (self.assignment_id, "assignment"),
            (self.gate_id, "gate"),
            (self.worker_spec_id, "worker spec"),
        ):
            if (
                not value
                or len(value) > 255
                or any(character in value for character in ("\x00", "\r", "\n"))
            ):
                raise CompletionWorkerError(f"{label} identity is invalid")
        for value, label in (
            (self.ledger_digest, "ledger digest"),
            (self.runner_registry_digest, "runner registry digest"),
            (self.worker_registry_digest, "worker registry digest"),
            (self.worker_spec_digest, "worker spec digest"),
        ):
            if not _DIGEST.fullmatch(value):
                raise CompletionWorkerError(f"{label} is invalid")
        if not _GIT_SHA.fullmatch(self.expected_head_sha):
            raise CompletionWorkerError("expected head SHA is invalid")
        repository = Path(self.repository_root)
        if not repository.is_absolute():
            raise CompletionWorkerError("repository root must be absolute")

    @property
    def digest(self) -> str:
        self.validate()
        return domain_digest(
            "director-completion-worker-request/v1", asdict(self)
        )


@dataclass(frozen=True, slots=True)
class WorkerBackendCapabilities:
    tier: WorkerTier
    low_privilege_identity: bool
    network_isolation: bool
    resource_limits: bool
    process_tree_termination: bool
    read_only_repository: bool
    disposable_filesystem: bool


@dataclass(frozen=True, slots=True)
class WorkerBackendEvidence:
    job_id: str
    lease_digest: str
    tier: WorkerTier
    runner_registry_digest: str
    worker_registry_digest: str
    worker_spec_digest: str
    snapshot_manifest_digest: str
    snapshot_file_count: int
    before_head_sha: str
    after_head_sha: str
    before_clean: bool
    after_clean: bool
    exit_code: int | None
    timed_out: bool
    output_limit_exceeded: bool
    sanitized_output_digest: str
    decisive_output: str
    redaction_passed: bool
    secret_scan_passed: bool
    network_isolated: bool
    resource_limits_applied: bool
    low_privilege_identity_applied: bool
    process_tree_terminated: bool
    repository_read_only: bool
    disposable_filesystem: bool


class CompletionWorkerBackend(Protocol):
    @property
    def capabilities(self) -> WorkerBackendCapabilities: ...

    def execute(
        self,
        spec: WorkerSpec,
        request: CompletionWorkerRequest,
        *,
        lease_token: str,
    ) -> WorkerBackendEvidence: ...


@dataclass(frozen=True, slots=True)
class CompletionWorkerOutcome:
    status: WorkerJobStatus
    reason: str
    job_id: str
    request_digest: str
    evidence_digest: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _Claim:
    status: WorkerJobStatus
    outcome: CompletionWorkerOutcome | None


class CompletionWorkerStore:
    """Durable exactly-once reservation and append-only worker audit stream."""

    def __init__(self, path: str):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete = ON")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect(immediate=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS completion_worker_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'CLAIMED','RUNNING','SUCCEEDED','FAILED','RECONCILIATION_REQUIRED'
                    )),
                    lease_digest TEXT,
                    outcome_json TEXT
                );
                CREATE TABLE IF NOT EXISTS completion_worker_events (
                    job_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    event_digest TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(job_id, ordinal),
                    FOREIGN KEY(job_id) REFERENCES completion_worker_jobs(job_id)
                );
                CREATE TRIGGER IF NOT EXISTS completion_worker_events_no_update
                BEFORE UPDATE ON completion_worker_events
                BEGIN SELECT RAISE(ABORT, 'completion worker events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS completion_worker_events_no_delete
                BEFORE DELETE ON completion_worker_events
                BEGIN SELECT RAISE(ABORT, 'completion worker events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS completion_worker_jobs_no_delete
                BEFORE DELETE ON completion_worker_jobs
                BEGIN SELECT RAISE(ABORT, 'completion worker jobs cannot be deleted'); END;
                CREATE TRIGGER IF NOT EXISTS completion_worker_jobs_guard_update
                BEFORE UPDATE ON completion_worker_jobs
                WHEN
                    NEW.job_id != OLD.job_id OR
                    NEW.idempotency_key != OLD.idempotency_key OR
                    NEW.request_digest != OLD.request_digest OR
                    NOT (
                        (OLD.status = 'CLAIMED' AND NEW.status = 'RUNNING') OR
                        (OLD.status = 'RUNNING' AND NEW.status IN (
                            'SUCCEEDED','FAILED','RECONCILIATION_REQUIRED'
                        ))
                    )
                BEGIN SELECT RAISE(ABORT, 'invalid completion worker state transition'); END;
                """
            )

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> str:
        return canonical_json_bytes(value).decode("utf-8")

    def _event(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        ordinal = int(
            connection.execute(
                "SELECT COUNT(*) FROM completion_worker_events WHERE job_id=?",
                (job_id,),
            ).fetchone()[0]
        ) + 1
        event_value = {
            "job_id": job_id,
            "ordinal": ordinal,
            "event_type": event_type,
            "payload": dict(payload),
        }
        connection.execute(
            "INSERT INTO completion_worker_events VALUES (?, ?, ?, ?, ?)",
            (
                job_id,
                ordinal,
                domain_digest("director-completion-worker-event/v1", event_value),
                event_type,
                self._encode(payload),
            ),
        )

    def claim(self, request: CompletionWorkerRequest) -> _Claim:
        request_digest = request.digest
        with self._lock, self._connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM completion_worker_jobs WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if row is None:
                job_row = connection.execute(
                    "SELECT idempotency_key,request_digest FROM completion_worker_jobs "
                    "WHERE job_id=?",
                    (request.job_id,),
                ).fetchone()
                if job_row is not None:
                    raise WorkerIdentityConflict(
                        "worker job id is bound to different immutable work"
                    )
                connection.execute(
                    "INSERT INTO completion_worker_jobs(job_id,idempotency_key,request_digest,status) "
                    "VALUES (?, ?, ?, 'CLAIMED')",
                    (request.job_id, request.idempotency_key, request_digest),
                )
                self._event(
                    connection, request.job_id, "CLAIMED",
                    {"request_digest": request_digest},
                )
                return _Claim(WorkerJobStatus.CLAIMED, None)
            if row["job_id"] != request.job_id or not hmac.compare_digest(
                row["request_digest"], request_digest
            ):
                raise WorkerIdentityConflict(
                    "worker idempotency key is bound to different immutable work"
                )
            status = WorkerJobStatus(row["status"])
            if status in {WorkerJobStatus.SUCCEEDED, WorkerJobStatus.FAILED}:
                stored = json.loads(row["outcome_json"])
                outcome = CompletionWorkerOutcome(
                    status=WorkerJobStatus(stored["status"]),
                    reason=stored["reason"],
                    job_id=stored["job_id"],
                    request_digest=stored["request_digest"],
                    evidence_digest=stored.get("evidence_digest"),
                    replayed=bool(stored.get("replayed", False)),
                )
                return _Claim(status, CompletionWorkerOutcome(
                    status=outcome.status,
                    reason=outcome.reason,
                    job_id=outcome.job_id,
                    request_digest=outcome.request_digest,
                    evidence_digest=outcome.evidence_digest,
                    replayed=True,
                ))
            if status in {
                WorkerJobStatus.RUNNING,
                WorkerJobStatus.RECONCILIATION_REQUIRED,
            }:
                return _Claim(
                    WorkerJobStatus.RECONCILIATION_REQUIRED,
                    CompletionWorkerOutcome(
                        status=WorkerJobStatus.RECONCILIATION_REQUIRED,
                        reason=(
                            "worker execution may have started; authenticated readback "
                            "is required and the job will not be dispatched again"
                        ),
                        job_id=request.job_id,
                        request_digest=request_digest,
                    ),
                )
            return _Claim(status, None)

    def acquire(self, request: CompletionWorkerRequest) -> str | None:
        token = secrets.token_urlsafe(32)
        lease_digest = domain_digest("director-completion-worker-lease/v1", token)
        with self._lock, self._connect(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE completion_worker_jobs SET status='RUNNING', lease_digest=? "
                "WHERE job_id=? AND request_digest=? AND status='CLAIMED'",
                (lease_digest, request.job_id, request.digest),
            )
            if cursor.rowcount != 1:
                return None
            self._event(
                connection, request.job_id, "RUNNING",
                {"request_digest": request.digest, "lease_digest": lease_digest},
            )
        return token

    def finish(
        self,
        request: CompletionWorkerRequest,
        lease_token: str,
        outcome: CompletionWorkerOutcome,
    ) -> None:
        lease_digest = domain_digest("director-completion-worker-lease/v1", lease_token)
        encoded = self._encode(
            {
                "status": outcome.status.value,
                "reason": outcome.reason,
                "job_id": outcome.job_id,
                "request_digest": outcome.request_digest,
                "evidence_digest": outcome.evidence_digest,
                "replayed": False,
            }
        )
        with self._lock, self._connect(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE completion_worker_jobs SET status=?, outcome_json=? "
                "WHERE job_id=? AND request_digest=? AND status='RUNNING' AND lease_digest=?",
                (
                    outcome.status.value, encoded, request.job_id,
                    request.digest, lease_digest,
                ),
            )
            if cursor.rowcount != 1:
                raise CompletionWorkerError("worker completion lost its exact lease")
            self._event(
                connection, request.job_id, outcome.status.value,
                {
                    "request_digest": request.digest,
                    "evidence_digest": outcome.evidence_digest,
                },
            )

    def reconcile_after_backend_error(
        self,
        request: CompletionWorkerRequest,
        lease_token: str,
        error_code: str,
    ) -> None:
        outcome = CompletionWorkerOutcome(
            status=WorkerJobStatus.RECONCILIATION_REQUIRED,
            reason="worker backend outcome is ambiguous; do not redispatch",
            job_id=request.job_id,
            request_digest=request.digest,
        )
        self.finish(request, lease_token, outcome)
        # The public reason is bounded; the append-only audit records only a
        # non-sensitive exception class identifier.
        with self._lock, self._connect(immediate=True) as connection:
            self._event(
                connection, request.job_id, "BACKEND_ERROR",
                {"error_code": error_code[:128]},
            )


class CompletionWorkerExecutor:
    def __init__(
        self,
        registry: CompletionWorkerRegistry,
        store: CompletionWorkerStore,
        backends: Mapping[WorkerTier, CompletionWorkerBackend],
        *,
        enabled: bool = False,
        reserved_database_paths: Sequence[str] = (),
    ) -> None:
        self.registry = registry
        self.store = store
        self.backends = dict(backends)
        self.enabled = bool(enabled)
        worker_path = Path(store.path).resolve()
        self.store_isolated = all(
            worker_path != Path(path).resolve() for path in reserved_database_paths
        )

    def readiness(self) -> dict[str, Any]:
        required = self.registry.tiers
        tier_readiness: dict[str, bool] = {}
        for tier in sorted(required, key=lambda item: item.value):
            backend = self.backends.get(tier)
            ready = backend is not None and self._capabilities_satisfy(
                tier, backend.capabilities
            )
            tier_readiness[tier.value] = ready
        reasons: list[str] = []
        if not self.enabled:
            reasons.append("worker_execution_disabled")
        if not self.store_isolated:
            reasons.append("worker_store_not_isolated")
        for tier, ready in tier_readiness.items():
            if not ready:
                reasons.append(f"{tier}_backend_not_ready")
        return {
            "ready": self.enabled and self.store_isolated and all(tier_readiness.values()),
            "enabled": self.enabled,
            "store_isolated": self.store_isolated,
            "runner_registry_digest": self.registry.runner_registry_digest,
            "worker_registry_digest": self.registry.registry_digest,
            "tiers": tier_readiness,
            "network_default": "none",
            "shell": False,
            "completion_authority": False,
            "reasons": reasons,
        }

    @staticmethod
    def _capabilities_satisfy(
        tier: WorkerTier, capabilities: WorkerBackendCapabilities
    ) -> bool:
        if capabilities.tier != tier:
            return False
        common = (
            capabilities.low_privilege_identity
            and capabilities.network_isolation
            and capabilities.resource_limits
            and capabilities.process_tree_termination
            and capabilities.read_only_repository
        )
        if tier == WorkerTier.DISPOSABLE_CONTAINER:
            return common and capabilities.disposable_filesystem
        return common

    def execute(self, request: CompletionWorkerRequest) -> CompletionWorkerOutcome:
        request.validate()
        spec = self.registry.resolve(request.worker_spec_id)
        for actual, expected, label in (
            (request.runner_registry_digest, self.registry.runner_registry_digest, "runner registry"),
            (request.worker_registry_digest, self.registry.registry_digest, "worker registry"),
            (request.worker_spec_digest, spec.spec_digest, "worker spec"),
        ):
            if not hmac.compare_digest(actual, expected):
                raise CompletionWorkerError(f"{label} binding mismatch")
        readiness = self.readiness()
        if not readiness["ready"]:
            raise CompletionWorkerError(
                "completion worker is not ready: " + ", ".join(readiness["reasons"])
            )
        self._authenticate_repository(request)
        claim = self.store.claim(request)
        if claim.outcome is not None:
            return claim.outcome
        lease_token = self.store.acquire(request)
        if lease_token is None:
            replay = self.store.claim(request)
            if replay.outcome is None:
                raise CompletionWorkerError("worker reservation race is unresolved")
            return replay.outcome
        backend = self.backends[spec.tier]
        try:
            evidence = backend.execute(spec, request, lease_token=lease_token)
        except Exception as exc:
            self.store.reconcile_after_backend_error(
                request, lease_token, type(exc).__name__
            )
            return self.store.claim(request).outcome or CompletionWorkerOutcome(
                WorkerJobStatus.RECONCILIATION_REQUIRED,
                "worker backend outcome is ambiguous; do not redispatch",
                request.job_id,
                request.digest,
            )
        outcome = self._validate_evidence(request, spec, lease_token, evidence)
        self.store.finish(request, lease_token, outcome)
        return outcome

    def _validate_evidence(
        self,
        request: CompletionWorkerRequest,
        spec: WorkerSpec,
        lease_token: str,
        evidence: WorkerBackendEvidence,
    ) -> CompletionWorkerOutcome:
        lease_digest = domain_digest("director-completion-worker-lease/v1", lease_token)
        exact_bindings = (
            evidence.job_id == request.job_id,
            hmac.compare_digest(evidence.lease_digest, lease_digest),
            evidence.tier == spec.tier,
            hmac.compare_digest(evidence.runner_registry_digest, request.runner_registry_digest),
            hmac.compare_digest(evidence.worker_registry_digest, request.worker_registry_digest),
            hmac.compare_digest(evidence.worker_spec_digest, request.worker_spec_digest),
            _DIGEST.fullmatch(evidence.snapshot_manifest_digest) is not None,
            evidence.snapshot_file_count >= 0,
            evidence.before_head_sha == request.expected_head_sha,
            evidence.after_head_sha == request.expected_head_sha,
        )
        isolation = (
            evidence.before_clean,
            evidence.after_clean,
            evidence.redaction_passed,
            evidence.secret_scan_passed,
            evidence.network_isolated,
            evidence.resource_limits_applied,
            evidence.low_privilege_identity_applied,
            evidence.process_tree_terminated,
            evidence.repository_read_only,
            evidence.disposable_filesystem
            if spec.tier == WorkerTier.DISPOSABLE_CONTAINER
            else True,
        )
        if not all(exact_bindings) or not all(isolation):
            reason = "worker evidence failed exact binding or isolation validation"
            status = WorkerJobStatus.FAILED
        elif evidence.timed_out or evidence.output_limit_exceeded:
            reason = "worker exceeded a deterministic resource boundary"
            status = WorkerJobStatus.FAILED
        elif evidence.exit_code != spec.expected_exit_code:
            reason = "worker returned an unexpected exit code"
            status = WorkerJobStatus.FAILED
        elif not _DIGEST.fullmatch(evidence.sanitized_output_digest):
            reason = "worker output digest is malformed"
            status = WorkerJobStatus.FAILED
        else:
            reason = "sanitized worker evidence passed the execution boundary"
            status = WorkerJobStatus.SUCCEEDED
        evidence_digest = domain_digest(
            "director-completion-worker-evidence/v1", asdict(evidence)
        )
        return CompletionWorkerOutcome(
            status=status,
            reason=reason,
            job_id=request.job_id,
            request_digest=request.digest,
            evidence_digest=evidence_digest,
        )

    @staticmethod
    def _authenticate_repository(request: CompletionWorkerRequest) -> None:
        supplied = Path(request.repository_root)
        try:
            if supplied.is_symlink():
                raise CompletionWorkerError("worker repository root cannot be a link")
            attributes = getattr(supplied.stat(follow_symlinks=False), "st_file_attributes", 0)
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise CompletionWorkerError(
                    "worker repository root cannot be a reparse point"
                )
            root = supplied.resolve(strict=True)
        except OSError as exc:
            raise CompletionWorkerError("worker repository root cannot be authenticated") from exc
        head = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            cwd=trusted_neutral_cwd(), shell=False, check=False,
            capture_output=True, text=True, timeout=15,
        )
        status = subprocess.run(
            (
                "git", "-C", str(root), "status", "--porcelain=v2",
                "--untracked-files=all",
            ),
            cwd=trusted_neutral_cwd(), shell=False, check=False,
            capture_output=True, text=True, timeout=15,
        )
        if head.returncode != 0 or status.returncode != 0:
            raise CompletionWorkerError("worker repository Git state is unavailable")
        if head.stdout.strip() != request.expected_head_sha:
            raise CompletionWorkerError("worker repository head does not match the request")
        if status.stdout:
            raise CompletionWorkerError("worker repository must be clean before execution")
# [[Codex | GPT-5.6 | 2026-08-25 | 02:14 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 08:20 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | END]]
