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
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import Settings
from .models import ClaudeCallback, ProviderName, ProviderRun, TaskRecord
from .oracle_diff import (
    GITHUB_DIFF_MEDIA_TYPE,
    MAX_COMPARE_DIFF_BYTES,
    MAX_FILE_PATCH_CHARS,
    OracleDiffError,
    bind_complete_diff_evidence,
    parse_compare_diff,
    parse_hunk_patch,
    require_github_diff_content_type,
    require_identity_content_encoding,
)
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
    patch: str | None = Field(default=None, max_length=MAX_FILE_PATCH_CHARS)
    added_lines: list[str] | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def bind_structured_added_lines(self) -> OracleChangedFile:
        if self.patch is None:
            return self
        try:
            parsed = parse_hunk_patch(self.patch)
        except OracleDiffError as exc:
            raise ValueError(str(exc)) from exc
        if parsed.additions != self.additions or parsed.deletions != self.deletions:
            raise ValueError(
                "changed-file patch totals do not match REST addition and deletion counts"
            )
        if self.added_lines is None:
            self.added_lines = list(parsed.added_lines)
            return self
        if tuple(self.added_lines) != parsed.added_lines:
            raise ValueError("changed-file added_lines do not match structured hunk evidence")
        return self


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
    UNRESOLVED_MERGEABILITY_REASON = (
        "pull request is conflicting or mergeability is unresolved"
    )
    DRAFT_NOT_MERGEABLE_REASON = "draft pull requests cannot be merged automatically"
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
            if changed.added_lines is None:
                continue
            additions.extend(changed.added_lines)
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
            errors.append(self.DRAFT_NOT_MERGEABLE_REASON)
        if snapshot.mergeable is not True:
            errors.append(self.UNRESOLVED_MERGEABILITY_REASON)
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
    _EVALUATING = "EVALUATING"
    _RECOVERABLE = "RECOVERABLE"

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
                if existing["status"] != self._RECOVERABLE:
                    raise RuntimeError("the review artifact is already being evaluated")
                # A released attempt failed closed on a transient condition without
                # reaching a terminal result, so the identical artifact may retry.
                # Compare-and-swap on RECOVERABLE so separate service or CLI
                # processes cannot both re-arm the same one-shot authority after
                # observing the same released reservation.
                rearmed = connection.execute(
                    "UPDATE oracle_merge_attempts SET status = ?, updated_at = ? "
                    "WHERE artifact_id = ? AND status = ? AND result_json IS NULL",
                    (
                        self._EVALUATING,
                        now,
                        artifact.artifact_id,
                        self._RECOVERABLE,
                    ),
                ).rowcount
                if rearmed != 1:
                    raced = connection.execute(
                        "SELECT result_json FROM oracle_merge_attempts "
                        "WHERE artifact_id = ?",
                        (artifact.artifact_id,),
                    ).fetchone()
                    if raced and raced["result_json"]:
                        return OracleMergeResult.model_validate_json(
                            raced["result_json"]
                        )
                    raise RuntimeError("the review artifact is already being evaluated")
                self._append(
                    connection,
                    artifact.artifact_id,
                    "review_artifact_retried",
                    self._reservation_payload(artifact, digest),
                )
                return None
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
                        self._EVALUATING,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("assignment or review run was already consumed") from exc
            self._append(
                connection,
                artifact.artifact_id,
                "review_artifact_reserved",
                self._reservation_payload(artifact, digest),
            )
        return None

    @staticmethod
    def _reservation_payload(artifact: OracleReviewArtifact, digest: str) -> dict:
        return {
            "artifact_digest": digest,
            "assignment_id": artifact.assignment_id,
            "review_run_id": artifact.review_run_id,
            "repository": canonical_repository(artifact.repository),
            "pull_number": artifact.pull_number,
            "head_sha": artifact.head_sha,
        }

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

    def release(self, result: OracleMergeResult) -> OracleMergeResult:
        """Record a fail-closed outcome that the same artifact may retry.

        The attempt keeps its artifact, assignment, and review-run reservation, so no
        other review can claim them, but it stores no terminal result: the identical
        artifact can be delivered again once the transient condition clears. Only an
        attempt that has not reached a terminal result can be released.
        """
        with self._lock, self._connect() as connection:
            released = connection.execute(
                "UPDATE oracle_merge_attempts SET status = ?, updated_at = ? "
                "WHERE artifact_id = ? AND result_json IS NULL",
                (
                    self._RECOVERABLE,
                    datetime.now(timezone.utc).isoformat(),
                    result.artifact_id,
                ),
            ).rowcount
            if released != 1:
                raise RuntimeError(
                    "only an unresolved merge attempt can be released for retry"
                )
            self._append(
                connection,
                result.artifact_id,
                "merge_gate_recoverable",
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
    """Deterministic exact-SHA merge gate.

    GitHub recomputes mergeability asynchronously and reports `mergeable: null`
    meanwhile, most visibly right after a draft-to-ready transition. An unresolved
    value is transient rather than a verdict, so it is re-read with bounded backoff
    instead of consuming the one-shot review artifact. A transient read failure during
    that wait is a separate transient condition with its own bounded allowance, so
    neither condition can exhaust the other's allowance.
    """

    def __init__(
        self,
        policy: OracleMergePolicy,
        store: OracleMergeStore,
        github: OracleGitHubGateway | None,
        *,
        mergeable_poll_attempts: int = 5,
        mergeable_poll_read_failures: int = 2,
        mergeable_poll_stale_draft_reads: int = 3,
        mergeable_poll_base_delay_seconds: float = 1.0,
        mergeable_poll_max_delay_seconds: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if mergeable_poll_attempts < 1:
            raise ValueError("mergeability polling requires at least one attempt")
        if mergeable_poll_read_failures < 1:
            raise ValueError("mergeability polling requires at least one read attempt")
        if mergeable_poll_stale_draft_reads < 1:
            raise ValueError("mergeability polling requires at least one draft read")
        if (
            mergeable_poll_base_delay_seconds <= 0
            or mergeable_poll_max_delay_seconds < mergeable_poll_base_delay_seconds
        ):
            raise ValueError("mergeability backoff bounds are invalid")
        self.policy = policy
        self.store = store
        self.github = github
        self._poll_attempts = mergeable_poll_attempts
        self._poll_read_failures = mergeable_poll_read_failures
        self._poll_stale_draft_reads = mergeable_poll_stale_draft_reads
        self._poll_base_delay = mergeable_poll_base_delay_seconds
        self._poll_max_delay = mergeable_poll_max_delay_seconds
        self._sleep = sleep

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

        snapshot, unreadable = self._initial_snapshot(artifact)
        if unreadable is not None:
            return unreadable
        transitioned_from_draft = snapshot.draft
        if snapshot.draft:
            preflight = self.policy.evaluate(
                artifact, snapshot, allow_draft_transition=True
            )
            # Unresolved mergeability is the ordinary state of a draft pull request,
            # and it is revalidated in full under the bounded wait after the ready
            # transition. Letting it veto the transition would record a terminal
            # rejection before the wait ever began, which is the exact consumption this
            # gate exists to avoid. Every other reason still blocks, and the guard is
            # taken from the snapshot so an explicit conflict is never forgiven.
            blocking = [
                reason
                for reason in preflight.reasons
                if not (
                    snapshot.mergeable is None
                    and reason == self.policy.UNRESOLVED_MERGEABILITY_REASON
                )
            ]
            if blocking or preflight.serious_safety_reasons:
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
            except Exception as exc:
                preflight.status = OracleMergeStatus.ERROR
                preflight.reasons = [
                    f"GitHub ready-for-review transition failed closed: {type(exc).__name__}"
                ]
                return self.store.complete(preflight)
        if snapshot.draft or snapshot.mergeable is None:
            # The stale draft snapshot must be refetched after the ready transition,
            # and GitHub may report mergeability as unresolved while it recalculates.
            resolved, unresolved = self._resolved_snapshot(
                artifact, transitioned_from_draft=transitioned_from_draft
            )
            if unresolved is not None:
                return unresolved
            snapshot = resolved
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

    def _initial_snapshot(
        self, artifact: OracleReviewArtifact
    ) -> tuple[OraclePullRequestSnapshot | None, OracleMergeResult | None]:
        """Take the first read, tolerating the same transient failures as the poll.

        A read that never produced a snapshot proves nothing about the pull request, so
        the first read gets the same bounded read-failure allowance as every later one.
        Recording a terminal verdict here would spend a correctly reviewed artifact on a
        network blip; exhausting the allowance releases it for an authenticated retry
        instead. Nothing is merged from here, so nothing ambiguous is retried.
        """
        read_failure_allowance = self._poll_read_failures
        last_read_failure: Exception | None = None
        delay = self._poll_base_delay
        reads = 0
        while read_failure_allowance:
            if reads:
                self._sleep(delay)
                delay = min(delay * 2, self._poll_max_delay)
            reads += 1
            try:
                return (
                    self.github.snapshot(artifact.repository, artifact.pull_number),
                    None,
                )
            except OracleDiffError as exc:
                return None, self._complete_evidence_failure(artifact, exc)
            except Exception as exc:
                read_failure_allowance -= 1
                last_read_failure = exc
        return None, self._release_for_retry(
            artifact,
            f"GitHub validation read failed closed after {self._poll_read_failures} "
            "transient read failures and remains retryable: "
            f"{type(last_read_failure).__name__}",
        )

    def _resolved_snapshot(
        self,
        artifact: OracleReviewArtifact,
        *,
        transitioned_from_draft: bool = False,
    ) -> tuple[OraclePullRequestSnapshot | None, OracleMergeResult | None]:
        """Re-read the pull request until GitHub converges on the state it confirmed.

        Three transient conditions are possible here, and each carries its own strictly
        bounded allowance so that none can exhaust another's:

        * an unresolved ``mergeable: null`` (``mergeable_poll_attempts``);
        * a read that never produced a snapshot (``mergeable_poll_read_failures``);
        * a stale ``draft: true`` after ``mark_ready`` already confirmed ``isDraft:
          false`` through GraphQL, which a lagging REST replica can still report
          (``mergeable_poll_stale_draft_reads``).

        Each iteration spends the allowance for every transient condition it exhibits;
        overlapping conditions therefore shorten the wait. The loop performs at most
        ``attempts + read_failures + stale_draft_reads - 2`` reads — eight by default,
        over about thirty-nine seconds of backoff.

        Every successful snapshot is revalidated in full before the wait continues, so
        any repository, pull-request, state, base-SHA, head-SHA, check, review-thread,
        path, or safety drift is terminal. A condition is treated as transient only when
        the snapshot actually exhibits it, so an explicit ``mergeable: false`` conflict
        is terminal on the read that reports it, and a draft flag is only ever forgiven
        on a pull request this gate just transitioned. Exhausting any allowance fails
        closed without consuming the review artifact, so the identical artifact can be
        delivered again. No merge call is made from here.
        """
        unresolved_allowance = self._poll_attempts
        read_failure_allowance = self._poll_read_failures
        # Without a transition of our own there is nothing to be stale about, so the
        # allowance stays pinned at one and is never spent: a draft flag then falls
        # through to `blocking` as genuine drift.
        stale_draft_allowance = (
            self._poll_stale_draft_reads if transitioned_from_draft else 1
        )
        last_read_failure: Exception | None = None
        delay = self._poll_base_delay
        reads = 0
        while unresolved_allowance and read_failure_allowance and stale_draft_allowance:
            if reads:
                self._sleep(delay)
                delay = min(delay * 2, self._poll_max_delay)
            reads += 1
            try:
                snapshot = self.github.snapshot(
                    artifact.repository, artifact.pull_number
                )
            except OracleDiffError as exc:
                return None, self._complete_evidence_failure(artifact, exc)
            except Exception as exc:
                # A read that never produced a snapshot proves nothing about
                # mergeability, so it spends only the read-failure allowance.
                read_failure_allowance -= 1
                last_read_failure = exc
                continue
            # Classify from the snapshot itself rather than from the reason text alone,
            # so a `false` conflict can never be mistaken for an unresolved one.
            unresolved = snapshot.mergeable is None
            stale_draft = transitioned_from_draft and snapshot.draft
            transient = set()
            if unresolved:
                transient.add(self.policy.UNRESOLVED_MERGEABILITY_REASON)
            if stale_draft:
                transient.add(self.policy.DRAFT_NOT_MERGEABLE_REASON)
            interim = self.policy.evaluate(artifact, snapshot)
            blocking = [
                reason for reason in interim.reasons if reason not in transient
            ]
            if blocking or interim.serious_safety_reasons:
                return None, self.store.complete(interim)
            if not transient:
                return snapshot, None
            if unresolved:
                unresolved_allowance -= 1
            if stale_draft:
                stale_draft_allowance -= 1
        if last_read_failure is not None and not read_failure_allowance:
            return None, self._release_for_retry(
                artifact,
                "GitHub mergeability re-read failed closed after "
                f"{self._poll_read_failures} transient read failures and remains "
                f"retryable: {type(last_read_failure).__name__}",
            )
        if not stale_draft_allowance:
            return None, self._release_for_retry(
                artifact,
                "GitHub still reported the pull request as draft after "
                f"{self._poll_stale_draft_reads} bounded re-reads following a confirmed "
                "ready-for-review transition and remains retryable",
            )
        return None, self._release_for_retry(
            artifact,
            f"GitHub mergeability was unresolved after {self._poll_attempts} bounded "
            "re-reads and remains retryable",
        )

    def _complete_evidence_failure(
        self, artifact: OracleReviewArtifact, exc: OracleDiffError
    ) -> OracleMergeResult:
        return self.store.complete(
            OracleMergeResult(
                artifact_id=artifact.artifact_id,
                artifact_digest=artifact.digest(),
                status=OracleMergeStatus.ERROR,
                repository=self.policy.repository,
                pull_number=artifact.pull_number,
                reviewed_sha=artifact.head_sha,
                reasons=[f"complete compare-diff evidence failed closed: {exc}"],
            )
        )

    def _release_for_retry(
        self, artifact: OracleReviewArtifact, reason: str
    ) -> OracleMergeResult:
        return self.store.release(
            OracleMergeResult(
                artifact_id=artifact.artifact_id,
                artifact_digest=artifact.digest(),
                status=OracleMergeStatus.ERROR,
                repository=self.policy.repository,
                pull_number=artifact.pull_number,
                reviewed_sha=artifact.head_sha,
                reasons=[reason],
            )
        )


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
        max_compare_diff_bytes: int = MAX_COMPARE_DIFF_BYTES,
    ):
        if bool(token) == bool(token_provider):
            raise ValueError("Configure exactly one GitHub authentication method")
        self._token = token
        self._token_provider = token_provider
        self._max_compare_diff_bytes = max_compare_diff_bytes
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

    def _get_pull(self, repository: str, pull_number: int) -> dict:
        return self._request("GET", f"/repos/{repository}/pulls/{pull_number}").json()

    def _get_files(self, repository: str, pull_number: int) -> list[dict]:
        payload = self._request(
            "GET",
            f"/repos/{repository}/pulls/{pull_number}/files",
            params={"per_page": 100},
        ).json()
        if not isinstance(payload, list):
            raise OracleDiffError("GitHub file enumeration is incomplete")
        names = [item.get("filename") for item in payload]
        if len(set(names)) != len(names):
            raise OracleDiffError("GitHub file enumeration is incomplete")
        return payload

    @staticmethod
    def _rest_patch_incomplete(item: dict) -> bool:
        patch = item.get("patch")
        return patch is None or patch == ""

    @staticmethod
    def _changed_files_from_rest(files: list[dict]) -> list[OracleChangedFile]:
        try:
            return [
                OracleChangedFile(
                    filename=item["filename"],
                    previous_filename=item.get("previous_filename"),
                    status=item["status"],
                    additions=item.get("additions", 0),
                    deletions=item.get("deletions", 0),
                    patch=item.get("patch"),
                )
                for item in files
            ]
        except ValidationError as exc:
            raise OracleDiffError(
                "REST file patch is incomplete or disagrees with REST counts"
            ) from exc

    @staticmethod
    def _require_complete_file_enumeration(pull: dict, files: list[dict]) -> None:
        if len(files) != int(pull.get("changed_files", len(files))):
            raise OracleDiffError("GitHub file enumeration is incomplete")

    @staticmethod
    def _require_sha40(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise OracleDiffError("GitHub pull request SHA is not a pinned 40-hex digest")
        return value

    def _pull_identity(self, repository: str, pull_number: int, pull: dict) -> dict[str, object]:
        return {
            "repository": canonical_repository(repository),
            "pull_number": pull_number,
            "title": pull.get("title") or "",
            "body": pull.get("body") or "",
            "draft": bool(pull["draft"]),
            "state": pull["state"],
            "merged": bool(pull.get("merged")),
            "base_ref": pull["base"]["ref"],
            "base_sha": self._require_sha40(pull["base"]["sha"]),
            "head_ref": pull["head"]["ref"],
            "head_sha": self._require_sha40(pull["head"]["sha"]),
            "head_repository": canonical_repository(pull["head"]["repo"]["full_name"]),
            "author": pull["user"]["login"],
            "changed_files": int(pull["changed_files"]),
        }

    @staticmethod
    def _file_set(files: list[dict]) -> tuple[tuple[object, ...], ...]:
        return tuple(
            sorted(
                (
                    item["filename"],
                    item.get("previous_filename"),
                    item["status"],
                    int(item.get("additions", 0)),
                    int(item.get("deletions", 0)),
                )
                for item in files
            )
        )

    def _fetch_compare_diff(self, repository: str, base_sha: str, head_sha: str) -> str:
        base_sha = self._require_sha40(base_sha)
        head_sha = self._require_sha40(head_sha)
        path = f"/repos/{repository}/compare/{base_sha}...{head_sha}"
        body = self._request_bounded_bytes(
            "GET",
            path,
            max_bytes=self._max_compare_diff_bytes,
            headers={
                "Accept": GITHUB_DIFF_MEDIA_TYPE,
                "Accept-Encoding": "identity",
            },
        )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OracleDiffError("complete compare diff is malformed") from exc
        if text.startswith("{") or text.startswith("["):
            raise OracleDiffError("complete compare diff is malformed")
        return text

    def _request_bounded_bytes(
        self,
        method: str,
        path: str,
        *,
        max_bytes: int,
        headers: dict[str, str],
    ) -> bytes:
        supplied_headers = dict(headers)
        for attempt in range(2):
            token = (
                self._token_provider.get_token()
                if self._token_provider is not None
                else self._token
            )
            request_headers = {
                **supplied_headers,
                "Authorization": f"Bearer {token}",
                "Accept-Encoding": "identity",
            }
            with self._client.stream(method, path, headers=request_headers) as response:
                if (
                    response.status_code == 401
                    and self._token_provider is not None
                    and attempt == 0
                ):
                    self._token_provider.invalidate()
                    continue
                if response.status_code == 406:
                    raise OracleDiffError(
                        "complete compare diff exceeds the bounded evidence limit"
                    )
                if self._is_transient_compare_status(response):
                    raise RuntimeError(
                        f"GitHub API {method} {path} failed with status {response.status_code}"
                    )
                if response.status_code >= 400:
                    raise OracleDiffError("complete compare diff is unavailable")
                require_github_diff_content_type(response.headers.get("content-type"))
                require_identity_content_encoding(response.headers.get("content-encoding"))
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        length = int(declared)
                    except ValueError as exc:
                        raise OracleDiffError("complete compare diff is truncated") from exc
                    if length > max_bytes:
                        raise OracleDiffError(
                            "complete compare diff exceeds the bounded evidence limit"
                        )
                collected = bytearray()
                for chunk in response.iter_bytes():
                    collected.extend(chunk)
                    if len(collected) > max_bytes:
                        raise OracleDiffError(
                            "complete compare diff exceeds the bounded evidence limit"
                        )
                if declared is not None and int(declared) != len(collected):
                    raise OracleDiffError("complete compare diff is truncated")
                return bytes(collected)
        raise RuntimeError(f"GitHub API {method} {path} authentication failed")

    @staticmethod
    def _is_transient_compare_status(response: httpx.Response) -> bool:
        if response.status_code >= 500 or response.status_code in {408, 429}:
            return True
        if response.status_code != 403:
            return False
        if (response.headers.get("retry-after") or "").strip():
            return True
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is None:
            return False
        try:
            return int(remaining.strip()) == 0
        except ValueError:
            return False

    def _complete_missing_patches(
        self,
        repository: str,
        pull_number: int,
        pull: dict,
        files: list[dict],
    ) -> tuple[dict, list[OracleChangedFile]]:
        before = self._pull_identity(repository, pull_number, pull)
        before_files = self._file_set(files)
        diff_text = self._fetch_compare_diff(
            repository, str(before["base_sha"]), str(before["head_sha"])
        )
        after_pull = self._get_pull(repository, pull_number)
        after_files = self._get_files(repository, pull_number)
        self._require_complete_file_enumeration(after_pull, after_files)
        after = self._pull_identity(repository, pull_number, after_pull)
        if before != after or before_files != self._file_set(after_files):
            raise OracleDiffError("pull request identity changed during complete-diff retrieval")
        bound = bind_complete_diff_evidence(
            after_files,
            parse_compare_diff(diff_text, max_bytes=self._max_compare_diff_bytes),
        )
        return after_pull, [
            OracleChangedFile(
                filename=item.filename,
                previous_filename=item.previous_filename,
                status=item.status,
                additions=item.additions,
                deletions=item.deletions,
                patch=item.patch,
                added_lines=list(item.added_lines),
            )
            for item in bound
        ]

    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot:
        repository = canonical_repository(repository)
        owner, name = repository.split("/", 1)
        pull = self._get_pull(repository, pull_number)
        self._require_sha40(str(pull["base"]["sha"]))
        self._require_sha40(str(pull["head"]["sha"]))
        files = self._get_files(repository, pull_number)
        self._require_complete_file_enumeration(pull, files)
        if any(self._rest_patch_incomplete(item) for item in files):
            pull, changed_files = self._complete_missing_patches(
                repository, pull_number, pull, files
            )
        else:
            changed_files = self._changed_files_from_rest(files)
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
            changed_files=changed_files,
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
