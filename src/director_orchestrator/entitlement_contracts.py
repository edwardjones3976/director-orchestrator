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
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

PROJECT_REGISTRATION_VERSION = "ProjectRegistration/v1"
INSTALL_ATTESTATION_VERSION = "InstallAttestation/v1"
ENTITLEMENT_EVENT_VERSION = "EntitlementEvent/v1"
OWNER_NOTICE_VERSION = "OwnerNotice/v1"
SIGNED_TEMPLATE_VERSION = "SignedEntitlementTemplate/v1"

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
    except (json.JSONDecodeError, TypeError, ValueError, EntitlementContractError) as exc:
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


@dataclass(frozen=True, slots=True)
class InstallAttestationV1:
    subject_kind: SubjectKind
    subject_id: str
    provider_id: str
    project_profile_id: str
    installation_id: str
    source_commit_sha: str
    source_tree_sha: str
    manifest_digest: str
    artifact_digest: str
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
            "template_digest",
            "owner_policy_digest",
            "nonce_digest",
        ):
            if not _SHA256.fullmatch(getattr(self, digest_name)):
                raise EntitlementContractError(f"{digest_name} must be sha256")
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
            "manifest_digest": self.manifest_digest,
            "artifact_digest": self.artifact_digest,
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
        }


@dataclass(frozen=True, slots=True)
class EntitlementActionBoundaryQuery:
    project_profile_id: str
    repository: str
    ref: str
    path: str
    provider_id: str
    subject_id: str
    installation_id: str
    required_capability: str
    current_build_digest: str
    action: str
    budget_task_usd: float
    now: datetime

    def __post_init__(self) -> None:
        for name in (
            "project_profile_id",
            "provider_id",
            "subject_id",
            "installation_id",
            "action",
        ):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise EntitlementContractError(f"{name} is malformed")
        if not _REPOSITORY.fullmatch(self.repository):
            raise EntitlementContractError("repository is malformed")
        if not _SAFE_REF.fullmatch(self.ref):
            raise EntitlementContractError("ref is malformed")
        normalize_posix_path(self.path)
        if not _CAPABILITY.fullmatch(self.required_capability):
            raise EntitlementContractError("required_capability is malformed")
        if not _SHA256.fullmatch(self.current_build_digest):
            raise EntitlementContractError("current_build_digest must be sha256")
        _require_finite_positive(self.budget_task_usd, "budget_task_usd")
        _require_utc(self.now, "now")


@dataclass(frozen=True, slots=True)
class EntitlementActionBoundaryResult:
    permitted: bool
    entitlement_generation: int | None
    lifecycle_status: EntitlementLifecycleStatus | None
    notice_delivery_state: OwnerNoticeDeliveryState | None
    audit_event_ids: tuple[str, ...]
    denial_reason: str | None

# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:20 UTC | END]]
# [[Cursor Agent | Composer 2.5 | 2026-09-04 | 20:00 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:34 UTC | END]]
