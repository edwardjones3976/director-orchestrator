from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import hmac
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .models import ClaudeCallback, ProviderName, ProviderRun, TaskRecord
from .policy import canonical_repository


class OracleReviewVerdict(StrEnum):
    ACCEPT = "ACCEPT"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    SERIOUS_SAFETY_STOP = "SERIOUS_SAFETY_STOP"


class OracleMergeStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    SERIOUS_SAFETY_STOP = "SERIOUS_SAFETY_STOP"
    MERGED = "MERGED"
    ERROR = "ERROR"


class OracleReviewArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["edge_oracle_exact_sha_review/v1"]
    artifact_id: str = Field(min_length=1, max_length=255)
    assignment_id: str = Field(min_length=1, max_length=255)
    project: str = Field(min_length=1, max_length=255)
    repository: str = Field(min_length=3, max_length=255)
    pull_number: int = Field(ge=1)
    base_ref: str = Field(min_length=1, max_length=255)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_ref: str = Field(min_length=1, max_length=255)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    implementation_provider: str = Field(min_length=1, max_length=64)
    implementation_actor: str = Field(min_length=1, max_length=255)
    reviewer_provider: str = Field(min_length=1, max_length=64)
    review_run_id: str = Field(min_length=1, max_length=255)
    verdict: OracleReviewVerdict
    summary: str = Field(min_length=1, max_length=10_000)
    evidence: list[str] = Field(min_length=1, max_length=50)
    acceptance_checks: list[str] = Field(min_length=1, max_length=50)
    rollback_instructions: str = Field(min_length=20, max_length=10_000)
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")

    def signing_payload(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json", exclude={"signature"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def sign(self, secret: str) -> OracleReviewArtifact:
        if not secret:
            raise ValueError("review signing secret is not configured")
        signature = hmac.new(
            secret.encode("utf-8"), self.signing_payload(), hashlib.sha256
        ).hexdigest()
        return self.model_copy(update={"signature": signature})

    def signature_is_valid(self, secret: str) -> bool:
        if not secret:
            return False
        expected = hmac.new(
            secret.encode("utf-8"), self.signing_payload(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def authenticate_provider_review_artifact(
    record: TaskRecord, run: ProviderRun, secret: str
) -> OracleReviewArtifact | None:
    """Bind one Claude or Cursor result to its stored exact-SHA review assignment."""
    if run.provider not in {ProviderName.CLAUDE, ProviderName.CURSOR}:
        return None
    successful_statuses = {
        ProviderName.CLAUDE: {"completed"},
        ProviderName.CURSOR: {"FINISHED", "completed"},
    }
    if run.status not in successful_statuses[run.provider]:
        raise ValueError("only a successful provider run can authenticate a review")
    if not run.result:
        return None
    try:
        payload = json.loads(run.result)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("artifact_type") != (
        "edge_oracle_exact_sha_review/v1"
    ):
        return None
    if "signature" in payload:
        raise ValueError("provider review output must not supply an authentication signature")
    payload["signature"] = "0" * 64
    artifact = OracleReviewArtifact.model_validate(payload)

    matching_runs = [
        stored
        for stored in record.provider_runs
        if stored.provider == run.provider
        and hmac.compare_digest(stored.run_id, run.run_id)
        and stored.assignment_sequence == run.assignment_sequence
    ]
    if len(matching_runs) != 1 or matching_runs[0].assignment_sequence is None:
        raise ValueError("provider review output is not bound to one stored assignment")
    stored_run = matching_runs[0]
    if stored_run.status not in successful_statuses[run.provider]:
        raise ValueError("stored provider review run is not successful")
    if not hmac.compare_digest(stored_run.result or "", run.result):
        raise ValueError("provider review result does not match the stored run")
    sequence = stored_run.assignment_sequence
    if artifact.review_run_id != run.run_id:
        raise ValueError("review artifact run does not match the authenticated callback")
    if artifact.reviewer_provider.strip().lower() != run.provider.value:
        raise ValueError("review artifact provider does not match the authenticated run")
    if artifact.assignment_id != f"{record.task_id}:{sequence}":
        raise ValueError("review artifact assignment identifier does not match the task")
    if record.plan is None or artifact.project.strip().lower() != record.plan.project.strip().lower():
        raise ValueError("review artifact project does not match the stored plan")
    assignments = [item for item in record.plan.assignments if item.sequence == sequence]
    if len(assignments) != 1 or assignments[0].agent != run.provider:
        raise ValueError("stored assignment does not match the review provider")
    assignment = assignments[0]
    if not assignment.repository or canonical_repository(assignment.repository) != canonical_repository(
        artifact.repository
    ):
        raise ValueError("review artifact repository does not match the stored assignment")
    if assignment.starting_ref != artifact.base_ref:
        raise ValueError("review artifact base ref does not match the stored assignment")
    dependency_outcomes = [
        outcome
        for outcome in record.assignment_outcomes
        if outcome.assignment_sequence in assignment.depends_on
    ]
    if len(dependency_outcomes) != 1:
        raise ValueError(
            "review assignment must depend on exactly one accepted implementation outcome"
        )
    implementation = dependency_outcomes[0]
    if artifact.implementation_provider.strip().lower() != implementation.provider.value:
        raise ValueError(
            "review artifact implementation provider does not match the accepted dependency"
        )
    if implementation.provider == run.provider:
        raise ValueError("review provider cannot accept its own implementation outcome")
    review_context = "\n".join([assignment.task, *assignment.acceptance]).lower()
    if "review" not in review_context or artifact.head_sha not in review_context:
        raise ValueError("provider assignment was not pinned to the exact reviewed head SHA")
    if artifact.base_sha not in review_context:
        raise ValueError("provider assignment was not pinned to the exact reviewed base SHA")
    return artifact.sign(secret)


def authenticate_claude_review_artifact(
    record: TaskRecord, callback: ClaudeCallback, secret: str
) -> OracleReviewArtifact | None:
    """Compatibility wrapper for the authenticated Claude callback boundary."""
    if callback.status != "completed":
        raise ValueError("only a completed Claude callback can authenticate a review")
    matching_runs = [
        run
        for run in record.provider_runs
        if run.provider == ProviderName.CLAUDE
        and hmac.compare_digest(run.run_id, callback.run_id)
    ]
    if len(matching_runs) != 1:
        raise ValueError("Claude review output is not bound to one stored assignment")
    run = matching_runs[0]
    if run.result is None:
        raise ValueError("Claude callback result was not persisted before authentication")
    if not hmac.compare_digest(run.result, callback.result):
        raise ValueError("Claude callback result does not match the stored run")
    return authenticate_provider_review_artifact(record, run, secret)


class OracleChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=2_000)
    previous_filename: str | None = Field(default=None, min_length=1, max_length=2_000)
    status: str = Field(min_length=1, max_length=64)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    patch: str | None = Field(default=None, max_length=200_000)


class OracleCheckRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: int = Field(ge=1)
    suite_id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=255)
    status: str = Field(min_length=1, max_length=64)
    conclusion: str | None = Field(default=None, max_length=64)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    app_slug: str = Field(min_length=1, max_length=255)


class OraclePullRequestSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    pull_number: int = Field(ge=1)
    title: str = Field(max_length=2_000)
    body: str = Field(default="", max_length=100_000)
    state: str
    draft: bool
    mergeable: bool | None
    merged: bool = False
    base_ref: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_ref: str
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_repository: str = Field(min_length=3, max_length=255)
    author_login: str = Field(min_length=1, max_length=255)
    changed_files: list[OracleChangedFile]
    checks: list[OracleCheckRun]
    blocking_review_threads: int = Field(default=0, ge=0)
    review_threads_complete: bool = True
    review_decision: str | None = Field(default=None, max_length=64)


class OracleGitHubMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merged: bool
    merge_commit_sha: str | None = None
    actor: str = Field(min_length=1, max_length=255)


class OracleMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    artifact_digest: str
    status: OracleMergeStatus
    repository: str
    pull_number: int
    reviewed_sha: str
    reasons: list[str] = Field(default_factory=list)
    serious_safety_reasons: list[str] = Field(default_factory=list)
    check_run_ids: list[int] = Field(default_factory=list)
    check_suite_ids: list[int] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    merge_commit_sha: str | None = None
    actor: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OracleGitHubGateway(Protocol):
    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot: ...

    def merge(
        self,
        repository: str,
        pull_number: int,
        expected_sha: str,
        expected_base_sha: str,
    ) -> OracleGitHubMerge: ...

    def mark_ready(
        self,
        repository: str,
        pull_number: int,
        expected_sha: str,
        expected_base_sha: str,
    ) -> bool: ...


class OracleMergePolicy:
    _SERIOUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "funded wagering or sportsbook operation",
            re.compile(
                r"\b(?:place|submit|execute|authorize|enable|automate|start|run)\w*\b.{0,80}"
                r"\b(?:real[- ]money|funded|wager|bet|sportsbook|bankroll|settlement)\b",
                re.I | re.S,
            ),
        ),
        (
            "deployment or publication",
            re.compile(
                r"\b(?:deploy|publish|release|activate|go live)\w*\b.{0,80}"
                r"\b(?:production|service|artifact|release|public)\b",
                re.I | re.S,
            ),
        ),
        (
            "live scheduler activation or modification",
            re.compile(
                r"\b(?:enable|activate|start|run|modify|change)\w*\b.{0,80}"
                r"\b(?:live scheduler|scheduler service|scheduled job)\b",
                re.I | re.S,
            ),
        ),
        (
            "permission or repository administration",
            re.compile(
                r"\b(?:branch protection|repository admin|collaborator|permission expansion|access control)\b",
                re.I,
            ),
        ),
        (
            "safety-gate weakening",
            re.compile(
                r"\b(?:bypass|disable|remove|weaken)\w*\b.{0,50}"
                r"\b(?:approval|security|budget|provenance|idempotency|calibration|promotion)\b",
                re.I | re.S,
            ),
        ),
    )
    _SECRET_MATERIAL = re.compile(
        r"(?im)(?:^\s*(?:[A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET)|GITHUB_TOKEN)\s*="
        r"\s*(?!$|REPLACE_WITH_|PASTE_|\$\{)[^\s#]+|"
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|"
        r"\b(?:gh[oprsu]_[A-Za-z0-9_]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,})\b)"
    )

    def __init__(self, settings: Settings):
        self.enabled = settings.oracle_automerge_enabled
        self.kill_switch = settings.oracle_automerge_kill_switch
        self.project = settings.oracle_automerge_project.strip().lower()
        self.repository = canonical_repository(settings.oracle_automerge_repository)
        self.base_ref = settings.oracle_automerge_base_ref
        self.branch_prefix = settings.oracle_automerge_branch_prefix
        self.allowed_paths = settings.oracle_automerge_allowed_paths
        self.required_checks = settings.oracle_automerge_required_checks
        self.required_check_app = settings.oracle_automerge_required_check_app
        self.review_secret = settings.oracle_automerge_review_secret

    @staticmethod
    def _added_patch_text(snapshot: OraclePullRequestSnapshot) -> str:
        additions: list[str] = []
        for changed in snapshot.changed_files:
            if changed.patch is None:
                continue
            additions.extend(
                line[1:]
                for line in changed.patch.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
        return "\n".join(additions)

    def evaluate(
        self,
        artifact: OracleReviewArtifact,
        snapshot: OraclePullRequestSnapshot,
        *,
        allow_draft_transition: bool = False,
    ) -> OracleMergeResult:
        errors: list[str] = []
        serious: list[str] = []

        if not self.enabled:
            errors.append("Oracle automatic merge is disabled")
        if self.kill_switch:
            errors.append("Oracle automatic merge kill switch is active")
        if not self.required_checks:
            errors.append("required exact-head checks are not configured")
        if artifact.verdict == OracleReviewVerdict.SERIOUS_SAFETY_STOP:
            serious.append("reviewer returned SERIOUS_SAFETY_STOP")
        elif artifact.verdict != OracleReviewVerdict.ACCEPT:
            errors.append("reviewer did not accept the reviewed commit")
        if artifact.reviewer_provider.lower() not in {"claude", "cursor"}:
            errors.append("the exact-SHA reviewer must be Claude or Cursor")
        if artifact.reviewer_provider.lower() == artifact.implementation_provider.lower():
            errors.append("the implementation author cannot accept its own work")
        if artifact.implementation_actor.lower() != snapshot.author_login.lower():
            errors.append("pull request author does not match the recorded implementation actor")

        if artifact.project.strip().lower() != self.project:
            errors.append("project does not match the Edge Oracle policy")
        artifact_repository = canonical_repository(artifact.repository)
        snapshot_repository = canonical_repository(snapshot.repository)
        if artifact_repository != self.repository or snapshot_repository != self.repository:
            errors.append("repository does not match the Edge Oracle policy")
        if canonical_repository(snapshot.head_repository) != self.repository:
            errors.append("pull request head repository does not match the Oracle policy")
        if artifact.base_ref != self.base_ref or snapshot.base_ref != self.base_ref:
            errors.append("base ref does not match the Edge Oracle policy")
        if artifact.base_sha != snapshot.base_sha:
            errors.append("base SHA changed after review")
        if not artifact.head_ref.startswith(self.branch_prefix) or not snapshot.head_ref.startswith(
            self.branch_prefix
        ):
            errors.append("head branch does not use the authorized Oracle prefix")
        if artifact.pull_number != snapshot.pull_number:
            errors.append("pull request number does not match the review artifact")
        if artifact.head_ref != snapshot.head_ref:
            errors.append("head ref changed after review")
        if artifact.head_sha != snapshot.head_sha:
            errors.append("head SHA changed after review")

        if snapshot.state.lower() != "open" or snapshot.merged:
            errors.append("pull request is not open and unmerged")
        if snapshot.draft and not allow_draft_transition:
            errors.append("draft pull requests cannot be merged automatically")
        if snapshot.mergeable is not True:
            errors.append("pull request is conflicting or mergeability is unresolved")
        if not snapshot.review_threads_complete:
            errors.append("review-thread enumeration is incomplete")
        if snapshot.blocking_review_threads:
            errors.append("blocking review threads remain unresolved")
        if (snapshot.review_decision or "").upper() == "CHANGES_REQUESTED":
            errors.append("GitHub reviews still request changes")

        candidate_paths = {
            path
            for changed in snapshot.changed_files
            for path in (changed.filename, changed.previous_filename)
            if path is not None
        }
        unauthorized = sorted(
            path
            for path in candidate_paths
            if not any(fnmatch.fnmatchcase(path, pattern) for pattern in self.allowed_paths)
        )
        if unauthorized:
            errors.append("changed paths exceed the Oracle allowlist: " + ", ".join(unauthorized))
        incomplete_files = sorted(
            changed.filename for changed in snapshot.changed_files if changed.patch is None
        )
        if incomplete_files:
            errors.append(
                "changed file content is incomplete and cannot be scanned: "
                + ", ".join(incomplete_files)
            )

        checks_by_name: dict[str, list[OracleCheckRun]] = {}
        for check in snapshot.checks:
            checks_by_name.setdefault(check.name, []).append(check)
        missing_from_review = sorted(
            set(self.required_checks) - set(artifact.acceptance_checks)
        )
        if missing_from_review:
            errors.append(
                "review artifact omits required checks: " + ", ".join(missing_from_review)
            )
        for name in self.required_checks:
            matches = checks_by_name.get(name, [])
            if not matches:
                errors.append(f"required check is missing: {name}")
                continue
            if len(matches) != 1:
                errors.append(f"required check is ambiguous: {name}")
                continue
            check = matches[0]
            if check.app_slug != self.required_check_app:
                errors.append(f"required check has an untrusted producer: {name}")
            elif check.head_sha != artifact.head_sha:
                errors.append(f"required check is not bound to the reviewed SHA: {name}")
            elif check.status.lower() != "completed" or (check.conclusion or "").lower() != "success":
                errors.append(f"required check is not successful: {name}")
        for check in snapshot.checks:
            if check.status.lower() != "completed" or (check.conclusion or "").lower() != "success":
                errors.append(f"non-green check blocks merge: {check.name}")

        added_text = self._added_patch_text(snapshot)
        safety_text = "\n".join(
            [snapshot.title, snapshot.body, *(item.filename for item in snapshot.changed_files), added_text]
        )
        for label, pattern in self._SERIOUS_PATTERNS:
            if pattern.search(safety_text):
                serious.append(label)
        if any(
            changed.status.lower() == "removed"
            or (changed.deletions >= 100 and changed.deletions > changed.additions * 2)
            for changed in snapshot.changed_files
        ):
            serious.append("material deletion")
        if self._SECRET_MATERIAL.search(added_text):
            serious.append("credential or secret material")

        if serious:
            status = OracleMergeStatus.SERIOUS_SAFETY_STOP
        elif errors:
            status = OracleMergeStatus.REJECTED
        else:
            status = OracleMergeStatus.ELIGIBLE
        return OracleMergeResult(
            artifact_id=artifact.artifact_id,
            artifact_digest=artifact.digest(),
            status=status,
            repository=self.repository,
            pull_number=artifact.pull_number,
            reviewed_sha=artifact.head_sha,
            reasons=sorted(set(errors)),
            serious_safety_reasons=sorted(set(serious)),
            check_run_ids=sorted(check.run_id for check in snapshot.checks),
            check_suite_ids=sorted(
                {check.suite_id for check in snapshot.checks if check.suite_id is not None}
            ),
            changed_paths=sorted(candidate_paths),
        )


class OracleMergeStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oracle_merge_attempts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_digest TEXT NOT NULL,
                    assignment_id TEXT NOT NULL UNIQUE,
                    review_run_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_oracle_merge_assignment "
                "ON oracle_merge_attempts(assignment_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oracle_merge_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS oracle_merge_audit_no_update
                BEFORE UPDATE ON oracle_merge_audit
                BEGIN SELECT RAISE(ABORT, 'oracle merge audit is append-only'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS oracle_merge_audit_no_delete
                BEFORE DELETE ON oracle_merge_audit
                BEGIN SELECT RAISE(ABORT, 'oracle merge audit is append-only'); END
                """
            )

    def reserve(self, artifact: OracleReviewArtifact) -> OracleMergeResult | None:
        digest = artifact.digest()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT artifact_digest, status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            if existing:
                if existing["artifact_digest"] != digest:
                    raise ValueError("review artifact identifier was reused with different content")
                if existing["result_json"]:
                    return OracleMergeResult.model_validate_json(existing["result_json"])
                raise RuntimeError("the review artifact is already being evaluated")
            assignment = connection.execute(
                "SELECT artifact_id FROM oracle_merge_attempts WHERE assignment_id = ?",
                (artifact.assignment_id,),
            ).fetchone()
            if assignment:
                raise ValueError("assignment already has terminal review authority")
            review_run = connection.execute(
                "SELECT artifact_id FROM oracle_merge_attempts WHERE review_run_id = ?",
                (artifact.review_run_id,),
            ).fetchone()
            if review_run:
                raise ValueError("review run was reused for another merge artifact")
            try:
                connection.execute(
                    "INSERT INTO oracle_merge_attempts VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        artifact.artifact_id,
                        digest,
                        artifact.assignment_id,
                        artifact.review_run_id,
                        "EVALUATING",
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("assignment or review run was already consumed") from exc
            self._append(connection, artifact.artifact_id, "review_artifact_reserved", {
                "artifact_digest": digest,
                "assignment_id": artifact.assignment_id,
                "review_run_id": artifact.review_run_id,
                "repository": canonical_repository(artifact.repository),
                "pull_number": artifact.pull_number,
                "head_sha": artifact.head_sha,
            })
        return None

    @staticmethod
    def _append(
        connection: sqlite3.Connection, artifact_id: str, event_type: str, payload: dict
    ) -> None:
        connection.execute(
            "INSERT INTO oracle_merge_audit(artifact_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (
                artifact_id,
                event_type,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def complete(self, result: OracleMergeResult) -> OracleMergeResult:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE oracle_merge_attempts SET status = ?, result_json = ?, updated_at = ? WHERE artifact_id = ?",
                (
                    result.status.value,
                    result.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    result.artifact_id,
                ),
            )
            self._append(
                connection,
                result.artifact_id,
                "merge_gate_completed",
                result.model_dump(mode="json"),
            )
        return result

    def audit_events(self, artifact_id: str) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, payload_json, created_at FROM oracle_merge_audit WHERE artifact_id = ? ORDER BY id",
                (artifact_id,),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


class OracleMergeService:
    def __init__(
        self,
        policy: OracleMergePolicy,
        store: OracleMergeStore,
        github: OracleGitHubGateway | None,
    ):
        self.policy = policy
        self.store = store
        self.github = github

    def process(self, artifact: OracleReviewArtifact) -> OracleMergeResult:
        if not artifact.signature_is_valid(self.policy.review_secret):
            raise ValueError("review artifact signature is invalid")
        existing = self.store.reserve(artifact)
        if existing is not None:
            return existing

        if not self.policy.enabled or self.policy.kill_switch:
            placeholder = OraclePullRequestSnapshot(
                repository=artifact.repository,
                pull_number=artifact.pull_number,
                title="",
                state="open",
                draft=False,
                mergeable=None,
                base_ref=artifact.base_ref,
                base_sha=artifact.base_sha,
                head_ref=artifact.head_ref,
                head_sha=artifact.head_sha,
                head_repository=artifact.repository,
                author_login=artifact.implementation_actor,
                changed_files=[],
                checks=[],
            )
            return self.store.complete(self.policy.evaluate(artifact, placeholder))
        if self.github is None:
            return self.store.complete(
                OracleMergeResult(
                    artifact_id=artifact.artifact_id,
                    artifact_digest=artifact.digest(),
                    status=OracleMergeStatus.ERROR,
                    repository=self.policy.repository,
                    pull_number=artifact.pull_number,
                    reviewed_sha=artifact.head_sha,
                    reasons=["GitHub merger is not configured"],
                )
            )

        try:
            snapshot = self.github.snapshot(artifact.repository, artifact.pull_number)
        except Exception as exc:
            return self.store.complete(
                OracleMergeResult(
                    artifact_id=artifact.artifact_id,
                    artifact_digest=artifact.digest(),
                    status=OracleMergeStatus.ERROR,
                    repository=self.policy.repository,
                    pull_number=artifact.pull_number,
                    reviewed_sha=artifact.head_sha,
                    reasons=[f"GitHub validation failed closed: {type(exc).__name__}"],
                )
            )
        if snapshot.draft:
            preflight = self.policy.evaluate(
                artifact, snapshot, allow_draft_transition=True
            )
            if preflight.reasons or preflight.serious_safety_reasons:
                return self.store.complete(preflight)
            try:
                ready = self.github.mark_ready(
                    self.policy.repository,
                    artifact.pull_number,
                    artifact.head_sha,
                    artifact.base_sha,
                )
                if not ready:
                    raise RuntimeError("GitHub refused to mark the pull request ready")
                snapshot = self.github.snapshot(
                    artifact.repository, artifact.pull_number
                )
            except Exception as exc:
                preflight.status = OracleMergeStatus.ERROR
                preflight.reasons = [
                    f"GitHub ready-for-review transition failed closed: {type(exc).__name__}"
                ]
                return self.store.complete(preflight)
        decision = self.policy.evaluate(artifact, snapshot)
        if decision.reasons or decision.serious_safety_reasons:
            return self.store.complete(decision)

        try:
            merged = self.github.merge(
                self.policy.repository,
                artifact.pull_number,
                artifact.head_sha,
                artifact.base_sha,
            )
        except Exception as exc:
            decision.status = OracleMergeStatus.ERROR
            decision.reasons = [f"GitHub merge failed closed: {type(exc).__name__}"]
            return self.store.complete(decision)
        if not merged.merged or not merged.merge_commit_sha:
            decision.status = OracleMergeStatus.ERROR
            decision.reasons = ["GitHub refused the exact-SHA merge"]
            decision.actor = merged.actor
            return self.store.complete(decision)

        decision.status = OracleMergeStatus.MERGED
        decision.merge_commit_sha = merged.merge_commit_sha
        decision.actor = merged.actor
        return self.store.complete(decision)


class GitHubTokenProvider(Protocol):
    @property
    def actor(self) -> str: ...

    def get_token(self) -> str: ...

    def invalidate(self) -> None: ...


class GitHubAppInstallationTokenProvider:
    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key_pem: bytes,
        *,
        api_base: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        if not app_id or not installation_id or not private_key_pem:
            raise ValueError("GitHub App credentials are incomplete")
        if not app_id.isdigit() or not installation_id.isdigit():
            raise ValueError("GitHub App and installation IDs must be numeric")
        self._app_id = app_id
        self._installation_id = installation_id
        self._private_key_pem = private_key_pem
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at: datetime | None = None
        self._client = httpx.Client(
            base_url=api_base.rstrip("/"),
            timeout=30,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "director-orchestrator-oracle-merge-gate",
            },
        )

    @property
    def actor(self) -> str:
        return f"github-app-installation:{self._installation_id}"

    def invalidate(self) -> None:
        with self._lock:
            self._token = ""
            self._expires_at = None

    def get_token(self) -> str:
        with self._lock:
            now = self._now()
            if (
                self._token
                and self._expires_at is not None
                and self._expires_at > now.replace(microsecond=0) + timedelta(seconds=60)
            ):
                return self._token

            issued_at = int(now.timestamp()) - 60
            app_jwt = jwt.encode(
                {"iat": issued_at, "exp": issued_at + 600, "iss": self._app_id},
                self._private_key_pem,
                algorithm="RS256",
            )
            response = self._client.post(
                f"/app/installations/{self._installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {app_jwt}"},
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    "GitHub App installation-token request failed with status "
                    f"{response.status_code}"
                )
            payload = response.json()
            token = payload.get("token")
            expires_at = payload.get("expires_at")
            if not token or not expires_at:
                raise RuntimeError("GitHub App returned an incomplete installation token")
            parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if parsed_expiry.tzinfo is None:
                raise RuntimeError("GitHub App returned an invalid installation-token expiry")
            self._token = token
            self._expires_at = parsed_expiry.astimezone(timezone.utc)
            return token


class GitHubOracleGateway:
    def __init__(
        self,
        token: str = "",
        api_base: str = "https://api.github.com",
        *,
        token_provider: GitHubTokenProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        if bool(token) == bool(token_provider):
            raise ValueError("Configure exactly one GitHub authentication method")
        self._token = token
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=api_base.rstrip("/"),
            timeout=30,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "director-orchestrator-oracle-merge-gate",
            },
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        api_base: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        auth_transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> GitHubOracleGateway:
        app_values = (
            settings.oracle_automerge_github_app_id,
            settings.oracle_automerge_github_installation_id,
            settings.oracle_automerge_github_app_private_key_b64,
        )
        if any(app_values):
            if not all(app_values) or settings.oracle_automerge_github_token:
                raise ValueError("GitHub App authentication is incomplete or ambiguous")
            try:
                private_key = base64.b64decode(
                    settings.oracle_automerge_github_app_private_key_b64,
                    validate=True,
                )
            except (binascii.Error, ValueError) as exc:
                raise ValueError("GitHub App private key is not valid base64") from exc
            provider = GitHubAppInstallationTokenProvider(
                settings.oracle_automerge_github_app_id,
                settings.oracle_automerge_github_installation_id,
                private_key,
                api_base=api_base,
                transport=auth_transport,
                now=now,
            )
            return cls(
                api_base=api_base,
                token_provider=provider,
                transport=transport,
            )
        if settings.oracle_automerge_github_token:
            return cls(
                settings.oracle_automerge_github_token,
                api_base,
                transport=transport,
            )
        raise ValueError("GitHub credential is not configured")

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        supplied_headers = dict(kwargs.pop("headers", {}))
        for attempt in range(2):
            token = (
                self._token_provider.get_token()
                if self._token_provider is not None
                else self._token
            )
            request_headers = {**supplied_headers, "Authorization": f"Bearer {token}"}
            response = self._client.request(
                method, path, headers=request_headers, **kwargs
            )
            if (
                response.status_code == 401
                and self._token_provider is not None
                and attempt == 0
            ):
                response.close()
                self._token_provider.invalidate()
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"GitHub API {method} {path} failed with status {response.status_code}"
                )
            return response
        raise RuntimeError(f"GitHub API {method} {path} authentication failed")

    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot:
        repository = canonical_repository(repository)
        owner, name = repository.split("/", 1)
        pull = self._request("GET", f"/repos/{repository}/pulls/{pull_number}").json()
        files = self._request(
            "GET", f"/repos/{repository}/pulls/{pull_number}/files", params={"per_page": 100}
        ).json()
        if len(files) != int(pull.get("changed_files", len(files))):
            raise RuntimeError("GitHub file enumeration is incomplete; merge failed closed")
        checks_payload = self._request(
            "GET",
            f"/repos/{repository}/commits/{pull['head']['sha']}/check-runs",
            params={"per_page": 100, "filter": "latest"},
        ).json()
        if checks_payload.get("total_count", 0) != len(checks_payload.get("check_runs", [])):
            raise RuntimeError("GitHub check-run enumeration is incomplete; merge failed closed")

        query = """
        query($owner:String!, $name:String!, $number:Int!) {
          repository(owner:$owner, name:$name) {
            pullRequest(number:$number) {
              reviewDecision
              reviewThreads(first:100) {
                nodes { isResolved isOutdated }
                pageInfo { hasNextPage }
              }
            }
          }
        }
        """
        graph = self._request(
            "POST",
            "/graphql",
            json={"query": query, "variables": {"owner": owner, "name": name, "number": pull_number}},
        ).json()
        threads = graph["data"]["repository"]["pullRequest"]["reviewThreads"]
        review_decision = graph["data"]["repository"]["pullRequest"].get("reviewDecision")
        blocking = sum(
            1 for node in threads["nodes"] if not node["isResolved"] and not node["isOutdated"]
        )

        return OraclePullRequestSnapshot(
            repository=repository,
            pull_number=pull_number,
            title=pull.get("title") or "",
            body=pull.get("body") or "",
            state=pull["state"],
            draft=bool(pull["draft"]),
            mergeable=pull.get("mergeable"),
            merged=bool(pull.get("merged")),
            base_ref=pull["base"]["ref"],
            base_sha=pull["base"]["sha"],
            head_ref=pull["head"]["ref"],
            head_sha=pull["head"]["sha"],
            head_repository=pull["head"]["repo"]["full_name"],
            author_login=pull["user"]["login"],
            changed_files=[
                OracleChangedFile(
                    filename=item["filename"],
                    previous_filename=item.get("previous_filename"),
                    status=item["status"],
                    additions=item.get("additions", 0),
                    deletions=item.get("deletions", 0),
                    patch=item.get("patch"),
                )
                for item in files
            ],
            checks=[
                OracleCheckRun(
                    run_id=item["id"],
                    suite_id=(item.get("check_suite") or {}).get("id"),
                    name=item["name"],
                    status=item["status"],
                    conclusion=item.get("conclusion"),
                    head_sha=item["head_sha"],
                    app_slug=(item.get("app") or {}).get("slug") or "unknown",
                )
                for item in checks_payload.get("check_runs", [])
            ],
            blocking_review_threads=blocking,
            review_threads_complete=not threads["pageInfo"]["hasNextPage"],
            review_decision=review_decision,
        )

    def merge(
        self,
        repository: str,
        pull_number: int,
        expected_sha: str,
        expected_base_sha: str,
    ) -> OracleGitHubMerge:
        repository = canonical_repository(repository)
        pull = self._request("GET", f"/repos/{repository}/pulls/{pull_number}").json()
        if pull["head"]["sha"] != expected_sha or pull["base"]["sha"] != expected_base_sha:
            raise RuntimeError("GitHub pull request changed during final merge validation")
        actor = (
            self._token_provider.actor
            if self._token_provider is not None
            else self._request("GET", "/user").json().get("login")
            or "unknown-github-actor"
        )
        payload = self._request(
            "PUT",
            f"/repos/{repository}/pulls/{pull_number}/merge",
            json={"sha": expected_sha, "merge_method": "merge"},
        ).json()
        return OracleGitHubMerge(
            merged=bool(payload.get("merged")),
            merge_commit_sha=payload.get("sha"),
            actor=actor,
        )

    def mark_ready(
        self,
        repository: str,
        pull_number: int,
        expected_sha: str,
        expected_base_sha: str,
    ) -> bool:
        repository = canonical_repository(repository)
        pull = self._request("GET", f"/repos/{repository}/pulls/{pull_number}").json()
        if pull["head"]["sha"] != expected_sha or pull["base"]["sha"] != expected_base_sha:
            raise RuntimeError("GitHub pull request changed before ready-for-review transition")
        if not pull.get("draft"):
            return True
        query = """
        mutation($pullRequestId:ID!) {
          markPullRequestReadyForReview(input:{pullRequestId:$pullRequestId}) {
            pullRequest { isDraft }
          }
        }
        """
        payload = self._request(
            "POST",
            "/graphql",
            json={"query": query, "variables": {"pullRequestId": pull["node_id"]}},
        ).json()
        if payload.get("errors"):
            raise RuntimeError("GitHub refused the ready-for-review transition")
        pull_request = (
            payload.get("data", {})
            .get("markPullRequestReadyForReview", {})
            .get("pullRequest", {})
        )
        return pull_request.get("isDraft") is False
