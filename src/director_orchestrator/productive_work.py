# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:21 UTC | BEGIN]]
"""Typed authority and evidence for externally produced OpenAI work.

These values are deliberately store-level primitives.  They do not create an
HTTP surface and they do not turn the Director's runtime OpenAI provider into a
production provider.  A trusted caller must first persist the exact,
authenticated owner authorization and may then append one matching accepted
candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum

from .workload_policy import DirectProductionProvider, Provider


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_INDEPENDENT_REVIEWERS = frozenset(
    {Provider.CLAUDE, Provider.CURSOR, Provider.GROK}
)


class ExternalOwnerAuthorizationSource(str, Enum):
    """Authenticated owner channels allowed to mint an exact authorization."""

    API = "api"
    SLACK_COMMAND = "slack_command"
    SLACK_BUTTON = "slack_button"


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical identifier")


def _require_digest(value: str, label: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_candidate(head_sha: str | None, digest: str | None) -> None:
    if (head_sha is None) == (digest is None):
        raise ValueError("exactly one candidate head SHA or candidate digest is required")
    if head_sha is not None and (
        not isinstance(head_sha, str) or _GIT_SHA.fullmatch(head_sha) is None
    ):
        raise ValueError("candidate head SHA must be a complete lowercase Git SHA")
    if digest is not None:
        _require_digest(digest, "candidate digest")


def _require_git_sha(value: str, label: str) -> None:
    if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
        raise ValueError(f"{label} must be a complete lowercase Git SHA")


def _require_common_binding(
    *,
    task_id: str,
    plan_generation: int,
    assignment_sequence: int,
    repository: str,
    base_sha: str,
    candidate_head_sha: str | None,
    candidate_digest: str | None,
    producer: DirectProductionProvider,
    effort_units: int,
    reviewer_provider: Provider,
) -> None:
    _require_identifier(task_id, "task id")
    if isinstance(plan_generation, bool) or not isinstance(plan_generation, int):
        raise ValueError("plan generation must be an integer")
    if plan_generation < 0:
        raise ValueError("plan generation must not be negative")
    if (
        isinstance(assignment_sequence, bool)
        or not isinstance(assignment_sequence, int)
        or not 1 <= assignment_sequence <= 20
    ):
        raise ValueError("assignment sequence must be between 1 and 20")
    if not isinstance(repository, str) or _REPOSITORY.fullmatch(repository) is None:
        raise ValueError("repository must be an exact owner/name identity")
    _require_git_sha(base_sha, "base SHA")
    _require_candidate(candidate_head_sha, candidate_digest)
    if producer is not DirectProductionProvider.EXTERNAL_OPENAI:
        raise ValueError("external OpenAI production requires the external_openai producer")
    if (
        isinstance(effort_units, bool)
        or not isinstance(effort_units, int)
        or not 1 <= effort_units <= 100
    ):
        raise ValueError("effort units must be between 1 and 100")
    if (
        not isinstance(reviewer_provider, Provider)
        or reviewer_provider not in _INDEPENDENT_REVIEWERS
    ):
        raise ValueError(
            "external OpenAI production requires a different Claude, Cursor, or Grok reviewer"
        )


def _productive_unit_key(
    *,
    task_id: str,
    plan_generation: int,
    assignment_sequence: int,
    repository: str,
    base_sha: str,
    candidate_head_sha: str | None,
    candidate_digest: str | None,
    producer: DirectProductionProvider,
) -> str:
    """Return one immutable identity for the exact productive unit/candidate.

    Authorization IDs, replay IDs, and evidence digests are deliberately absent:
    changing any transport identifier must not mint a second credit for the same
    task generation, assignment unit, repository, base, candidate, and producer.
    """

    payload = json.dumps(
        {
            "binding_version": "external-openai-productive-unit/v1",
            "task_id": task_id,
            "plan_generation": plan_generation,
            "assignment_sequence": assignment_sequence,
            "repository": repository,
            "base_sha": base_sha,
            "candidate_head_sha": candidate_head_sha,
            "candidate_digest": candidate_digest,
            "producer": producer.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"external-openai-unit:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class ExternalOpenAIProductionAuthorization:
    """One exact owner-authorized external OpenAI production assignment."""

    authorization_id: str
    owner_authorization_digest: str
    owner_user_id_digest: str
    source: ExternalOwnerAuthorizationSource
    task_id: str
    plan_generation: int
    assignment_sequence: int
    repository: str
    base_sha: str
    candidate_head_sha: str | None
    candidate_digest: str | None
    producer: DirectProductionProvider
    effort_units: int
    reviewer_provider: Provider

    def __post_init__(self) -> None:
        _require_identifier(self.authorization_id, "authorization id")
        _require_digest(self.owner_authorization_digest, "owner authorization digest")
        _require_digest(self.owner_user_id_digest, "owner user id digest")
        if not isinstance(self.source, ExternalOwnerAuthorizationSource):
            raise ValueError("owner authorization source is invalid")
        _require_common_binding(
            task_id=self.task_id,
            plan_generation=self.plan_generation,
            assignment_sequence=self.assignment_sequence,
            repository=self.repository,
            base_sha=self.base_sha,
            candidate_head_sha=self.candidate_head_sha,
            candidate_digest=self.candidate_digest,
            producer=self.producer,
            effort_units=self.effort_units,
            reviewer_provider=self.reviewer_provider,
        )

    @property
    def productive_unit_key(self) -> str:
        return _productive_unit_key(
            task_id=self.task_id,
            plan_generation=self.plan_generation,
            assignment_sequence=self.assignment_sequence,
            repository=self.repository,
            base_sha=self.base_sha,
            candidate_head_sha=self.candidate_head_sha,
            candidate_digest=self.candidate_digest,
            producer=self.producer,
        )

    def binding_identity(self) -> tuple[object, ...]:
        return (
            self.authorization_id,
            self.owner_authorization_digest,
            self.owner_user_id_digest,
            self.source.value,
            self.productive_unit_key,
            self.task_id,
            self.plan_generation,
            self.assignment_sequence,
            self.repository,
            self.base_sha,
            self.candidate_head_sha,
            self.candidate_digest,
            self.producer.value,
            self.effort_units,
            self.reviewer_provider.value,
        )


@dataclass(frozen=True)
class ExternalOpenAIProductionEvidence:
    """Accepted exact-candidate evidence for one authorized external assignment."""

    replay_id: str
    authorization_id: str
    owner_authorization_digest: str
    task_id: str
    plan_generation: int
    assignment_sequence: int
    repository: str
    base_sha: str
    candidate_head_sha: str | None
    candidate_digest: str | None
    producer: DirectProductionProvider
    effort_units: int
    reviewer_provider: Provider
    producer_event_digest: str
    producer_result_digest: str
    acceptance_event_digest: str
    acceptance_result_digest: str
    ci_evidence_digest: str
    security_evidence_digest: str
    review_verdict: str
    ci_passed: bool
    security_cleared: bool

    def __post_init__(self) -> None:
        _require_identifier(self.replay_id, "replay id")
        _require_identifier(self.authorization_id, "authorization id")
        _require_digest(self.owner_authorization_digest, "owner authorization digest")
        _require_common_binding(
            task_id=self.task_id,
            plan_generation=self.plan_generation,
            assignment_sequence=self.assignment_sequence,
            repository=self.repository,
            base_sha=self.base_sha,
            candidate_head_sha=self.candidate_head_sha,
            candidate_digest=self.candidate_digest,
            producer=self.producer,
            effort_units=self.effort_units,
            reviewer_provider=self.reviewer_provider,
        )
        for value, label in (
            (self.producer_event_digest, "producer event digest"),
            (self.producer_result_digest, "producer result digest"),
            (self.acceptance_event_digest, "acceptance event digest"),
            (self.acceptance_result_digest, "acceptance result digest"),
            (self.ci_evidence_digest, "CI evidence digest"),
            (self.security_evidence_digest, "security evidence digest"),
        ):
            _require_digest(value, label)
        if self.review_verdict != "ACCEPT":
            raise ValueError("external OpenAI production requires an ACCEPT verdict")
        if self.ci_passed is not True:
            raise ValueError("external OpenAI production requires passing trusted CI")
        if self.security_cleared is not True:
            raise ValueError("external OpenAI production requires cleared security evidence")

    @property
    def credit_key(self) -> str:
        return self.productive_unit_key

    @property
    def productive_unit_key(self) -> str:
        return _productive_unit_key(
            task_id=self.task_id,
            plan_generation=self.plan_generation,
            assignment_sequence=self.assignment_sequence,
            repository=self.repository,
            base_sha=self.base_sha,
            candidate_head_sha=self.candidate_head_sha,
            candidate_digest=self.candidate_digest,
            producer=self.producer,
        )

    def authorization_binding(self) -> tuple[object, ...]:
        return (
            self.authorization_id,
            self.owner_authorization_digest,
            self.productive_unit_key,
            self.task_id,
            self.plan_generation,
            self.assignment_sequence,
            self.repository,
            self.base_sha,
            self.candidate_head_sha,
            self.candidate_digest,
            self.producer.value,
            self.effort_units,
            self.reviewer_provider.value,
        )

    def evidence_identity(self) -> tuple[object, ...]:
        return (
            self.replay_id,
            self.authorization_id,
            self.credit_key,
            *self.authorization_binding()[1:],
            self.producer_event_digest,
            self.producer_result_digest,
            self.acceptance_event_digest,
            self.acceptance_result_digest,
            self.ci_evidence_digest,
            self.security_evidence_digest,
            self.review_verdict,
        )


# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:21 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 04:50 UTC | END]]
