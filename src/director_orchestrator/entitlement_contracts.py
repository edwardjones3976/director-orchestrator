# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:00 UTC | BEGIN]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:20 UTC | BEGIN]]
"""Versioned entitlement control-plane contracts.

Subjects never declare rights. Owner-signed templates and registrations are the
only authority source after authenticated ``InstallAttestation/v1``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

PROJECT_REGISTRATION_VERSION = "ProjectRegistration/v1"
INSTALL_ATTESTATION_VERSION = "InstallAttestation/v1"
ENTITLEMENT_EVENT_VERSION = "EntitlementEvent/v1"
OWNER_NOTICE_VERSION = "OwnerNotice/v1"
SIGNED_TEMPLATE_VERSION = "SignedEntitlementTemplate/v1"
OWNER_ACTION_VERSION = "EntitlementOwnerAction/v1"
SLACK_DESTINATION_PROOF_VERSION = "SlackDestinationHealthProof/v1"
ACTION_BOUNDARY_QUERY_VERSION = "EntitlementActionBoundaryQuery/v1"
NOTICE_ROUTE_PROOF_VERSION = "NoticeRouteProof/v1"
RUNTIME_EVIDENCE_PROOF_VERSION = "RuntimeEvidenceProof/v1"

SHARED_INTEGRATIONS_TEMPLATE_ID = "shared-integrations/v1"

_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")

NOTICE_SUSPENSION_SECONDS = 300

_SIGNING_EXCLUDED_FIELDS = frozenset(
    {
        "owner_signature_digest",
        "owner_signature",
        "verifier_signature_digest",
    }
)


class EntitlementEventType(StrEnum):
    GRANT = "GRANT"
    SUSPEND = "SUSPEND"
    REVOKE = "REVOKE"
    RESTORE = "RESTORE"
    SUPERSEDE = "SUPERSEDE"
    UNINSTALL = "UNINSTALL"


class OwnerNoticeDeliveryState(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class EntitlementLifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    UNINSTALLED = "UNINSTALLED"
    SUPERSEDED = "SUPERSEDED"


class SubjectKind(StrEnum):
    PROVIDER = "provider"
    PROJECT = "project"


class EntitlementActionType(StrEnum):
    SOURCE_READ = "source.read"
    REVIEW_EXACT_SHA = "review.exact_sha"
    REPOSITORY_WRITE = "repository.write"
    SLACK_WRITE = "slack.write"
    NOTION_WRITE = "notion.write"
    N8N_WRITE = "n8n.write"
    PROVIDER_DISPATCH = "provider.dispatch"


ACTION_CAPABILITY: Mapping[EntitlementActionType, str] = {
    EntitlementActionType.SOURCE_READ: "source.read.manifest",
    EntitlementActionType.REVIEW_EXACT_SHA: "review.exact_sha",
    EntitlementActionType.REPOSITORY_WRITE: "repository.write.non_protected",
    EntitlementActionType.SLACK_WRITE: "integration.slack.write",
    EntitlementActionType.NOTION_WRITE: "integration.notion.write",
    EntitlementActionType.N8N_WRITE: "integration.n8n.write",
    EntitlementActionType.PROVIDER_DISPATCH: "dispatch.scoped",
}

SPEND_BEARING_ACTIONS = frozenset({EntitlementActionType.PROVIDER_DISPATCH})


PROFILE_TEMPLATE_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "director-control-plane/v1": frozenset(
        {
            "orchestration.intake",
            "repository.write.non_protected",
            "dispatch.bounded",
            "review.coordinate",
        }
    ),
    "standard-delivery/v1": frozenset(
        {
            "intake",
            "repository.write.non_protected",
            "paid_dispatch",
        }
    ),
    "oracle-research/v1": frozenset(
        {
            "research.oracle",
            "repository.write.oracle_scope",
        }
    ),
    "read-only/v1": frozenset({"read.only"}),
    "trusted-write/v1": frozenset(
        {
            "intake",
            "repository.write.enumerated",
            "paid_dispatch",
        }
    ),
    "antigravity-exact-sha/v1": frozenset({"review.exact_sha"}),
    "antigravity-team-engineering/v1": frozenset({"source.read.manifest", "dispatch.scoped", "repository.write.non_protected", "review.exact_sha"}),
}

SHARED_TEMPLATE_CAPABILITIES = frozenset(
    {
        "integration.slack",
        "integration.slack.write",
        "integration.notion",
        "integration.notion.write",
        "integration.n8n",
        "integration.n8n.write",
        "dispatch.scoped",
    }
)

AUTHORITY_BEARING_CAPABILITIES = frozenset(
    {
        "source.read.manifest",
        "review.exact_sha",
        "review.coordinate",
        "repository.write.non_protected",
        "repository.write.enumerated",
        "repository.write.oracle_scope",
        "paid_dispatch",
        "provider.spend",
        "dispatch.scoped",
        "dispatch.bounded",
        "integration.slack",
        "integration.slack.write",
        "integration.notion",
        "integration.notion.write",
        "integration.n8n",
        "integration.n8n.write",
        "github.write",
        "github.merge",
        "deploy",
        "publish",
        "secret.admin",
        "config.write",
        "external.representation",
        "orchestration.intake",
        "intake",
    }
)


class EntitlementContractError(ValueError):
    """A contract document failed closed validation."""


def _reject_constant(value: str) -> None:
    raise EntitlementContractError(f"non-finite JSON number is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EntitlementContractError("duplicate JSON object key is not allowed")
        result[key] = value
    return result


def _decode_strict_json(raw: bytes, *, source: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EntitlementContractError(f"{source} is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
        EntitlementContractError,
    ) as exc:
        raise EntitlementContractError(f"{source} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise EntitlementContractError(f"{source} must be a JSON object")
    return value


def _require_exact_keys(document: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = set(document) - allowed
    if unknown:
        raise EntitlementContractError(
            f"{label} contains unknown fields: " + ", ".join(sorted(unknown))
        )


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise EntitlementContractError(f"{name} must be timezone-aware UTC")


def _require_finite_positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EntitlementContractError(f"{name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise EntitlementContractError(f"{name} must be a finite positive number")


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def document_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def signing_bytes(payload: Mapping[str, Any]) -> bytes:
    filtered = {
        key: value
        for key, value in payload.items()
        if key not in _SIGNING_EXCLUDED_FIELDS
    }
    return canonical_json_bytes(filtered)


def compute_signature(
    *,
    principal: str,
    signing_payload: bytes,
    secret: bytes,
) -> str:
    if not _IDENTIFIER.fullmatch(principal):
        raise EntitlementContractError("signature principal is not a safe identifier")
    digest = hashlib.sha256(signing_payload).hexdigest()
    return hmac.new(
        secret,
        f"{principal}:{digest}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    *,
    principal: str,
    signature_digest: str,
    signing_payload: bytes,
    secret: bytes,
) -> None:
    if not _SHA256.fullmatch(signature_digest):
        raise EntitlementContractError("signature digest must be lowercase sha256")
    expected = compute_signature(
        principal=principal,
        signing_payload=signing_payload,
        secret=secret,
    )
    if not hmac.compare_digest(expected, signature_digest):
        raise EntitlementContractError("signature mismatch")


def normalize_posix_path(path: str) -> str:
    if not path or "\\" in path or path.startswith("/") or path.endswith("/"):
        raise EntitlementContractError(
            "path must be a canonical repository-relative POSIX path"
        )
    if len(path) > 256 or any(ord(character) < 32 for character in path):
        raise EntitlementContractError("path contains invalid characters")
    parts: list[str] = []
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            raise EntitlementContractError(
                "path must not contain empty, dot, or traversal segments"
            )
        parts.append(segment)
    if not parts:
        raise EntitlementContractError("path must not be empty")
    return "/".join(parts)


def _require_document_keys(
    document: Mapping[str, Any], required: frozenset[str], label: str
) -> None:
    _require_exact_keys(document, required, label)
    missing = required - set(document)
    if missing:
        raise EntitlementContractError(
            f"{label} is missing fields: " + ", ".join(sorted(missing))
        )


def _require_type(value: Any, expected: type | tuple[type, ...], name: str) -> Any:
    if isinstance(value, bool) and expected in ((int, float), int, float):
        raise EntitlementContractError(f"{name} has the wrong type")
    if not isinstance(value, expected):
        raise EntitlementContractError(f"{name} has the wrong type")
    return value


def _parse_utc(value: Any, name: str) -> datetime:
    _require_type(value, str, name)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EntitlementContractError(f"{name} is not ISO-8601") from exc
    _require_utc(parsed, name)
    return parsed


def path_matches_pattern(pattern: str, candidate_path: str) -> bool:
    if not pattern:
        raise EntitlementContractError("path pattern must not be empty")
    normalized = normalize_posix_path(candidate_path)
    if pattern.endswith("/**"):
        prefix = normalize_posix_path(pattern[:-3])
        return normalized == prefix or normalized.startswith(prefix + "/")
    return normalized == normalize_posix_path(pattern)


def effective_capabilities(profile_template_id: str) -> frozenset[str]:
    if profile_template_id not in PROFILE_TEMPLATE_CAPABILITIES:
        raise EntitlementContractError("unknown profile entitlement template")
    if profile_template_id == "antigravity-team-engineering/v1":
        return PROFILE_TEMPLATE_CAPABILITIES[profile_template_id]
    if profile_template_id == SHARED_INTEGRATIONS_TEMPLATE_ID:
        return frozenset(SHARED_TEMPLATE_CAPABILITIES)
    return SHARED_TEMPLATE_CAPABILITIES | PROFILE_TEMPLATE_CAPABILITIES[profile_template_id]


def composed_template_digest(profile_template_id: str) -> str:
    return document_digest(
        {
            "schema_version": SIGNED_TEMPLATE_VERSION,
            "shared_template_id": SHARED_INTEGRATIONS_TEMPLATE_ID,
            "profile_template_id": profile_template_id,
            "capabilities": sorted(effective_capabilities(profile_template_id)),
        }
    )


def authority_bearing_capabilities(capabilities: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(cap for cap in capabilities if cap in AUTHORITY_BEARING_CAPABILITIES))


@dataclass(frozen=True, slots=True)
class RepositoryScope:
    repository: str
    refs: tuple[str, ...]
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _REPOSITORY.fullmatch(self.repository):
            raise EntitlementContractError("repository scope is malformed")
        if not self.refs:
            raise EntitlementContractError("ref scope must not be empty")
        for ref in self.refs:
            if not _SAFE_REF.fullmatch(ref):
                raise EntitlementContractError("ref scope is malformed")
        if not self.paths:
            raise EntitlementContractError("path scope must not be empty")
        for path in self.paths:
            if path.endswith("/**"):
                normalize_posix_path(path[:-3])
            else:
                normalize_posix_path(path)

    def digest(self) -> str:
        return document_digest(
            {
                "repository": self.repository,
                "refs": list(self.refs),
                "paths": list(self.paths),
            }
        )

    def allows(self, *, repository: str, ref: str, path: str) -> bool:
        if self.repository != repository:
            return False
        if ref not in self.refs:
            return False
        return any(path_matches_pattern(pattern, path) for pattern in self.paths)


@dataclass(frozen=True, slots=True)
class ProjectRegistrationV1:
    project_profile_id: str
    entitlement_template_id: str
    repository_scopes: tuple[RepositoryScope, ...]
    slack_destination_id: str
    notion_destination_id: str
    n8n_workflow_ids: tuple[str, ...]
    budget_task_usd: float
    budget_daily_usd: float
    state_namespace: str
    managed_private_source: bool
    owner_policy_digest: str
    registration_generation: int
    owner_signature_principal: str
    owner_signature_digest: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.project_profile_id):
            raise EntitlementContractError("project_profile_id is malformed")
        if self.entitlement_template_id not in PROFILE_TEMPLATE_CAPABILITIES:
            raise EntitlementContractError("unknown profile entitlement template")
        if self.entitlement_template_id == SHARED_INTEGRATIONS_TEMPLATE_ID:
            raise EntitlementContractError(
                "profile registration must name a profile template, not the shared layer"
            )
        if not self.repository_scopes:
            raise EntitlementContractError("repository scopes must not be empty")
        if not _IDENTIFIER.fullmatch(self.slack_destination_id):
            raise EntitlementContractError("slack destination id is malformed")
        if not _IDENTIFIER.fullmatch(self.notion_destination_id):
            raise EntitlementContractError("notion destination id is malformed")
        if not self.n8n_workflow_ids:
            raise EntitlementContractError("n8n workflow ids must not be empty")
        for workflow_id in self.n8n_workflow_ids:
            if not _IDENTIFIER.fullmatch(workflow_id):
                raise EntitlementContractError("n8n workflow id is malformed")
        if not _IDENTIFIER.fullmatch(self.state_namespace):
            raise EntitlementContractError("state namespace is malformed")
        if self.registration_generation < 1:
            raise EntitlementContractError("registration generation must be positive")
        if not _SHA256.fullmatch(self.owner_policy_digest):
            raise EntitlementContractError("owner_policy_digest must be sha256")
        if not _IDENTIFIER.fullmatch(self.owner_signature_principal):
            raise EntitlementContractError("owner signature principal is malformed")
        if not _SHA256.fullmatch(self.owner_signature_digest):
            raise EntitlementContractError("owner signature digest must be sha256")
        _require_finite_positive(self.budget_task_usd, "budget_task_usd")
        _require_finite_positive(self.budget_daily_usd, "budget_daily_usd")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_REGISTRATION_VERSION,
            "project_profile_id": self.project_profile_id,
            "entitlement_template_id": self.entitlement_template_id,
            "repository_scopes": [
                {
                    "repository": scope.repository,
                    "refs": list(scope.refs),
                    "paths": list(scope.paths),
                }
                for scope in self.repository_scopes
            ],
            "slack_destination_id": self.slack_destination_id,
            "notion_destination_id": self.notion_destination_id,
            "n8n_workflow_ids": list(self.n8n_workflow_ids),
            "budget_task_usd": self.budget_task_usd,
            "budget_daily_usd": self.budget_daily_usd,
            "state_namespace": self.state_namespace,
            "managed_private_source": self.managed_private_source,
            "owner_policy_digest": self.owner_policy_digest,
            "registration_generation": self.registration_generation,
            "owner_signature_principal": self.owner_signature_principal,
        }

    def to_document(self) -> dict[str, Any]:
        document = dict(self.signing_document())
        document["owner_signature_digest"] = self.owner_signature_digest
        return document

    @property
    def digest(self) -> str:
        return document_digest(self.signing_document())

    def verify_owner_signature(self, *, owner_authority_secret: bytes) -> None:
        verify_signature(
            principal=self.owner_signature_principal,
            signature_digest=self.owner_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=owner_authority_secret,
        )


@dataclass(frozen=True, slots=True)
class SignedEntitlementTemplateV1:
    profile_template_id: str
    owner_policy_digest: str
    owner_signature_principal: str
    owner_signature_digest: str

    def __post_init__(self) -> None:
        if self.profile_template_id not in PROFILE_TEMPLATE_CAPABILITIES:
            raise EntitlementContractError("unknown profile entitlement template")
        if not _SHA256.fullmatch(self.owner_policy_digest):
            raise EntitlementContractError("owner_policy_digest must be sha256")
        if not _IDENTIFIER.fullmatch(self.owner_signature_principal):
            raise EntitlementContractError("owner signature principal is malformed")
        if not _SHA256.fullmatch(self.owner_signature_digest):
            raise EntitlementContractError("owner signature digest must be sha256")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": SIGNED_TEMPLATE_VERSION,
            "shared_template_id": SHARED_INTEGRATIONS_TEMPLATE_ID,
            "profile_template_id": self.profile_template_id,
            "template_digest": composed_template_digest(self.profile_template_id),
            "owner_policy_digest": self.owner_policy_digest,
            "owner_signature_principal": self.owner_signature_principal,
        }

    def verify_owner_signature(self, *, owner_authority_secret: bytes) -> None:
        verify_signature(
            principal=self.owner_signature_principal,
            signature_digest=self.owner_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=owner_authority_secret,
        )

    def to_document(self) -> dict[str, Any]:
        document = self.signing_document()
        document["owner_signature_digest"] = self.owner_signature_digest
        return document


@dataclass(frozen=True, slots=True)
class ManifestEntryV1:
    path: str
    artifact_digest: str
    size_bytes: int

    def __post_init__(self) -> None:
        normalize_posix_path(self.path)
        if not _SHA256.fullmatch(self.artifact_digest):
            raise EntitlementContractError("manifest artifact_digest must be sha256")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise EntitlementContractError("manifest size_bytes must be an integer")
        if self.size_bytes < 0:
            raise EntitlementContractError("manifest size_bytes must not be negative")

    def to_document(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "artifact_digest": self.artifact_digest,
            "size_bytes": self.size_bytes,
        }


def canonical_manifest_bytes(entries: tuple[ManifestEntryV1, ...]) -> bytes:
    if not entries:
        raise EntitlementContractError("manifest entries must not be empty")
    if tuple(sorted(entry.path for entry in entries)) != tuple(
        entry.path for entry in entries
    ):
        raise EntitlementContractError("manifest entries must be sorted by path")
    if len({entry.path for entry in entries}) != len(entries):
        raise EntitlementContractError("manifest paths must be unique")
    framed = bytearray(b"EntitlementManifest/v1\0")
    for entry in entries:
        for value in (
            entry.path.encode("utf-8"),
            entry.artifact_digest.encode("ascii"),
            str(entry.size_bytes).encode("ascii"),
        ):
            framed.extend(struct.pack(">Q", len(value)))
            framed.extend(value)
    return bytes(framed)


def manifest_digest(entries: tuple[ManifestEntryV1, ...]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(entries)).hexdigest()


@dataclass(frozen=True, slots=True)
class InstallAttestationV1:
    subject_kind: SubjectKind
    subject_id: str
    provider_id: str
    project_profile_id: str
    installation_id: str
    source_commit_sha: str
    source_tree_sha: str
    manifest_entries: tuple[ManifestEntryV1, ...]
    manifest_digest: str
    artifact_digest: str
    build_evidence_digest: str
    test_evidence_digest: str
    registration_generation: int
    template_digest: str
    owner_policy_digest: str
    verifier_principal: str
    verifier_signature_digest: str
    issued_at: datetime
    expires_at: datetime
    nonce_digest: str
    attestation_id: str

    def __post_init__(self) -> None:
        for name in ("subject_id", "provider_id", "project_profile_id", "installation_id"):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if not _GIT_SHA1.fullmatch(self.source_commit_sha):
            raise EntitlementContractError("source_commit_sha must be git sha1")
        if not _GIT_SHA1.fullmatch(self.source_tree_sha):
            raise EntitlementContractError("source_tree_sha must be git tree sha1")
        for digest_name in (
            "manifest_digest",
            "artifact_digest",
            "build_evidence_digest",
            "test_evidence_digest",
            "template_digest",
            "owner_policy_digest",
            "nonce_digest",
        ):
            if not _SHA256.fullmatch(getattr(self, digest_name)):
                raise EntitlementContractError(f"{digest_name} must be sha256")
        if self.manifest_digest != manifest_digest(self.manifest_entries):
            raise EntitlementContractError("manifest_digest does not match canonical manifest")
        if not _IDENTIFIER.fullmatch(self.verifier_principal):
            raise EntitlementContractError("verifier_principal is malformed")
        if not _SHA256.fullmatch(self.verifier_signature_digest):
            raise EntitlementContractError("verifier_signature_digest must be sha256")
        if not _IDENTIFIER.fullmatch(self.attestation_id):
            raise EntitlementContractError("attestation_id is malformed")
        if self.registration_generation < 1:
            raise EntitlementContractError("registration generation must be positive")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise EntitlementContractError("expires_at must be later than issued_at")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": INSTALL_ATTESTATION_VERSION,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "provider_id": self.provider_id,
            "project_profile_id": self.project_profile_id,
            "installation_id": self.installation_id,
            "source_commit_sha": self.source_commit_sha,
            "source_tree_sha": self.source_tree_sha,
            "manifest_entries": [entry.to_document() for entry in self.manifest_entries],
            "manifest_digest": self.manifest_digest,
            "artifact_digest": self.artifact_digest,
            "build_evidence_digest": self.build_evidence_digest,
            "test_evidence_digest": self.test_evidence_digest,
            "registration_generation": self.registration_generation,
            "template_digest": self.template_digest,
            "owner_policy_digest": self.owner_policy_digest,
            "verifier_principal": self.verifier_principal,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce_digest": self.nonce_digest,
            "attestation_id": self.attestation_id,
        }

    def to_document(self) -> dict[str, Any]:
        document = dict(self.signing_document())
        document["verifier_signature_digest"] = self.verifier_signature_digest
        return document

    @property
    def digest(self) -> str:
        return document_digest(self.signing_document())

    def verify_signature(self, *, trusted_verifier_secret: bytes) -> None:
        verify_signature(
            principal=self.verifier_principal,
            signature_digest=self.verifier_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=trusted_verifier_secret,
        )


@dataclass(frozen=True, slots=True)
class EntitlementEventV1:
    event_type: EntitlementEventType
    installation_id: str
    entitlement_generation: int
    expected_generation: int | None
    actor_id: str
    idempotency_key_digest: str
    scope_digest: str
    evidence_binding_digest: str
    event_id: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.installation_id):
            raise EntitlementContractError("installation_id is malformed")
        if self.entitlement_generation < 0:
            raise EntitlementContractError("entitlement generation must not be negative")
        if self.entitlement_generation == 0 and not (
            self.event_type == EntitlementEventType.REVOKE
            and self.expected_generation is None
        ):
            raise EntitlementContractError(
                "generation zero is reserved for preemptive revocation"
            )
        if self.expected_generation is not None and self.expected_generation < 1:
            raise EntitlementContractError("expected generation must be positive")
        if not _IDENTIFIER.fullmatch(self.actor_id):
            raise EntitlementContractError("actor_id is malformed")
        for name in (
            "idempotency_key_digest",
            "scope_digest",
            "evidence_binding_digest",
        ):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} must be sha256")
        if not _IDENTIFIER.fullmatch(self.event_id):
            raise EntitlementContractError("event_id is malformed")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": ENTITLEMENT_EVENT_VERSION,
            "event_type": self.event_type.value,
            "installation_id": self.installation_id,
            "entitlement_generation": self.entitlement_generation,
            "expected_generation": self.expected_generation,
            "actor_id": self.actor_id,
            "idempotency_key_digest": self.idempotency_key_digest,
            "scope_digest": self.scope_digest,
            "evidence_binding_digest": self.evidence_binding_digest,
            "event_id": self.event_id,
        }


@dataclass(frozen=True, slots=True)
class OwnerNoticeV1:
    notice_id: str
    installation_id: str
    entitlement_generation: int
    delivery_state: OwnerNoticeDeliveryState
    project_profile_id: str
    subject_kind: SubjectKind
    subject_id: str
    template_id: str
    authority_bearing_capabilities: tuple[str, ...]
    build_digest: str
    pending_since: datetime
    delivery_proof_digest: str | None = None
    ack_proof_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("notice_id", "installation_id", "project_profile_id", "subject_id"):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if self.entitlement_generation < 1:
            raise EntitlementContractError("entitlement generation must be positive")
        if self.template_id not in PROFILE_TEMPLATE_CAPABILITIES:
            raise EntitlementContractError("unknown notice entitlement template")
        if not _SHA256.fullmatch(self.build_digest):
            raise EntitlementContractError("build_digest must be sha256")
        _require_utc(self.pending_since, "pending_since")
        if self.delivery_proof_digest is not None and not _SHA256.fullmatch(
            self.delivery_proof_digest
        ):
            raise EntitlementContractError("delivery_proof_digest must be sha256")
        if self.ack_proof_digest is not None and not _SHA256.fullmatch(
            self.ack_proof_digest
        ):
            raise EntitlementContractError("ack_proof_digest must be sha256")

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": OWNER_NOTICE_VERSION,
            "notice_id": self.notice_id,
            "installation_id": self.installation_id,
            "entitlement_generation": self.entitlement_generation,
            "delivery_state": self.delivery_state.value,
            "project_profile_id": self.project_profile_id,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "template_id": self.template_id,
            "authority_bearing_capabilities": list(self.authority_bearing_capabilities),
            "build_digest": self.build_digest,
            "pending_since": self.pending_since.isoformat(),
            "delivery_proof_digest": self.delivery_proof_digest,
            "ack_proof_digest": self.ack_proof_digest,
        }


@dataclass(frozen=True, slots=True)
class SlackDestinationHealthProofV1:
    destination_id: str
    workspace_id: str
    channel_id: str
    account_id: str
    authenticated: bool
    delivery_probe_digest: str
    verifier_principal: str
    issued_at: datetime
    expires_at: datetime
    nonce_digest: str
    verifier_signature_digest: str

    def __post_init__(self) -> None:
        for name in (
            "destination_id",
            "workspace_id",
            "channel_id",
            "account_id",
            "verifier_principal",
        ):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if self.authenticated is not True:
            raise EntitlementContractError("Slack health proof must be authenticated")
        for name in ("delivery_probe_digest", "nonce_digest", "verifier_signature_digest"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} must be sha256")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise EntitlementContractError("Slack health proof expiry must follow issuance")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": SLACK_DESTINATION_PROOF_VERSION,
            "destination_id": self.destination_id,
            "workspace_id": self.workspace_id,
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "authenticated": self.authenticated,
            "delivery_probe_digest": self.delivery_probe_digest,
            "verifier_principal": self.verifier_principal,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce_digest": self.nonce_digest,
        }

    def to_document(self) -> dict[str, Any]:
        document = self.signing_document()
        document["verifier_signature_digest"] = self.verifier_signature_digest
        return document

    @property
    def digest(self) -> str:
        return document_digest(self.signing_document())

    def verify_signature(self, *, trusted_verifier_secret: bytes) -> None:
        verify_signature(
            principal=self.verifier_principal,
            signature_digest=self.verifier_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=trusted_verifier_secret,
        )


@dataclass(frozen=True, slots=True)
class EntitlementOwnerActionV1:
    operation: str
    actor_id: str
    installation_id: str
    expected_generation: int | None
    idempotency_key_digest: str
    request_digest: str
    nonce_digest: str
    issued_at: datetime
    expires_at: datetime
    owner_signature_digest: str

    def __post_init__(self) -> None:
        for name in ("operation", "actor_id", "installation_id"):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if self.expected_generation is not None and self.expected_generation < 0:
            raise EntitlementContractError("expected_generation must not be negative")
        for name in (
            "idempotency_key_digest",
            "request_digest",
            "nonce_digest",
            "owner_signature_digest",
        ):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} must be sha256")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise EntitlementContractError("owner action expiry must follow issuance")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": OWNER_ACTION_VERSION,
            "operation": self.operation,
            "actor_id": self.actor_id,
            "installation_id": self.installation_id,
            "expected_generation": self.expected_generation,
            "idempotency_key_digest": self.idempotency_key_digest,
            "request_digest": self.request_digest,
            "nonce_digest": self.nonce_digest,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def to_document(self) -> dict[str, Any]:
        document = self.signing_document()
        document["owner_signature_digest"] = self.owner_signature_digest
        return document

    def verify_signature(self, *, owner_authority_secret: bytes) -> None:
        verify_signature(
            principal=self.actor_id,
            signature_digest=self.owner_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=owner_authority_secret,
        )

    def to_document(self) -> dict[str, Any]:
        document = self.signing_document()
        document["owner_signature_digest"] = self.owner_signature_digest
        return document


@dataclass(frozen=True, slots=True)
class NoticeRouteProofV1:
    notice_id: str
    installation_id: str
    entitlement_generation: int
    destination_id: str
    outcome: str
    evidence_digest: str
    verifier_principal: str
    issued_at: datetime
    expires_at: datetime
    nonce_digest: str
    verifier_signature_digest: str

    def __post_init__(self) -> None:
        for name in ("notice_id", "installation_id", "destination_id", "verifier_principal"):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if self.entitlement_generation < 1:
            raise EntitlementContractError("entitlement_generation must be positive")
        if self.outcome not in {"DELIVERED", "FAILED"}:
            raise EntitlementContractError("notice route outcome is unknown")
        for name in ("evidence_digest", "nonce_digest", "verifier_signature_digest"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} must be sha256")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise EntitlementContractError("notice route proof expiry must follow issuance")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": NOTICE_ROUTE_PROOF_VERSION,
            "notice_id": self.notice_id,
            "installation_id": self.installation_id,
            "entitlement_generation": self.entitlement_generation,
            "destination_id": self.destination_id,
            "outcome": self.outcome,
            "evidence_digest": self.evidence_digest,
            "verifier_principal": self.verifier_principal,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce_digest": self.nonce_digest,
        }

    def to_document(self) -> dict[str, Any]:
        document = self.signing_document()
        document["verifier_signature_digest"] = self.verifier_signature_digest
        return document

    def verify_signature(self, *, trusted_verifier_secret: bytes) -> None:
        verify_signature(
            principal=self.verifier_principal,
            signature_digest=self.verifier_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=trusted_verifier_secret,
        )


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceProofV1:
    installation_id: str
    build_digest: str
    source_commit_sha: str
    source_tree_sha: str
    action_type: EntitlementActionType
    destination_id: str | None
    effect_digest: str
    verifier_principal: str
    issued_at: datetime
    expires_at: datetime
    nonce_digest: str
    verifier_signature_digest: str

    def __post_init__(self) -> None:
        for name in ("installation_id", "verifier_principal"):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if not _SHA256.fullmatch(self.build_digest) or not _SHA256.fullmatch(self.effect_digest):
            raise EntitlementContractError("runtime build/effect digest must be sha256")
        if not _GIT_SHA1.fullmatch(self.source_commit_sha) or not _GIT_SHA1.fullmatch(self.source_tree_sha):
            raise EntitlementContractError("runtime source commit/tree must be git sha1")
        if not isinstance(self.action_type, EntitlementActionType):
            raise EntitlementContractError("runtime action_type is unknown")
        if self.destination_id is not None and not _IDENTIFIER.fullmatch(self.destination_id):
            raise EntitlementContractError("runtime destination_id is malformed")
        for name in ("nonce_digest", "verifier_signature_digest"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} must be sha256")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise EntitlementContractError("runtime proof expiry must follow issuance")

    def signing_document(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_EVIDENCE_PROOF_VERSION,
            "installation_id": self.installation_id,
            "build_digest": self.build_digest,
            "source_commit_sha": self.source_commit_sha,
            "source_tree_sha": self.source_tree_sha,
            "action_type": self.action_type.value,
            "destination_id": self.destination_id,
            "effect_digest": self.effect_digest,
            "verifier_principal": self.verifier_principal,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce_digest": self.nonce_digest,
        }

    def to_document(self) -> dict[str, Any]:
        document = self.signing_document()
        document["verifier_signature_digest"] = self.verifier_signature_digest
        return document

    @property
    def digest(self) -> str:
        return document_digest(self.to_document())

    def verify_signature(self, *, trusted_verifier_secret: bytes) -> None:
        verify_signature(
            principal=self.verifier_principal,
            signature_digest=self.verifier_signature_digest,
            signing_payload=signing_bytes(self.signing_document()),
            secret=trusted_verifier_secret,
        )


@dataclass(frozen=True, slots=True)
class EntitlementActionBoundaryQuery:
    project_profile_id: str
    repository: str
    ref: str
    path: str
    provider_id: str
    subject_id: str
    installation_id: str
    current_build_digest: str
    action_type: EntitlementActionType
    destination_id: str | None
    effect_digest: str
    reserved_spend_usd: float
    runtime_proof: RuntimeEvidenceProofV1
    now: datetime

    def __post_init__(self) -> None:
        for name in (
            "project_profile_id",
            "provider_id",
            "subject_id",
            "installation_id",
        ):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if not _REPOSITORY.fullmatch(self.repository):
            raise EntitlementContractError("repository is malformed")
        if not _SAFE_REF.fullmatch(self.ref):
            raise EntitlementContractError("ref is malformed")
        normalize_posix_path(self.path)
        if not isinstance(self.action_type, EntitlementActionType):
            raise EntitlementContractError("action_type must be a known typed action")
        if self.destination_id is not None and not _IDENTIFIER.fullmatch(self.destination_id):
            raise EntitlementContractError("destination_id is malformed")
        if self.action_type in (
            EntitlementActionType.SLACK_WRITE,
            EntitlementActionType.NOTION_WRITE,
            EntitlementActionType.N8N_WRITE,
            EntitlementActionType.PROVIDER_DISPATCH,
        ) and self.destination_id is None:
            raise EntitlementContractError("destination_id is required for integration actions")
        if not _SHA256.fullmatch(self.effect_digest):
            raise EntitlementContractError("effect_digest must be sha256")
        if not _SHA256.fullmatch(self.current_build_digest):
            raise EntitlementContractError("current_build_digest must be sha256")
        if isinstance(self.reserved_spend_usd, bool) or not isinstance(
            self.reserved_spend_usd, (int, float)
        ) or not math.isfinite(float(self.reserved_spend_usd)) or self.reserved_spend_usd < 0:
            raise EntitlementContractError("reserved_spend_usd must be a finite non-negative number")
        if self.action_type in SPEND_BEARING_ACTIONS and self.reserved_spend_usd <= 0:
            raise EntitlementContractError("spend-bearing action requires an actual reservation")
        if self.action_type not in SPEND_BEARING_ACTIONS and self.reserved_spend_usd != 0:
            raise EntitlementContractError("non-spend action cannot carry a spend reservation")
        _require_utc(self.now, "now")
        if not isinstance(self.runtime_proof, RuntimeEvidenceProofV1):
            raise EntitlementContractError("runtime_proof is required")

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": ACTION_BOUNDARY_QUERY_VERSION,
            "project_profile_id": self.project_profile_id,
            "repository": self.repository,
            "ref": self.ref,
            "path": self.path,
            "provider_id": self.provider_id,
            "subject_id": self.subject_id,
            "installation_id": self.installation_id,
            "current_build_digest": self.current_build_digest,
            "action_type": self.action_type.value,
            "destination_id": self.destination_id,
            "effect_digest": self.effect_digest,
            "reserved_spend_usd": self.reserved_spend_usd,
            "runtime_proof": self.runtime_proof.to_document(),
            "now": self.now.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EntitlementActionBoundaryResult:
    permitted: bool
    entitlement_generation: int | None
    lifecycle_status: EntitlementLifecycleStatus | None
    notice_delivery_state: OwnerNoticeDeliveryState | None
    audit_event_ids: tuple[str, ...]
    denial_reason: str | None
    authorization_binding_digest: str | None = None


def _strict_document(raw: bytes, *, version: str, keys: frozenset[str]) -> dict[str, Any]:
    document = _decode_strict_json(raw, source=version)
    _require_document_keys(document, keys, version)
    if document.get("schema_version") != version:
        raise EntitlementContractError(f"{version} schema_version mismatch")
    return document


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    _require_type(value, list, name)
    if not all(isinstance(item, str) for item in value):
        raise EntitlementContractError(f"{name} must contain strings")
    return tuple(value)


def parse_project_registration_v1(raw: bytes) -> ProjectRegistrationV1:
    keys = frozenset(
        {
            "schema_version", "project_profile_id", "entitlement_template_id",
            "repository_scopes", "slack_destination_id", "notion_destination_id",
            "n8n_workflow_ids", "budget_task_usd", "budget_daily_usd",
            "state_namespace", "managed_private_source", "owner_policy_digest",
            "registration_generation", "owner_signature_principal",
            "owner_signature_digest",
        }
    )
    document = _strict_document(raw, version=PROJECT_REGISTRATION_VERSION, keys=keys)
    _require_type(document["repository_scopes"], list, "repository_scopes")
    scopes: list[RepositoryScope] = []
    for raw_scope in document["repository_scopes"]:
        _require_type(raw_scope, dict, "repository_scope")
        _require_document_keys(
            raw_scope, frozenset({"repository", "refs", "paths"}), "RepositoryScope"
        )
        scopes.append(
            RepositoryScope(
                repository=_require_type(raw_scope["repository"], str, "repository"),
                refs=_string_tuple(raw_scope["refs"], "refs"),
                paths=_string_tuple(raw_scope["paths"], "paths"),
            )
        )
    return ProjectRegistrationV1(
        project_profile_id=_require_type(document["project_profile_id"], str, "project_profile_id"),
        entitlement_template_id=_require_type(document["entitlement_template_id"], str, "entitlement_template_id"),
        repository_scopes=tuple(scopes),
        slack_destination_id=_require_type(document["slack_destination_id"], str, "slack_destination_id"),
        notion_destination_id=_require_type(document["notion_destination_id"], str, "notion_destination_id"),
        n8n_workflow_ids=_string_tuple(document["n8n_workflow_ids"], "n8n_workflow_ids"),
        budget_task_usd=_require_type(document["budget_task_usd"], (int, float), "budget_task_usd"),
        budget_daily_usd=_require_type(document["budget_daily_usd"], (int, float), "budget_daily_usd"),
        state_namespace=_require_type(document["state_namespace"], str, "state_namespace"),
        managed_private_source=_require_type(document["managed_private_source"], bool, "managed_private_source"),
        owner_policy_digest=_require_type(document["owner_policy_digest"], str, "owner_policy_digest"),
        registration_generation=_require_type(document["registration_generation"], int, "registration_generation"),
        owner_signature_principal=_require_type(document["owner_signature_principal"], str, "owner_signature_principal"),
        owner_signature_digest=_require_type(document["owner_signature_digest"], str, "owner_signature_digest"),
    )


def parse_signed_entitlement_template_v1(raw: bytes) -> SignedEntitlementTemplateV1:
    keys = frozenset(
        {"schema_version", "shared_template_id", "profile_template_id", "template_digest",
         "owner_policy_digest", "owner_signature_principal", "owner_signature_digest"}
    )
    document = _strict_document(raw, version=SIGNED_TEMPLATE_VERSION, keys=keys)
    profile = _require_type(document["profile_template_id"], str, "profile_template_id")
    if document["shared_template_id"] != SHARED_INTEGRATIONS_TEMPLATE_ID:
        raise EntitlementContractError("shared_template_id mismatch")
    if document["template_digest"] != composed_template_digest(profile):
        raise EntitlementContractError("template_digest mismatch")
    return SignedEntitlementTemplateV1(
        profile_template_id=profile,
        owner_policy_digest=_require_type(document["owner_policy_digest"], str, "owner_policy_digest"),
        owner_signature_principal=_require_type(document["owner_signature_principal"], str, "owner_signature_principal"),
        owner_signature_digest=_require_type(document["owner_signature_digest"], str, "owner_signature_digest"),
    )


def parse_install_attestation_v1(raw: bytes) -> InstallAttestationV1:
    keys = frozenset(
        {"schema_version", "subject_kind", "subject_id", "provider_id", "project_profile_id",
         "installation_id", "source_commit_sha", "source_tree_sha", "manifest_entries",
         "manifest_digest", "artifact_digest", "build_evidence_digest", "test_evidence_digest",
         "registration_generation", "template_digest", "owner_policy_digest", "verifier_principal",
         "verifier_signature_digest", "issued_at", "expires_at", "nonce_digest", "attestation_id"}
    )
    document = _strict_document(raw, version=INSTALL_ATTESTATION_VERSION, keys=keys)
    _require_type(document["manifest_entries"], list, "manifest_entries")
    entries: list[ManifestEntryV1] = []
    for raw_entry in document["manifest_entries"]:
        _require_type(raw_entry, dict, "manifest_entry")
        _require_document_keys(
            raw_entry, frozenset({"path", "artifact_digest", "size_bytes"}), "ManifestEntryV1"
        )
        entries.append(ManifestEntryV1(
            path=_require_type(raw_entry["path"], str, "path"),
            artifact_digest=_require_type(raw_entry["artifact_digest"], str, "artifact_digest"),
            size_bytes=_require_type(raw_entry["size_bytes"], int, "size_bytes"),
        ))
    try:
        subject_kind = SubjectKind(_require_type(document["subject_kind"], str, "subject_kind"))
    except ValueError as exc:
        raise EntitlementContractError("subject_kind is unknown") from exc
    return InstallAttestationV1(
        subject_kind=subject_kind,
        subject_id=_require_type(document["subject_id"], str, "subject_id"),
        provider_id=_require_type(document["provider_id"], str, "provider_id"),
        project_profile_id=_require_type(document["project_profile_id"], str, "project_profile_id"),
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        source_commit_sha=_require_type(document["source_commit_sha"], str, "source_commit_sha"),
        source_tree_sha=_require_type(document["source_tree_sha"], str, "source_tree_sha"),
        manifest_entries=tuple(entries),
        manifest_digest=_require_type(document["manifest_digest"], str, "manifest_digest"),
        artifact_digest=_require_type(document["artifact_digest"], str, "artifact_digest"),
        build_evidence_digest=_require_type(document["build_evidence_digest"], str, "build_evidence_digest"),
        test_evidence_digest=_require_type(document["test_evidence_digest"], str, "test_evidence_digest"),
        registration_generation=_require_type(document["registration_generation"], int, "registration_generation"),
        template_digest=_require_type(document["template_digest"], str, "template_digest"),
        owner_policy_digest=_require_type(document["owner_policy_digest"], str, "owner_policy_digest"),
        verifier_principal=_require_type(document["verifier_principal"], str, "verifier_principal"),
        verifier_signature_digest=_require_type(document["verifier_signature_digest"], str, "verifier_signature_digest"),
        issued_at=_parse_utc(document["issued_at"], "issued_at"),
        expires_at=_parse_utc(document["expires_at"], "expires_at"),
        nonce_digest=_require_type(document["nonce_digest"], str, "nonce_digest"),
        attestation_id=_require_type(document["attestation_id"], str, "attestation_id"),
    )


def parse_entitlement_event_v1(raw: bytes) -> EntitlementEventV1:
    keys = frozenset({"schema_version", "event_type", "installation_id", "entitlement_generation",
                      "expected_generation", "actor_id", "idempotency_key_digest", "scope_digest",
                      "evidence_binding_digest", "event_id"})
    document = _strict_document(raw, version=ENTITLEMENT_EVENT_VERSION, keys=keys)
    try:
        event_type = EntitlementEventType(_require_type(document["event_type"], str, "event_type"))
    except ValueError as exc:
        raise EntitlementContractError("event_type is unknown") from exc
    expected = document["expected_generation"]
    if expected is not None:
        _require_type(expected, int, "expected_generation")
    return EntitlementEventV1(
        event_type=event_type,
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        entitlement_generation=_require_type(document["entitlement_generation"], int, "entitlement_generation"),
        expected_generation=expected,
        actor_id=_require_type(document["actor_id"], str, "actor_id"),
        idempotency_key_digest=_require_type(document["idempotency_key_digest"], str, "idempotency_key_digest"),
        scope_digest=_require_type(document["scope_digest"], str, "scope_digest"),
        evidence_binding_digest=_require_type(document["evidence_binding_digest"], str, "evidence_binding_digest"),
        event_id=_require_type(document["event_id"], str, "event_id"),
    )


def parse_owner_notice_v1(raw: bytes) -> OwnerNoticeV1:
    keys = frozenset({"schema_version", "notice_id", "installation_id", "entitlement_generation",
                      "delivery_state", "project_profile_id", "subject_kind", "subject_id", "template_id",
                      "authority_bearing_capabilities", "build_digest", "pending_since",
                      "delivery_proof_digest", "ack_proof_digest"})
    document = _strict_document(raw, version=OWNER_NOTICE_VERSION, keys=keys)
    try:
        delivery = OwnerNoticeDeliveryState(_require_type(document["delivery_state"], str, "delivery_state"))
        subject = SubjectKind(_require_type(document["subject_kind"], str, "subject_kind"))
    except ValueError as exc:
        raise EntitlementContractError("notice enum value is unknown") from exc
    proof = document["delivery_proof_digest"]
    if proof is not None:
        _require_type(proof, str, "delivery_proof_digest")
    ack_proof = document["ack_proof_digest"]
    if ack_proof is not None:
        _require_type(ack_proof, str, "ack_proof_digest")
    return OwnerNoticeV1(
        notice_id=_require_type(document["notice_id"], str, "notice_id"),
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        entitlement_generation=_require_type(document["entitlement_generation"], int, "entitlement_generation"),
        delivery_state=delivery,
        project_profile_id=_require_type(document["project_profile_id"], str, "project_profile_id"),
        subject_kind=subject,
        subject_id=_require_type(document["subject_id"], str, "subject_id"),
        template_id=_require_type(document["template_id"], str, "template_id"),
        authority_bearing_capabilities=_string_tuple(document["authority_bearing_capabilities"], "authority_bearing_capabilities"),
        build_digest=_require_type(document["build_digest"], str, "build_digest"),
        pending_since=_parse_utc(document["pending_since"], "pending_since"),
        delivery_proof_digest=proof,
        ack_proof_digest=ack_proof,
    )


def parse_slack_destination_health_proof_v1(raw: bytes) -> SlackDestinationHealthProofV1:
    keys = frozenset({"schema_version", "destination_id", "workspace_id", "channel_id", "account_id",
                      "authenticated", "delivery_probe_digest", "verifier_principal", "issued_at",
                      "expires_at", "nonce_digest", "verifier_signature_digest"})
    document = _strict_document(raw, version=SLACK_DESTINATION_PROOF_VERSION, keys=keys)
    return SlackDestinationHealthProofV1(
        destination_id=_require_type(document["destination_id"], str, "destination_id"),
        workspace_id=_require_type(document["workspace_id"], str, "workspace_id"),
        channel_id=_require_type(document["channel_id"], str, "channel_id"),
        account_id=_require_type(document["account_id"], str, "account_id"),
        authenticated=_require_type(document["authenticated"], bool, "authenticated"),
        delivery_probe_digest=_require_type(document["delivery_probe_digest"], str, "delivery_probe_digest"),
        verifier_principal=_require_type(document["verifier_principal"], str, "verifier_principal"),
        issued_at=_parse_utc(document["issued_at"], "issued_at"),
        expires_at=_parse_utc(document["expires_at"], "expires_at"),
        nonce_digest=_require_type(document["nonce_digest"], str, "nonce_digest"),
        verifier_signature_digest=_require_type(document["verifier_signature_digest"], str, "verifier_signature_digest"),
    )


def parse_entitlement_owner_action_v1(raw: bytes) -> EntitlementOwnerActionV1:
    keys = frozenset({"schema_version", "operation", "actor_id", "installation_id",
                      "expected_generation", "idempotency_key_digest", "request_digest", "nonce_digest",
                      "issued_at", "expires_at", "owner_signature_digest"})
    document = _strict_document(raw, version=OWNER_ACTION_VERSION, keys=keys)
    expected = document["expected_generation"]
    if expected is not None:
        _require_type(expected, int, "expected_generation")
    return EntitlementOwnerActionV1(
        operation=_require_type(document["operation"], str, "operation"),
        actor_id=_require_type(document["actor_id"], str, "actor_id"),
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        expected_generation=expected,
        idempotency_key_digest=_require_type(document["idempotency_key_digest"], str, "idempotency_key_digest"),
        request_digest=_require_type(document["request_digest"], str, "request_digest"),
        nonce_digest=_require_type(document["nonce_digest"], str, "nonce_digest"),
        issued_at=_parse_utc(document["issued_at"], "issued_at"),
        expires_at=_parse_utc(document["expires_at"], "expires_at"),
        owner_signature_digest=_require_type(document["owner_signature_digest"], str, "owner_signature_digest"),
    )


def parse_entitlement_action_boundary_query_v1(raw: bytes) -> EntitlementActionBoundaryQuery:
    keys = frozenset({"schema_version", "project_profile_id", "repository", "ref", "path", "provider_id",
                      "subject_id", "installation_id", "current_build_digest", "action_type",
                      "destination_id", "effect_digest", "reserved_spend_usd", "now"})
    keys = keys | {"runtime_proof"}
    document = _strict_document(raw, version=ACTION_BOUNDARY_QUERY_VERSION, keys=keys)
    try:
        action = EntitlementActionType(_require_type(document["action_type"], str, "action_type"))
    except ValueError as exc:
        raise EntitlementContractError("action_type is unknown") from exc
    destination = document["destination_id"]
    if destination is not None:
        _require_type(destination, str, "destination_id")
    return EntitlementActionBoundaryQuery(
        project_profile_id=_require_type(document["project_profile_id"], str, "project_profile_id"),
        repository=_require_type(document["repository"], str, "repository"),
        ref=_require_type(document["ref"], str, "ref"),
        path=_require_type(document["path"], str, "path"),
        provider_id=_require_type(document["provider_id"], str, "provider_id"),
        subject_id=_require_type(document["subject_id"], str, "subject_id"),
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        current_build_digest=_require_type(document["current_build_digest"], str, "current_build_digest"),
        action_type=action,
        destination_id=destination,
        effect_digest=_require_type(document["effect_digest"], str, "effect_digest"),
        reserved_spend_usd=_require_type(document["reserved_spend_usd"], (int, float), "reserved_spend_usd"),
        runtime_proof=parse_runtime_evidence_proof_v1(
            canonical_json_bytes(
                _require_type(document["runtime_proof"], dict, "runtime_proof")
            )
        ),
        now=_parse_utc(document["now"], "now"),
    )


def parse_notice_route_proof_v1(raw: bytes) -> NoticeRouteProofV1:
    keys = frozenset({"schema_version", "notice_id", "installation_id", "entitlement_generation",
                      "destination_id", "outcome", "evidence_digest", "verifier_principal",
                      "issued_at", "expires_at", "nonce_digest", "verifier_signature_digest"})
    document = _strict_document(raw, version=NOTICE_ROUTE_PROOF_VERSION, keys=keys)
    return NoticeRouteProofV1(
        notice_id=_require_type(document["notice_id"], str, "notice_id"),
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        entitlement_generation=_require_type(document["entitlement_generation"], int, "entitlement_generation"),
        destination_id=_require_type(document["destination_id"], str, "destination_id"),
        outcome=_require_type(document["outcome"], str, "outcome"),
        evidence_digest=_require_type(document["evidence_digest"], str, "evidence_digest"),
        verifier_principal=_require_type(document["verifier_principal"], str, "verifier_principal"),
        issued_at=_parse_utc(document["issued_at"], "issued_at"),
        expires_at=_parse_utc(document["expires_at"], "expires_at"),
        nonce_digest=_require_type(document["nonce_digest"], str, "nonce_digest"),
        verifier_signature_digest=_require_type(document["verifier_signature_digest"], str, "verifier_signature_digest"),
    )


def parse_runtime_evidence_proof_v1(raw: bytes) -> RuntimeEvidenceProofV1:
    keys = frozenset({"schema_version", "installation_id", "build_digest", "source_commit_sha",
                      "source_tree_sha", "action_type", "destination_id", "effect_digest",
                      "verifier_principal", "issued_at", "expires_at", "nonce_digest",
                      "verifier_signature_digest"})
    document = _strict_document(raw, version=RUNTIME_EVIDENCE_PROOF_VERSION, keys=keys)
    try:
        action = EntitlementActionType(_require_type(document["action_type"], str, "action_type"))
    except ValueError as exc:
        raise EntitlementContractError("runtime action_type is unknown") from exc
    destination = document["destination_id"]
    if destination is not None:
        _require_type(destination, str, "destination_id")
    return RuntimeEvidenceProofV1(
        installation_id=_require_type(document["installation_id"], str, "installation_id"),
        build_digest=_require_type(document["build_digest"], str, "build_digest"),
        source_commit_sha=_require_type(document["source_commit_sha"], str, "source_commit_sha"),
        source_tree_sha=_require_type(document["source_tree_sha"], str, "source_tree_sha"),
        action_type=action,
        destination_id=destination,
        effect_digest=_require_type(document["effect_digest"], str, "effect_digest"),
        verifier_principal=_require_type(document["verifier_principal"], str, "verifier_principal"),
        issued_at=_parse_utc(document["issued_at"], "issued_at"),
        expires_at=_parse_utc(document["expires_at"], "expires_at"),
        nonce_digest=_require_type(document["nonce_digest"], str, "nonce_digest"),
        verifier_signature_digest=_require_type(document["verifier_signature_digest"], str, "verifier_signature_digest"),
    )

# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:20 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:00 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]]
