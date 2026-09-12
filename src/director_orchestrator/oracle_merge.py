# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:42 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 06:33 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:30 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | BEGIN]]
from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol
from urllib.parse import quote

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .allocation_profiles import canonical_allocation_lane
from .config import Settings
from .models import Assignment, ClaudeCallback, ProviderName, ProviderRun, TaskRecord
from .interactions import (
    CommunicationEvidenceFailure,
    ProviderTransport,
    require_verified_result,
)
from .oracle_diff import (
    GITHUB_DIFF_MEDIA_TYPE,
    MAX_COMPARE_DIFF_BYTES,
    MAX_FILE_PATCH_CHARS,
    OracleDiffError,
    OracleRateLimitError,
    bind_complete_diff_evidence,
    parse_compare_diff,
    parse_hunk_patch,
    require_github_diff_content_type,
    require_identity_content_encoding,
    require_rest_bool,
    require_rest_mapping,
    require_rest_nonnegative_int,
    require_rest_optional_bool,
    require_rest_optional_text,
    require_rest_positive_int,
    require_rest_sha40,
    require_rest_text,
)
from .policy import canonical_repository
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
from .state_witness import (
    ClaimKind,
    StateWitnessLedger,
    WitnessLedgerFull,
    WitnessRow,
    snapshot_not_merged,
)
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]


def _is_shared_trunk_ref(ref: str) -> bool:
    """True for the default trunk names this project treats as protected."""

    return ref.strip().lower().removeprefix("refs/heads/") in {"main", "master"}


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


class OracleMergeInProgress(RuntimeError):
    """A different live worker owns this exact merge evaluation."""


class OracleMergeClaimExpired(RuntimeError):
    """The prior merge owner is no longer provably live; reconciliation is required."""


@dataclass(frozen=True)
class OracleMergeClaim:
    token: str
    lease_until: datetime


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
    record: TaskRecord,
    run: ProviderRun,
    secret: str,
    *,
    review_assignment: Assignment | None = None,
) -> OracleReviewArtifact | None:
    """Bind one eligible provider result to its stored exact-SHA review assignment."""
    if run.provider not in {
        ProviderName.CLAUDE,
        ProviderName.CURSOR,
        ProviderName.GROK,
    }:
        return None
    successful_statuses = {
        ProviderName.CLAUDE: {"completed"},
        ProviderName.CURSOR: {"FINISHED", "completed"},
        ProviderName.GROK: {"completed"},
    }
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
    if run.evidence_source != "provider_authenticated":
        raise ValueError("review completion lacks authenticated provider evidence")
    if run.status not in successful_statuses[run.provider]:
        raise ValueError("only a successful provider run can authenticate a review")
    if "signature" in payload:
        raise ValueError("provider review output must not supply an authentication signature")
    payload["signature"] = "0" * 64
    artifact = OracleReviewArtifact.model_validate(payload)
    if run.provider == ProviderName.GROK:
        raw = run.raw if isinstance(run.raw, dict) else {}
        if raw.get("transport") != "grok_cli" or raw.get("mode") != "exact_sha_review":
            raise ValueError(
                "Grok review authority requires the repository-capable CLI transport"
            )
        if not raw.get("read_only"):
            raise ValueError("Grok exact-SHA review did not run in read-only mode")
        if raw.get("repository") != canonical_repository(artifact.repository):
            raise ValueError("Grok CLI review repository evidence does not match")
        if raw.get("base_sha") != artifact.base_sha or raw.get("head_sha") != artifact.head_sha:
            raise ValueError("Grok CLI review SHA evidence does not match the artifact")

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
    if stored_run.evidence_source != "provider_authenticated":
        raise ValueError("provider review result lacks authenticated provider evidence")
    if stored_run.status not in successful_statuses[run.provider]:
        raise ValueError("stored provider review run is not successful")
    if run.provider == ProviderName.GROK and stored_run.raw != run.raw:
        raise ValueError("stored Grok CLI evidence does not match the review run")
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
    planned_assignment = assignments[0]
    assignment = review_assignment or planned_assignment
    dynamic_fields = {"task", "acceptance", "repository", "starting_ref"}
    if (
        assignment.model_dump(mode="json", exclude=dynamic_fields)
        != planned_assignment.model_dump(mode="json", exclude=dynamic_fields)
    ):
        raise ValueError("review handoff changed planned reviewer authority or budget")
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
    try:
        handoff = json.loads(assignment.task)
    except json.JSONDecodeError:
        handoff = None
    if isinstance(handoff, dict) and handoff.get("schema_version") == (
        "director-review-handoff/v1"
    ):
        expected_digest = handoff.get("artifact_digest")
        if not isinstance(expected_digest, str) or not hmac.compare_digest(
            record.review_handoff_digests.get(sequence, ""), expected_digest
        ):
            raise ValueError("stored review handoff digest is missing or changed")
        expected = {
            "artifact_id": handoff.get("required_review_artifact_id"),
            "assignment_id": handoff.get("assignment_id"),
            "repository": handoff.get("repository"),
            "pull_number": handoff.get("pull_number"),
            "base_ref": handoff.get("base_ref"),
            "base_sha": handoff.get("base_sha"),
            "head_ref": handoff.get("head_ref"),
            "head_sha": handoff.get("head_sha"),
            "implementation_provider": handoff.get("logical_producer"),
            "implementation_actor": handoff.get("implementation_actor"),
            "reviewer_provider": handoff.get("reviewer_provider"),
        }
        actual = {
            "artifact_id": artifact.artifact_id,
            "assignment_id": artifact.assignment_id,
            "repository": artifact.repository,
            "pull_number": artifact.pull_number,
            "base_ref": artifact.base_ref,
            "base_sha": artifact.base_sha,
            "head_ref": artifact.head_ref,
            "head_sha": artifact.head_sha,
            "implementation_provider": artifact.implementation_provider,
            "implementation_actor": artifact.implementation_actor,
            "reviewer_provider": artifact.reviewer_provider,
        }
        mismatched = sorted(
            key for key, value in expected.items() if actual[key] != value
        )
        if mismatched:
            raise ValueError(
                "review artifact does not match the stored handoff: "
                + ", ".join(mismatched)
            )
    review_context = "\n".join([assignment.task, *assignment.acceptance]).lower()
    if "review" not in review_context or artifact.head_sha not in review_context:
        raise ValueError("provider assignment was not pinned to the exact reviewed head SHA")
    if artifact.base_sha not in review_context:
        raise ValueError("provider assignment was not pinned to the exact reviewed base SHA")
    # Preserve specific artifact/assignment policy diagnostics above, then
    # require the immutable provider event that authenticates an otherwise
    # valid exact-SHA review candidate.
    if not run.interaction_id:
        raise ValueError("review completion lacks immutable interaction evidence")
    try:
        verified_event = require_verified_result(
            record.provider_interaction_ledger,
            interaction_id=run.interaction_id,
            result=run.result,
        )
    except CommunicationEvidenceFailure as exc:
        raise ValueError(str(exc)) from exc
    if (
        run.provider == ProviderName.GROK
        and verified_event.transport_evidence.transport
        != ProviderTransport.GROK_CLI
    ):
        raise ValueError(
            "Grok review completion is not bound to verified CLI transport evidence"
        )
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
    # This compatibility wrapper is called only after the dedicated callback
    # bearer boundary has authenticated the payload. Persisted callback handling
    # sets the same provenance marker in callbacks.apply_claude_callback.
    run.evidence_source = "provider_authenticated"
    return authenticate_provider_review_artifact(record, run, secret)


class OracleChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=2_000)
    previous_filename: str | None = Field(default=None, min_length=1, max_length=2_000)
    status: str = Field(min_length=1, max_length=64)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    patch: str | None = Field(default=None, max_length=MAX_FILE_PATCH_CHARS)
    added_lines: list[str] | None = Field(default=None, max_length=MAX_FILE_PATCH_CHARS)

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
    base_protected: bool | None = Field(default=None, strict=True)
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
    UNCONFIGURED_BASE_REF_REASON = (
        "ORACLE_AUTOMERGE_BASE_REF is not configured"
    )
    PROTECTED_BASE_REF_REASON = (
        "tier 0 forbids merging into a protected branch: ORACLE_AUTOMERGE_BASE_REF "
        "must name this project's own base ref, not a shared trunk"
    )
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
        # Actor bindings are keyed by canonical allocation lane so an alias such
        # as `gemini` and its lane name resolve to the same trusted actor set
        # instead of silently missing each other on either side of a comparison.
        actor_map: dict[str, frozenset[str]] = {}
        for lane, actors in settings.oracle_provider_actor_map().items():
            canonical = canonical_allocation_lane(lane)
            actor_map[canonical] = actor_map.get(canonical, frozenset()) | actors
        self.provider_actor_map = actor_map

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
        if snapshot.base_protected is not False:
            errors.append("base branch protection is protected or unverified")
        if not self.base_ref:
            errors.append(self.UNCONFIGURED_BASE_REF_REASON)
        elif _is_shared_trunk_ref(self.base_ref):
            errors.append(self.PROTECTED_BASE_REF_REASON)
        if artifact.verdict == OracleReviewVerdict.SERIOUS_SAFETY_STOP:
            serious.append("reviewer returned SERIOUS_SAFETY_STOP")
        elif artifact.verdict != OracleReviewVerdict.ACCEPT:
            errors.append("reviewer did not accept the reviewed commit")
        # Both sides of every provider-identity comparison below are folded to
        # the canonical allocation lane, so an alias can neither slip past the
        # reviewer allowlist nor split a lane in two and defeat self-review.
        reviewer_lane = canonical_allocation_lane(artifact.reviewer_provider.strip().lower())
        implementation_lane = canonical_allocation_lane(
            artifact.implementation_provider.strip().lower()
        )
        if reviewer_lane not in {"openai", "claude", "cursor", "grok", "antigravity"}:
            errors.append("the exact-SHA reviewer must be OpenAI, Claude, Cursor, Grok, or Antigravity")
        if reviewer_lane == implementation_lane:
            errors.append("the implementation author cannot accept its own work")
        producer_actors = self.provider_actor_map.get(implementation_lane, frozenset())
        reviewer_actors = self.provider_actor_map.get(reviewer_lane, frozenset())
        implementation_actor = artifact.implementation_actor.lower()
        if not producer_actors:
            errors.append("implementation provider has no trusted GitHub actor binding")
        elif implementation_actor not in producer_actors:
            errors.append("implementation actor is not bound to the implementation provider")
        if not reviewer_actors:
            errors.append("review provider has no trusted GitHub actor binding")
        elif implementation_actor in reviewer_actors:
            errors.append("implementation actor is also controlled by the review provider")
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
    _AMBIGUOUS = "AMBIGUOUS"

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            if immediate:
                connection.isolation_level = None
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            else:
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
            self._ensure_retry_not_before_column(connection)
            self._ensure_claim_columns(connection)

    @staticmethod
    def _ensure_retry_not_before_column(connection: sqlite3.Connection) -> None:
        """Add retry_not_before on live DBs whose CREATE TABLE already ran.

        CREATE TABLE IF NOT EXISTS cannot add columns to an existing
        oracle_merge_attempts table. PRAGMA + ALTER is therefore the live
        migration and must stay idempotent.
        """
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(oracle_merge_attempts)")
        }
        if "retry_not_before" not in columns:
            try:
                connection.execute(
                    "ALTER TABLE oracle_merge_attempts ADD COLUMN retry_not_before TEXT"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(oracle_merge_attempts)"
                    )
                }
                if "retry_not_before" not in columns:
                    raise

    @staticmethod
    def _ensure_claim_columns(connection: sqlite3.Connection) -> None:
        """Add nullable claim metadata without granting legacy rows live ownership."""

        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(oracle_merge_attempts)")
        }
        for name in ("claim_token", "claim_lease_until"):
            if name in columns:
                continue
            try:
                connection.execute(
                    f"ALTER TABLE oracle_merge_attempts ADD COLUMN {name} TEXT"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    def claim(
        self,
        artifact: OracleReviewArtifact,
        *,
        lease_seconds: int,
    ) -> OracleMergeClaim | OracleMergeResult:
        """Atomically claim one evaluation or classify its persisted owner.

        A fresh lease means another process may still perform the one-shot merge and
        is therefore an observer-only condition. An expired or legacy owner is
        durably converted to AMBIGUOUS; it can never be reclaimed for another merge.
        """

        if lease_seconds < 60:
            raise ValueError("Oracle merge claim lease must be at least 60 seconds")
        digest = artifact.digest()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_until = now_dt + timedelta(seconds=lease_seconds)
        token = secrets.token_hex(32)
        with self._lock, self._connect(immediate=True) as connection:
            existing = connection.execute(
                "SELECT artifact_digest, status, result_json, retry_not_before, "
                "claim_token, claim_lease_until FROM oracle_merge_attempts "
                "WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            if existing:
                if existing["artifact_digest"] != digest:
                    raise ValueError(
                        "review artifact identifier was reused with different content"
                    )
                if existing["result_json"]:
                    return OracleMergeResult.model_validate_json(existing["result_json"])
                if existing["status"] == self._EVALUATING:
                    deadline = _parse_stored_deadline(existing["claim_lease_until"])
                    if existing["claim_token"] and deadline and deadline > now_dt:
                        raise OracleMergeInProgress(
                            "the review artifact is already being evaluated by a live owner"
                        )
                    changed = connection.execute(
                        "UPDATE oracle_merge_attempts SET status = ?, claim_token = NULL, "
                        "claim_lease_until = NULL, updated_at = ? WHERE artifact_id = ? "
                        "AND status = ? AND result_json IS NULL",
                        (
                            self._AMBIGUOUS,
                            now,
                            artifact.artifact_id,
                            self._EVALUATING,
                        ),
                    ).rowcount
                    if changed == 1:
                        self._append(
                            connection,
                            artifact.artifact_id,
                            "merge_evaluation_claim_expired",
                            {"artifact_digest": digest},
                        )
                        # This classification must survive the exception used to stop
                        # the caller; otherwise the context manager would roll it back
                        # and every observer could repeat the transition.
                        connection.commit()
                    raise OracleMergeClaimExpired(
                        "the prior merge evaluation claim expired; exact-SHA reconciliation is required"
                    )
                if existing["status"] == self._AMBIGUOUS:
                    raise OracleMergeClaimExpired(
                        "the prior merge evaluation outcome is ambiguous; exact-SHA reconciliation is required"
                    )
                if existing["status"] != self._RECOVERABLE:
                    raise RuntimeError("the review artifact has an unresolved terminal state")
                if _is_unparseable_nonempty_deadline(existing["retry_not_before"]):
                    repaired = self._repair_unparseable_retry_deadline(
                        connection,
                        artifact,
                        digest,
                        existing["retry_not_before"],
                        now,
                    )
                    if repaired is not None:
                        return repaired
                    raise OracleMergeInProgress(
                        "the review artifact retry deadline changed concurrently"
                    )
                hold = self._retry_hold_result(
                    artifact, digest, existing["retry_not_before"]
                )
                if hold is not None:
                    return hold
                rearmed = connection.execute(
                    "UPDATE oracle_merge_attempts SET status = ?, updated_at = ?, "
                    "retry_not_before = NULL, claim_token = ?, claim_lease_until = ? "
                    "WHERE artifact_id = ? AND status = ? AND result_json IS NULL "
                    "AND (retry_not_before IS NULL OR retry_not_before <= ?)",
                    (
                        self._EVALUATING,
                        now,
                        token,
                        lease_until.isoformat(),
                        artifact.artifact_id,
                        self._RECOVERABLE,
                        now,
                    ),
                ).rowcount
                if rearmed != 1:
                    raise OracleMergeInProgress(
                        "the review artifact retry was claimed concurrently"
                    )
                self._append(
                    connection,
                    artifact.artifact_id,
                    "review_artifact_retried",
                    self._reservation_payload(artifact, digest),
                )
                return OracleMergeClaim(token=token, lease_until=lease_until)

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
                    "INSERT INTO oracle_merge_attempts("
                    "artifact_id, artifact_digest, assignment_id, review_run_id, "
                    "status, result_json, created_at, updated_at, retry_not_before, "
                    "claim_token, claim_lease_until) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?)",
                    (
                        artifact.artifact_id,
                        digest,
                        artifact.assignment_id,
                        artifact.review_run_id,
                        self._EVALUATING,
                        now,
                        now,
                        token,
                        lease_until.isoformat(),
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
        return OracleMergeClaim(token=token, lease_until=lease_until)

    def reserve(self, artifact: OracleReviewArtifact) -> OracleMergeResult | None:
        digest = artifact.digest()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT artifact_digest, status, result_json, retry_not_before "
                "FROM oracle_merge_attempts WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            if existing:
                if existing["artifact_digest"] != digest:
                    raise ValueError("review artifact identifier was reused with different content")
                if existing["result_json"]:
                    return OracleMergeResult.model_validate_json(existing["result_json"])
                if existing["status"] != self._RECOVERABLE:
                    raise RuntimeError("the review artifact is already being evaluated")
                if _is_unparseable_nonempty_deadline(existing["retry_not_before"]):
                    repaired = self._repair_unparseable_retry_deadline(
                        connection,
                        artifact,
                        digest,
                        existing["retry_not_before"],
                        now,
                    )
                    if repaired is not None:
                        return repaired
                    raise RuntimeError("the review artifact is already being evaluated")
                hold = self._retry_hold_result(
                    artifact, digest, existing["retry_not_before"]
                )
                if hold is not None:
                    return hold
                # A released attempt failed closed on a transient condition without
                # reaching a terminal result, so the identical artifact may retry.
                # Compare-and-swap on RECOVERABLE so separate service or CLI
                # processes cannot both re-arm the same one-shot authority after
                # observing the same released reservation. Clear the deadline only
                # when it is due; a still-future deadline must not rearm.
                rearmed = connection.execute(
                    "UPDATE oracle_merge_attempts SET status = ?, updated_at = ?, "
                    "retry_not_before = NULL "
                    "WHERE artifact_id = ? AND status = ? AND result_json IS NULL "
                    "AND (retry_not_before IS NULL OR retry_not_before <= ?)",
                    (
                        self._EVALUATING,
                        now,
                        artifact.artifact_id,
                        self._RECOVERABLE,
                        now,
                    ),
                ).rowcount
                if rearmed != 1:
                    raced = connection.execute(
                        "SELECT result_json, retry_not_before FROM oracle_merge_attempts "
                        "WHERE artifact_id = ?",
                        (artifact.artifact_id,),
                    ).fetchone()
                    if raced and raced["result_json"]:
                        return OracleMergeResult.model_validate_json(
                            raced["result_json"]
                        )
                    raced_hold = self._retry_hold_result(
                        artifact,
                        digest,
                        raced["retry_not_before"] if raced else None,
                    )
                    if raced_hold is not None:
                        return raced_hold
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
                    "INSERT INTO oracle_merge_attempts("
                    "artifact_id, artifact_digest, assignment_id, review_run_id, "
                    "status, result_json, created_at, updated_at, retry_not_before"
                    ") VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL)",
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

    def renew_claim(
        self, artifact_id: str, claim_token: str, *, lease_seconds: int
    ) -> datetime:
        """Renew only the still-live owner; stale tokens never regain authority."""

        if lease_seconds < 60:
            raise ValueError("Oracle merge claim lease must be at least 60 seconds")
        now_dt = datetime.now(timezone.utc)
        lease_until = now_dt + timedelta(seconds=lease_seconds)
        with self._lock, self._connect(immediate=True) as connection:
            renewed = connection.execute(
                "UPDATE oracle_merge_attempts SET claim_lease_until = ?, updated_at = ? "
                "WHERE artifact_id = ? AND status = ? AND result_json IS NULL "
                "AND claim_token = ? AND claim_lease_until > ?",
                (
                    lease_until.isoformat(),
                    now_dt.isoformat(),
                    artifact_id,
                    self._EVALUATING,
                    claim_token,
                    now_dt.isoformat(),
                ),
            ).rowcount
            if renewed != 1:
                raise OracleMergeClaimExpired(
                    "Oracle merge evaluation claim is no longer current"
                )
        return lease_until

    def complete(
        self,
        result: OracleMergeResult,
        *,
        claim_token: str | None = None,
    ) -> OracleMergeResult:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect(immediate=True) as connection:
            if claim_token is None:
                where = (
                    "artifact_id = ? AND status = ? AND result_json IS NULL "
                    "AND claim_token IS NULL"
                )
                parameters = (result.artifact_id, self._EVALUATING)
            else:
                where = (
                    "artifact_id = ? AND status = ? AND result_json IS NULL "
                    "AND claim_token = ? AND claim_lease_until > ?"
                )
                parameters = (
                    result.artifact_id,
                    self._EVALUATING,
                    claim_token,
                    now,
                )
            completed = connection.execute(
                "UPDATE oracle_merge_attempts SET status = ?, result_json = ?, "
                "updated_at = ?, claim_token = NULL, claim_lease_until = NULL WHERE "
                + where,
                (result.status.value, result.model_dump_json(), now, *parameters),
            ).rowcount
            if completed != 1:
                raise OracleMergeClaimExpired(
                    "Oracle merge result does not belong to the current live claim"
                )
            self._append(
                connection,
                result.artifact_id,
                "merge_gate_completed",
                result.model_dump(mode="json"),
            )
        return result

    def _retry_hold_result(
        self,
        artifact: OracleReviewArtifact,
        digest: str,
        retry_not_before: str | None,
    ) -> OracleMergeResult | None:
        deadline = _parse_stored_deadline(retry_not_before)
        if deadline is None or datetime.now(timezone.utc) >= deadline:
            return None
        return OracleMergeResult(
            artifact_id=artifact.artifact_id,
            artifact_digest=digest,
            status=OracleMergeStatus.ERROR,
            repository=canonical_repository(artifact.repository),
            pull_number=artifact.pull_number,
            reviewed_sha=artifact.head_sha,
            reasons=[
                "GitHub rate limit is deferred until the persisted "
                "not-before deadline and remains retryable: OracleRateLimitError"
            ],
        )

    def _repair_unparseable_retry_deadline(
        self,
        connection: sqlite3.Connection,
        artifact: OracleReviewArtifact,
        digest: str,
        stored_deadline: str,
        now: str,
    ) -> OracleMergeResult | None:
        """Replace an unparseable nonempty deadline without calling GitHub.

        A garbage TEXT value can sort after ISO-8601 `now`, fail Python parsing,
        and then fail the SQL due comparison so reserve() raises forever. Repair
        is compare-and-swap on the current malformed value, uses the same
        conservative 60-second fallback window as an unusable rate-limit header,
        and never rewrites a parseable deadline.
        """
        repair = _utc_deadline_text(
            datetime.now(timezone.utc)
            + timedelta(seconds=_RATE_LIMIT_FALLBACK_SECONDS)
        )
        repaired = connection.execute(
            "UPDATE oracle_merge_attempts SET retry_not_before = ?, updated_at = ? "
            "WHERE artifact_id = ? AND status = ? AND result_json IS NULL "
            "AND retry_not_before = ?",
            (
                repair,
                now,
                artifact.artifact_id,
                self._RECOVERABLE,
                stored_deadline,
            ),
        ).rowcount
        if repaired == 1:
            self._append(
                connection,
                artifact.artifact_id,
                "retry_not_before_repaired",
                {
                    "reason": (
                        "unparseable nonempty retry_not_before was replaced "
                        "with a conservative UTC deadline"
                    )
                },
            )
            return self._retry_hold_result(artifact, digest, repair)
        raced = connection.execute(
            "SELECT result_json, retry_not_before FROM oracle_merge_attempts "
            "WHERE artifact_id = ?",
            (artifact.artifact_id,),
        ).fetchone()
        if raced and raced["result_json"]:
            return OracleMergeResult.model_validate_json(raced["result_json"])
        return self._retry_hold_result(
            artifact,
            digest,
            raced["retry_not_before"] if raced else None,
        )

    def release(
        self,
        result: OracleMergeResult,
        *,
        retry_not_before: datetime | None = None,
        claim_token: str | None = None,
    ) -> OracleMergeResult:
        """Record a fail-closed outcome that the same artifact may retry.

        The attempt keeps its artifact, assignment, and review-run reservation, so no
        other review can claim them, but it stores no terminal result: the identical
        artifact can be delivered again once the transient condition clears. Only an
        attempt that has not reached a terminal result can be released. A stored
        not-before deadline is never moved backward, including when this release
        carries no new deadline.
        """
        with self._lock, self._connect(immediate=True) as connection:
            current = connection.execute(
                "SELECT retry_not_before FROM oracle_merge_attempts "
                "WHERE artifact_id = ? AND result_json IS NULL",
                (result.artifact_id,),
            ).fetchone()
            if current is None:
                raise RuntimeError(
                    "only an unresolved merge attempt can be released for retry"
                )
            stored = _later_deadline_text(current["retry_not_before"], retry_not_before)
            now = datetime.now(timezone.utc).isoformat()
            if claim_token is None:
                owner_clause = "status IN (?, ?) AND claim_token IS NULL"
                owner_parameters: tuple[str, ...] = (
                    self._EVALUATING,
                    self._RECOVERABLE,
                )
            else:
                owner_clause = (
                    "status = ? AND claim_token = ? AND claim_lease_until > ?"
                )
                owner_parameters = (self._EVALUATING, claim_token, now)
            released = connection.execute(
                "UPDATE oracle_merge_attempts SET status = ?, updated_at = ?, "
                "retry_not_before = ?, claim_token = NULL, claim_lease_until = NULL "
                "WHERE artifact_id = ? AND result_json IS NULL AND "
                + owner_clause,
                (
                    self._RECOVERABLE,
                    now,
                    stored,
                    result.artifact_id,
                    *owner_parameters,
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

    def attempt_status(self, artifact_id: str) -> str | None:
        """Return the persisted recovery class without exposing artifact content."""

        status, _ = self.attempt_recovery_state(artifact_id)
        return status

    def attempt_recovery_state(
        self, artifact_id: str
    ) -> tuple[str | None, datetime | None]:
        """Return the recovery class and bounded not-before deadline."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, retry_not_before FROM oracle_merge_attempts "
                "WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None, None
        return str(row["status"]), _parse_stored_deadline(row["retry_not_before"])


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
        evaluation_lease_seconds: int = 7_200,
        sleep: Callable[[float], None] = time.sleep,
        # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
        state_witness: StateWitnessLedger | None = None,
        checks_green_oracle: Callable[[str, str], WitnessRow] | None = None,
        # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]
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
        if evaluation_lease_seconds < 60:
            raise ValueError("Oracle merge evaluation lease must be at least 60 seconds")
        self.policy = policy
        self.store = store
        self.github = github
        self._poll_attempts = mergeable_poll_attempts
        self._poll_read_failures = mergeable_poll_read_failures
        self._poll_stale_draft_reads = mergeable_poll_stale_draft_reads
        self._poll_base_delay = mergeable_poll_base_delay_seconds
        self._poll_max_delay = mergeable_poll_max_delay_seconds
        self._evaluation_lease_seconds = evaluation_lease_seconds
        self._claim_context = threading.local()
        self._sleep = sleep
        # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
        self._witness = state_witness
        # A real checks oracle (see state_witness.gh_checks_green) that sees job
        # step counts; the forge snapshot carries none, so without one the
        # green claim is UNOBSERVABLE and the gate denies.
        self._checks_green_oracle = checks_green_oracle
        # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]

    def process(self, artifact: OracleReviewArtifact) -> OracleMergeResult:
        if not artifact.signature_is_valid(self.policy.review_secret):
            raise ValueError("review artifact signature is invalid")
        claim = self.store.claim(
            artifact, lease_seconds=self._evaluation_lease_seconds
        )
        if isinstance(claim, OracleMergeResult):
            return claim
        self._claim_context.token = claim.token

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
            return self._complete(self.policy.evaluate(artifact, placeholder))
        if self.github is None:
            return self._complete(
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

        snapshot, unreadable = self._read_snapshot(artifact)
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
                return self._complete(preflight)
            try:
                self._renew_claim(artifact)
                ready = self.github.mark_ready(
                    self.policy.repository,
                    artifact.pull_number,
                    artifact.head_sha,
                    artifact.base_sha,
                )
                if not ready:
                    raise RuntimeError("GitHub refused to mark the pull request ready")
            except OracleMergeClaimExpired:
                raise
            except OracleRateLimitError as exc:
                return self._release_for_rate_limit(artifact, exc)
            except Exception as exc:
                preflight.status = OracleMergeStatus.ERROR
                preflight.reasons = [
                    f"GitHub ready-for-review transition failed closed: {type(exc).__name__}"
                ]
                return self._complete(preflight)
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
            return self._complete(decision)

        # The first evaluation can race the mutation. Take one complete snapshot
        # and re-run the full policy immediately before GitHub merge; a changed
        # thread, review decision, check, title/body safety signal, path/file
        # evidence, or SHA must block. Unresolved mergeability here is terminal.
        # A lagging REST draft flag after a gate-owned ready transition stays on
        # the same bounded stale-draft wait as `_resolved_snapshot` and does not
        # consume the artifact. Bounded read retries and rate-limit holds still
        # apply. The merge mutation itself is never retried if the result is
        # ambiguous. The gateway still rechecks base/head SHAs as defense in depth.
        final_snapshot, unreadable = self._final_snapshot(
            artifact, transitioned_from_draft=transitioned_from_draft
        )
        if unreadable is not None:
            return unreadable
        decision = self.policy.evaluate(artifact, final_snapshot)
        if decision.reasons or decision.serious_safety_reasons:
            return self._complete(decision)
        # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
        # No receipt, no state. The final snapshot is the oracle; write the
        # rows the merge consumes, and refuse on anything but MATCH.
        refusal = self._witness_final_snapshot(artifact, final_snapshot)
        if refusal is not None:
            decision.status = OracleMergeStatus.REJECTED
            decision.reasons = [refusal]
            return self._complete(decision)
        # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]

        try:
            self._renew_claim(artifact)
            merged = self.github.merge(
                self.policy.repository,
                artifact.pull_number,
                artifact.head_sha,
                artifact.base_sha,
            )
        except OracleMergeClaimExpired:
            raise
        except OracleRateLimitError as exc:
            return self._release_for_rate_limit(artifact, exc)
        except Exception as exc:
            decision.status = OracleMergeStatus.ERROR
            decision.reasons = [f"GitHub merge failed closed: {type(exc).__name__}"]
            return self._complete(decision)
        if not merged.merged or not merged.merge_commit_sha:
            decision.status = OracleMergeStatus.ERROR
            decision.reasons = ["GitHub refused the exact-SHA merge"]
            decision.actor = merged.actor
            return self._complete(decision)

        decision.status = OracleMergeStatus.MERGED
        decision.merge_commit_sha = merged.merge_commit_sha
        decision.actor = merged.actor
        return self._complete(decision)

    # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
    def _witness_final_snapshot(
        self, artifact: OracleReviewArtifact, snapshot: OraclePullRequestSnapshot
    ) -> str | None:
        """Record what the gate observed at consumption time; return a refusal
        reason, or None when both witnessed claims match."""

        if self._witness is None:
            return None
        agent = artifact.reviewer_provider
        try:
            not_merged = snapshot_not_merged(
                self._witness, self.policy.repository, artifact.pull_number,
                artifact.head_sha, agent=agent, merged=snapshot.merged,
                state=snapshot.state, head_sha=snapshot.head_sha,
            )
            if self._checks_green_oracle is None:
                # No oracle that can see step counts: unobservable, so deny.
                return (
                    "witness refused gh_checks_green: no checks oracle configured;"
                    " job step counts are UNOBSERVABLE from the forge snapshot"
                )
            green = self._checks_green_oracle(self.policy.repository, artifact.head_sha)
        except WitnessLedgerFull as exc:
            return f"witness ledger unavailable: {exc}"
        # The gate decides from the persisted row, never from the object the
        # oracle handed back: the returned row must be the latest persisted
        # GH_CHECKS_GREEN row for this repository and SHA, field for field.
        # Anything else is a forged or misrouted observation and is refused.
        expected_subject = f"{self.policy.repository}@{artifact.head_sha}"
        persisted = self._witness.latest(ClaimKind.GH_CHECKS_GREEN, expected_subject)
        if (
            not isinstance(green, WitnessRow)
            or persisted is None
            or persisted != green
        ):
            return (
                "witness refused gh_checks_green: the checks oracle returned a row"
                f" that is not the persisted observation for {expected_subject}"
            )
        green = persisted
        for row in (not_merged, green):
            if not row.matched:
                return (
                    f"witness refused {row.kind} ({row.status}): claimed {row.claimed!r},"
                    f" observed {json.dumps(row.observed, sort_keys=True)}"
                    f" (witness:{row.witness_id})"
                )
        return None
    # [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]

    def _claim_token(self) -> str:
        token = getattr(self._claim_context, "token", None)
        if not token:
            raise OracleMergeClaimExpired("Oracle merge evaluation has no active claim")
        return str(token)

    def _renew_claim(self, artifact: OracleReviewArtifact) -> None:
        self.store.renew_claim(
            artifact.artifact_id,
            self._claim_token(),
            lease_seconds=self._evaluation_lease_seconds,
        )

    def _complete(self, result: OracleMergeResult) -> OracleMergeResult:
        return self.store.complete(result, claim_token=self._claim_token())

    def _read_snapshot(
        self, artifact: OracleReviewArtifact
    ) -> tuple[OraclePullRequestSnapshot | None, OracleMergeResult | None]:
        """Take one complete GitHub snapshot, with bounded read-failure handling.

        A read that never produced a snapshot proves nothing about the pull request, so
        each snapshot site — the first read and the final pre-mutation read — gets the
        same bounded read-failure allowance. Recording a terminal verdict here would
        spend a correctly reviewed artifact on a network blip; exhausting the allowance
        releases it for an authenticated retry instead. Nothing is merged from here, so
        nothing ambiguous is retried.
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
                self._renew_claim(artifact)
                return (
                    self.github.snapshot(artifact.repository, artifact.pull_number),
                    None,
                )
            except OracleMergeClaimExpired:
                raise
            except OracleDiffError as exc:
                return None, self._complete_evidence_failure(artifact, exc)
            except OracleRateLimitError as exc:
                return None, self._release_for_rate_limit(artifact, exc)
            except Exception as exc:
                read_failure_allowance -= 1
                last_read_failure = exc
        return None, self._release_for_retry(
            artifact,
            f"GitHub validation read failed closed after {self._poll_read_failures} "
            "transient read failures and remains retryable: "
            f"{type(last_read_failure).__name__}",
        )

    def _final_snapshot(
        self,
        artifact: OracleReviewArtifact,
        *,
        transitioned_from_draft: bool,
    ) -> tuple[OraclePullRequestSnapshot | None, OracleMergeResult | None]:
        """Take the pre-mutation snapshot without consuming a gate-owned stale draft.

        After ``mark_ready`` already confirmed the pull request is ready, a lagging
        REST ``draft: true`` is the same bounded transient that ``_resolved_snapshot``
        already classifies. This read keeps that classification: the artifact is not
        consumed, and merge waits for a fresh complete snapshot that is non-draft and
        fully eligible. An unowned draft, unresolved mergeability, and every other
        policy or safety reason stay fail-closed on the read that reports them. Rate
        limits and evidence failures keep their existing holds. Nothing is merged
        from here, so nothing ambiguous is retried.
        """
        snapshot, unreadable = self._read_snapshot(artifact)
        if unreadable is not None:
            return None, unreadable
        stale_draft_allowance = (
            self._poll_stale_draft_reads if transitioned_from_draft else 1
        )
        read_failure_allowance = self._poll_read_failures
        last_read_failure: Exception | None = None
        delay = self._poll_base_delay
        while True:
            stale_draft = transitioned_from_draft and snapshot.draft
            decision = self.policy.evaluate(artifact, snapshot)
            blocking = [
                reason
                for reason in decision.reasons
                if not (
                    stale_draft and reason == self.policy.DRAFT_NOT_MERGEABLE_REASON
                )
            ]
            if blocking or decision.serious_safety_reasons:
                return None, self._complete(decision)
            if not stale_draft:
                return snapshot, None
            stale_draft_allowance -= 1
            if not stale_draft_allowance:
                return None, self._release_for_retry(
                    artifact,
                    "GitHub still reported the pull request as draft after "
                    f"{self._poll_stale_draft_reads} bounded re-reads following a "
                    "confirmed ready-for-review transition and remains retryable",
                )
            while read_failure_allowance:
                self._sleep(delay)
                delay = min(delay * 2, self._poll_max_delay)
                try:
                    self._renew_claim(artifact)
                    snapshot = self.github.snapshot(
                        artifact.repository, artifact.pull_number
                    )
                    break
                except OracleMergeClaimExpired:
                    raise
                except OracleDiffError as exc:
                    return None, self._complete_evidence_failure(artifact, exc)
                except OracleRateLimitError as exc:
                    return None, self._release_for_rate_limit(artifact, exc)
                except Exception as exc:
                    read_failure_allowance -= 1
                    last_read_failure = exc
            else:
                return None, self._release_for_retry(
                    artifact,
                    "GitHub validation read failed closed after "
                    f"{self._poll_read_failures} transient read failures and remains "
                    f"retryable: {type(last_read_failure).__name__}",
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
                self._renew_claim(artifact)
                snapshot = self.github.snapshot(
                    artifact.repository, artifact.pull_number
                )
            except OracleMergeClaimExpired:
                raise
            except OracleDiffError as exc:
                return None, self._complete_evidence_failure(artifact, exc)
            except OracleRateLimitError as exc:
                return None, self._release_for_rate_limit(artifact, exc)
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
                return None, self._complete(interim)
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
        return self._complete(
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

    def _release_for_rate_limit(
        self, artifact: OracleReviewArtifact, exc: OracleRateLimitError
    ) -> OracleMergeResult:
        return self.store.release(
            OracleMergeResult(
                artifact_id=artifact.artifact_id,
                artifact_digest=artifact.digest(),
                status=OracleMergeStatus.ERROR,
                repository=self.policy.repository,
                pull_number=artifact.pull_number,
                reviewed_sha=artifact.head_sha,
                reasons=[
                    "GitHub rate limit requires a later retry and remains "
                    f"retryable: {type(exc).__name__}"
                ],
            ),
            retry_not_before=exc.retry_not_before,
            claim_token=self._claim_token(),
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
            ),
            claim_token=self._claim_token(),
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


_RATE_LIMIT_FALLBACK_SECONDS = 60
_MAX_RATE_LIMIT_WINDOW_SECONDS = 7 * 24 * 60 * 60


def _rate_limit_not_before(response: httpx.Response) -> datetime:
    now = datetime.now(timezone.utc)
    deadlines = []
    retry_after = _parse_retry_after((response.headers.get("retry-after") or "").strip(), now)
    reset = _parse_rate_limit_reset((response.headers.get("x-ratelimit-reset") or "").strip(), now)
    if retry_after is not None:
        deadlines.append(retry_after)
    if reset is not None:
        deadlines.append(reset)
    if not deadlines:
        return now + timedelta(seconds=_RATE_LIMIT_FALLBACK_SECONDS)
    return max(deadlines)


def _parse_retry_after(raw: str, now: datetime) -> datetime | None:
    if not raw:
        return None
    if raw.isdigit():
        if not (1 <= len(raw) <= 7):
            return None
        seconds = int(raw)
        if seconds < 1 or seconds > _MAX_RATE_LIMIT_WINDOW_SECONDS:
            return None
        return now + timedelta(seconds=seconds)
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _bounded_future_deadline(parsed.astimezone(timezone.utc), now)


def _parse_rate_limit_reset(raw: str, now: datetime) -> datetime | None:
    if not raw.isdigit() or not (9 <= len(raw) <= 11):
        return None
    try:
        parsed = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return _bounded_future_deadline(parsed, now)


def _bounded_future_deadline(parsed: datetime, now: datetime) -> datetime | None:
    if parsed <= now:
        return None
    latest = now + timedelta(seconds=_MAX_RATE_LIMIT_WINDOW_SECONDS)
    if parsed > latest:
        return latest
    return parsed


def _parse_stored_deadline(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_unparseable_nonempty_deadline(raw: str | None) -> bool:
    return bool(raw) and _parse_stored_deadline(raw) is None


def _utc_deadline_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _later_deadline_text(existing: str | None, proposed: datetime | None) -> str | None:
    current = _parse_stored_deadline(existing)
    if proposed is None:
        return _utc_deadline_text(current) if current is not None else existing
    proposed_utc = proposed.astimezone(timezone.utc)
    if current is None or proposed_utc > current:
        return _utc_deadline_text(proposed_utc)
    return _utc_deadline_text(current)


# GitHub check-run and review-thread evidence is read at exactly the page size
# requested, so a page that overruns it is malformed rather than merely long.
MAX_CHECK_RUNS_PER_PAGE = 100
MAX_REVIEW_THREAD_NODES = 100
CHECK_RUN_EVIDENCE_MALFORMED = "GitHub check-run evidence is malformed"
CHECK_RUN_ENUMERATION_INCOMPLETE = "GitHub check-run enumeration is incomplete"
REVIEW_THREAD_EVIDENCE_INCOMPLETE = "GitHub review-thread evidence is incomplete"
PULL_REQUEST_EVIDENCE_MALFORMED = "GitHub pull request evidence is malformed"

# A GitHub response body that cannot be read as the shape the gate requires is
# durable evidence failure, not a transport blip: re-reading it returns the same
# bytes. Mapping these to OracleDiffError keeps them inside the terminal-evidence
# boundary so a malformed payload is consumed once instead of driving repeated
# full pull/files/compare cycles and endless recoverable redelivery. Transport
# exceptions and OracleRateLimitError never reach these guards, because every
# guard wraps parsing only — never the request itself.
_MALFORMED_EVIDENCE_ERRORS = (
    KeyError,
    ValueError,
    TypeError,
    AttributeError,
    ValidationError,
)


@dataclass(frozen=True)
class ReviewThreadEvidence:
    """Normalized GraphQL review-thread evidence with every field proven."""

    blocking: int
    has_next_page: bool
    review_decision: str | None


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

    def _request(
        self, method: str, path: str, *, mutate: bool = False, **kwargs
    ) -> httpx.Response:
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
            if not mutate and self._is_rate_limited_status(response):
                raise OracleRateLimitError(
                    f"GitHub API {method} {path} is rate limited",
                    retry_not_before=_rate_limit_not_before(response),
                )
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
        if not all(isinstance(item, dict) for item in payload):
            raise OracleDiffError("GitHub pull request evidence is malformed")
        names = [item.get("filename") for item in payload]
        if len(set(names)) != len(names):
            raise OracleDiffError("GitHub file enumeration is incomplete")
        return payload

    @staticmethod
    def _require_complete_file_enumeration(pull: dict, files: list[dict]) -> None:
        if "changed_files" not in pull:
            raise OracleDiffError("GitHub file enumeration is incomplete")
        raw = pull["changed_files"]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw != len(files):
            raise OracleDiffError("GitHub file enumeration is incomplete")

    @staticmethod
    def _require_rest_file_identities(files: list[dict]) -> None:
        for item in files:
            filename = item.get("filename")
            status = item.get("status")
            if (
                not isinstance(filename, str)
                or not filename
                or not isinstance(status, str)
                or not status
            ):
                raise OracleDiffError("GitHub pull request evidence is malformed")
            require_rest_nonnegative_int(item.get("additions"))
            require_rest_nonnegative_int(item.get("deletions"))

    @staticmethod
    def _require_sha40(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise OracleDiffError("GitHub pull request SHA is not a pinned 40-hex digest")
        return value

    def _pull_identity(self, repository: str, pull_number: int, pull: dict) -> dict[str, object]:
        """Bind the pull-request identity from proven scalars only.

        Truthiness and lax coercion are not evidence here either: a coerced flag
        would compare equal to a genuine one and hide drift between the two reads
        this identity exists to bracket.
        """
        return {
            "repository": canonical_repository(repository),
            "pull_number": pull_number,
            "title": require_rest_optional_text(pull.get("title"), max_length=2_000) or "",
            "body": require_rest_optional_text(pull.get("body"), max_length=100_000) or "",
            "draft": require_rest_bool(pull["draft"]),
            "state": require_rest_text(pull["state"], max_length=64),
            "merged": require_rest_optional_bool(pull.get("merged")) is True,
            "base_ref": require_rest_text(pull["base"]["ref"], max_length=255),
            "base_sha": self._require_sha40(pull["base"]["sha"]),
            "head_ref": require_rest_text(pull["head"]["ref"], max_length=255),
            "head_sha": self._require_sha40(pull["head"]["sha"]),
            "head_repository": canonical_repository(pull["head"]["repo"]["full_name"]),
            "author": require_rest_text(pull["user"]["login"], max_length=255),
            "changed_files": require_rest_nonnegative_int(pull["changed_files"]),
        }

    @staticmethod
    def _file_set(files: list[dict]) -> tuple[tuple[object, ...], ...]:
        try:
            return tuple(
                sorted(
                    (
                        item["filename"],
                        item.get("previous_filename"),
                        item["status"],
                        require_rest_nonnegative_int(item["additions"]),
                        require_rest_nonnegative_int(item["deletions"]),
                    )
                    for item in files
                )
            )
        except OracleDiffError:
            raise
        except (KeyError, ValueError, TypeError) as exc:
            raise OracleDiffError("GitHub pull request evidence is malformed") from exc

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
                if response.status_code == 401:
                    if self._token_provider is not None and attempt == 0:
                        self._token_provider.invalidate()
                        continue
                    break
                if response.status_code == 406:
                    raise OracleDiffError(
                        "complete compare diff exceeds the bounded evidence limit"
                    )
                if self._is_rate_limited_status(response):
                    raise OracleRateLimitError(
                        f"GitHub API {method} {path} is rate limited",
                        retry_not_before=_rate_limit_not_before(response),
                    )
                if self._is_transient_compare_status(response):
                    raise RuntimeError(
                        f"GitHub API {method} {path} failed with status {response.status_code}"
                    )
                if response.status_code != 200:
                    if response.status_code >= 400:
                        raise OracleDiffError("complete compare diff is unavailable")
                    raise OracleDiffError("complete compare diff is incomplete")
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
                    if len(chunk) > max_bytes - len(collected):
                        raise OracleDiffError(
                            "complete compare diff exceeds the bounded evidence limit"
                        )
                    collected.extend(chunk)
                if declared is not None and int(declared) != len(collected):
                    raise OracleDiffError("complete compare diff is truncated")
                return bytes(collected)
        raise RuntimeError(f"GitHub API {method} {path} authentication failed")

    @staticmethod
    def _is_rate_limited_status(response: httpx.Response) -> bool:
        if response.status_code == 429:
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

    _GRAPHQL_RATE_LIMIT_CODES = frozenset({"RATE_LIMITED", "RATE_LIMIT"})

    @classmethod
    def _graphql_has_rate_limit_error(cls, payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return False
        for item in errors:
            if not isinstance(item, dict):
                continue
            tokens = [item.get("type")]
            extensions = item.get("extensions")
            if isinstance(extensions, dict):
                tokens.append(extensions.get("code"))
            if any(token in cls._GRAPHQL_RATE_LIMIT_CODES for token in tokens):
                return True
        return False

    @classmethod
    def _is_graphql_rate_limited(cls, response: httpx.Response, payload: object) -> bool:
        if cls._graphql_has_rate_limit_error(payload):
            return True
        if (response.headers.get("retry-after") or "").strip():
            return True
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is None:
            return False
        try:
            return int(remaining.strip()) == 0
        except ValueError:
            return False

    @classmethod
    def _check_run_evidence(cls, response: httpx.Response) -> list[OracleCheckRun]:
        """Normalize the check-run page, proving every field the policy reads.

        The rate-limit and transport classifications are already settled by the
        request that produced ``response``; everything from here is pure parsing of
        bytes GitHub already delivered. A body that is not a check-run page, an
        enumeration that does not account for itself, or a run whose identity,
        producer, suite, or head SHA is missing or wrongly typed is therefore
        durable evidence failure, and is raised as ``OracleDiffError`` so the
        service consumes it once instead of re-reading it as a transient blip.
        Messages stay constant: no untrusted payload value is ever interpolated.
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise OracleDiffError(CHECK_RUN_EVIDENCE_MALFORMED) from exc
        payload = require_rest_mapping(payload, message=CHECK_RUN_EVIDENCE_MALFORMED)
        raw_runs = payload.get("check_runs")
        if not isinstance(raw_runs, list) or len(raw_runs) > MAX_CHECK_RUNS_PER_PAGE:
            raise OracleDiffError(CHECK_RUN_EVIDENCE_MALFORMED)
        if "total_count" not in payload:
            raise OracleDiffError(CHECK_RUN_ENUMERATION_INCOMPLETE)
        total_count = require_rest_nonnegative_int(
            payload["total_count"], message=CHECK_RUN_EVIDENCE_MALFORMED
        )
        # A page that does not account for the enumeration it declares cannot prove
        # which checks ran, so the merge fails closed on it rather than merging on a
        # partial view.
        if total_count != len(raw_runs):
            raise OracleDiffError(CHECK_RUN_ENUMERATION_INCOMPLETE)
        checks: list[OracleCheckRun] = []
        for item in raw_runs:
            run = require_rest_mapping(item, message=CHECK_RUN_EVIDENCE_MALFORMED)
            checks.append(
                OracleCheckRun(
                    run_id=require_rest_positive_int(
                        run.get("id"), message=CHECK_RUN_EVIDENCE_MALFORMED
                    ),
                    suite_id=cls._check_suite_id(run.get("check_suite")),
                    name=require_rest_text(
                        run.get("name"),
                        max_length=255,
                        message=CHECK_RUN_EVIDENCE_MALFORMED,
                    ),
                    status=require_rest_text(
                        run.get("status"),
                        max_length=64,
                        message=CHECK_RUN_EVIDENCE_MALFORMED,
                    ),
                    conclusion=require_rest_optional_text(
                        run.get("conclusion"),
                        max_length=64,
                        message=CHECK_RUN_EVIDENCE_MALFORMED,
                    ),
                    head_sha=require_rest_sha40(
                        run.get("head_sha"), message=CHECK_RUN_EVIDENCE_MALFORMED
                    ),
                    app_slug=cls._check_app_slug(run.get("app")),
                )
            )
        return checks

    @staticmethod
    def _check_suite_id(raw: object) -> int | None:
        """Absent suite evidence stays absent; present suite evidence must be real."""
        if raw is None:
            return None
        suite = require_rest_mapping(raw, message=CHECK_RUN_EVIDENCE_MALFORMED)
        identifier = suite.get("id")
        if identifier is None:
            return None
        return require_rest_positive_int(
            identifier, message=CHECK_RUN_EVIDENCE_MALFORMED
        )

    @staticmethod
    def _check_app_slug(raw: object) -> str:
        """Unproven producers stay ``unknown`` so they can never match a trusted app.

        A missing app object and a missing slug both keep the existing fail-closed
        default. A present-but-wrongly-typed app or slug is malformed evidence.
        """
        if raw is None:
            return "unknown"
        app = require_rest_mapping(raw, message=CHECK_RUN_EVIDENCE_MALFORMED)
        slug = app.get("slug")
        if slug is None or slug == "":
            return "unknown"
        return require_rest_text(
            slug, max_length=255, message=CHECK_RUN_EVIDENCE_MALFORMED
        )

    def _review_thread_evidence(
        self, response: httpx.Response
    ) -> ReviewThreadEvidence:
        """Normalize GraphQL review-thread evidence, proving every flag it reports.

        ``isResolved``/``isOutdated``/``hasNextPage`` are read as real JSON booleans
        rather than by truthiness: a string ``"false"`` is truthy in Python, so a
        truthiness read would silently drop an unresolved blocking thread and report
        the enumeration as complete. Every malformed shape here is durable and maps
        to ``OracleDiffError``; only the rate-limit classification, settled before
        parsing, still releases for a later retry.
        """
        payload: object
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if self._is_graphql_rate_limited(response, payload if payload is not None else {}):
            raise OracleRateLimitError(
                "GitHub API POST /graphql is rate limited",
                retry_not_before=_rate_limit_not_before(response),
            )
        if not isinstance(payload, dict) or payload.get("errors"):
            raise OracleDiffError(REVIEW_THREAD_EVIDENCE_INCOMPLETE)
        message = REVIEW_THREAD_EVIDENCE_INCOMPLETE
        data = require_rest_mapping(payload.get("data"), message=message)
        repository = require_rest_mapping(data.get("repository"), message=message)
        pull_request = require_rest_mapping(
            repository.get("pullRequest"), message=message
        )
        threads = require_rest_mapping(
            pull_request.get("reviewThreads"), message=message
        )
        nodes = threads.get("nodes")
        if not isinstance(nodes, list) or len(nodes) > MAX_REVIEW_THREAD_NODES:
            raise OracleDiffError(message)
        page_info = require_rest_mapping(threads.get("pageInfo"), message=message)
        has_next_page = require_rest_bool(
            page_info.get("hasNextPage"), message=message
        )
        blocking = 0
        for raw_node in nodes:
            node = require_rest_mapping(raw_node, message=message)
            resolved = require_rest_bool(node.get("isResolved"), message=message)
            outdated = require_rest_bool(node.get("isOutdated"), message=message)
            if not resolved and not outdated:
                blocking += 1
        return ReviewThreadEvidence(
            blocking=blocking,
            has_next_page=has_next_page,
            review_decision=require_rest_optional_text(
                pull_request.get("reviewDecision"), max_length=64, message=message
            ),
        )

    @staticmethod
    def _is_transient_compare_status(response: httpx.Response) -> bool:
        return response.status_code >= 500 or response.status_code in {404, 408}

    def _bind_exact_sha_compare(
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
        self._require_rest_file_identities(after_files)
        after = self._pull_identity(repository, pull_number, after_pull)
        if before != after or before_files != self._file_set(after_files):
            raise OracleDiffError("pull request identity changed during complete-diff retrieval")
        bound = bind_complete_diff_evidence(
            after_files,
            parse_compare_diff(diff_text, max_bytes=self._max_compare_diff_bytes),
        )
        try:
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
        except ValidationError as exc:
            raise OracleDiffError(
                "compare file evidence is incomplete or disagrees with structured hunk evidence"
            ) from exc

    def _base_protected(self, repository: str, ref: str, expected_sha: str) -> bool:
        """Bind observed protection and active rules to the exact base branch.

        Missing/private/unsupported API responses are unknown, never unprotected.
        The rules endpoint includes active repository and organization rulesets.
        """
        encoded = quote(ref, safe="")
        branch = self._request("GET", f"/repos/{repository}/branches/{encoded}").json()
        if (not isinstance(branch, dict) or branch.get("name") != ref
                or not isinstance(branch.get("commit"), dict)
                or branch["commit"].get("sha") != expected_sha
                or type(branch.get("protected")) is not bool):
            raise OracleDiffError("base branch protection evidence is malformed or stale")
        if branch["protected"]:
            return True
        rules = self._request("GET", f"/repos/{repository}/rules/branches/{encoded}",
                              params={"per_page": 100}).json()
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            raise OracleDiffError("base branch rules evidence is malformed")
        # Any active rule is sufficient to refuse; no pagination is needed to
        # establish emptiness or the presence of at least one rule.
        return bool(rules)

    def snapshot(self, repository: str, pull_number: int) -> OraclePullRequestSnapshot:
        repository = canonical_repository(repository)
        # A repository that does not canonicalize to owner/name is durable too: it
        # would raise on every re-read, so it fails closed here rather than
        # spending the bounded read allowance to reach the same result.
        owner, _, name = repository.partition("/")
        if not owner or not name:
            raise OracleDiffError(PULL_REQUEST_EVIDENCE_MALFORMED)
        try:
            pull = self._get_pull(repository, pull_number)
            self._require_sha40(str(pull["base"]["sha"]))
            self._require_sha40(str(pull["head"]["sha"]))
            files = self._get_files(repository, pull_number)
            self._require_complete_file_enumeration(pull, files)
            self._require_rest_file_identities(files)
            pull, changed_files = self._bind_exact_sha_compare(
                repository, pull_number, pull, files
            )
            head_sha = self._require_sha40(pull["head"]["sha"])
        except (OracleDiffError, OracleRateLimitError):
            raise
        except _MALFORMED_EVIDENCE_ERRORS as exc:
            raise OracleDiffError(PULL_REQUEST_EVIDENCE_MALFORMED) from exc
        # The request stays outside every parsing guard, so a rate limit still
        # releases for a later retry and a transport failure still spends the
        # bounded transient allowance. Only the body it returned is parsed inside
        # the terminal-evidence boundary.
        checks = self._check_run_evidence(
            self._request(
                "GET",
                f"/repos/{repository}/commits/{head_sha}/check-runs",
                params={"per_page": MAX_CHECK_RUNS_PER_PAGE, "filter": "latest"},
            )
        )

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
        graph_response = self._request(
            "POST",
            "/graphql",
            json={"query": query, "variables": {"owner": owner, "name": name, "number": pull_number}},
        )
        threads = self._review_thread_evidence(graph_response)
        base_protected = self._base_protected(repository, pull["base"]["ref"], pull["base"]["sha"])

        # Assembling the snapshot is the last place untrusted GitHub fields are
        # read, so it carries the same terminal-evidence boundary: a missing or
        # wrongly typed pull-request field is durable and must not be re-read as a
        # transient failure.
        message = PULL_REQUEST_EVIDENCE_MALFORMED
        try:
            return OraclePullRequestSnapshot(
                repository=repository,
                pull_number=pull_number,
                title=require_rest_optional_text(
                    pull.get("title"), max_length=2_000, message=message
                )
                or "",
                body=require_rest_optional_text(
                    pull.get("body"), max_length=100_000, message=message
                )
                or "",
                state=require_rest_text(pull["state"], max_length=64, message=message),
                draft=require_rest_bool(pull["draft"], message=message),
                mergeable=require_rest_optional_bool(
                    pull.get("mergeable"), message=message
                ),
                merged=require_rest_optional_bool(pull.get("merged"), message=message)
                is True,
                base_ref=require_rest_text(
                    pull["base"]["ref"], max_length=255, message=message
                ),
                base_sha=require_rest_sha40(pull["base"]["sha"], message=message),
                head_ref=require_rest_text(
                    pull["head"]["ref"], max_length=255, message=message
                ),
                head_sha=require_rest_sha40(pull["head"]["sha"], message=message),
                head_repository=pull["head"]["repo"]["full_name"],
                author_login=require_rest_text(
                    pull["user"]["login"], max_length=255, message=message
                ),
                changed_files=changed_files,
                checks=checks,
                base_protected=base_protected,
                blocking_review_threads=threads.blocking,
                review_threads_complete=not threads.has_next_page,
                review_decision=threads.review_decision,
            )
        except (OracleDiffError, OracleRateLimitError):
            raise
        except _MALFORMED_EVIDENCE_ERRORS as exc:
            raise OracleDiffError(PULL_REQUEST_EVIDENCE_MALFORMED) from exc

    def merge(
        self,
        repository: str,
        pull_number: int,
        expected_sha: str,
        expected_base_sha: str,
    ) -> OracleGitHubMerge:
        repository = canonical_repository(repository)
        # Defense in depth after the service's final full-policy snapshot. GitHub's
        # REST merge binds the expected head SHA but has no atomic expected-base
        # parameter; this preflight still rejects a drifted base or head before PUT.
        pull = self._request("GET", f"/repos/{repository}/pulls/{pull_number}").json()
        if pull["head"]["sha"] != expected_sha or pull["base"]["sha"] != expected_base_sha:
            raise RuntimeError("GitHub pull request changed during final merge validation")
        if self._base_protected(repository, pull["base"]["ref"], expected_base_sha):
            raise RuntimeError("protected base branch cannot be merged automatically")
        actor = (
            self._token_provider.actor
            if self._token_provider is not None
            else self._request("GET", "/user").json().get("login")
            or "unknown-github-actor"
        )
        payload = self._request(
            "PUT",
            f"/repos/{repository}/pulls/{pull_number}/merge",
            mutate=True,
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
            mutate=True,
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
# [[Codex | GPT-5.6 | 2026-08-24 | 09:24 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 16:30 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 00:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 06:33 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:10 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:42 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
