# [[OpenAI Codex | GPT-6 | 2026-09-06 | 08:24 UTC | BEGIN]]
"""Dormant v2 acceptance verification; no issuer, enrollment, journal or activation.

Registry and expected context are owner-controlled configuration, never packet
inputs. A signature authenticates a provisioned issuer; that issuer must directly
verify its native provider session or trusted CI evidence before signing. Separate
OS/service key custody is a deployment prerequisite, not proved by caller labels.
The caller must atomically insert challenge and acceptance in one durable record.
This pure verifier does not consume challenges or claim replay-safe activation.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .primary_activation_evidence import ActivationIdentity

VERSION = "director-provider-acceptance/v2"
ROLES = frozenset({"security", "independent_review", "ci"})
DOMAIN = b"director-provider-acceptance/v2\0"


class ProviderAcceptanceError(ValueError):
    pass


def canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderAcceptanceError("invalid canonical evidence") from exc


def _digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProviderAcceptanceError("invalid digest")


def _text(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}", value) is None:
        raise ProviderAcceptanceError("invalid identity")


def _closed(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProviderAcceptanceError("incomplete or unknown evidence fields")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProviderAcceptanceError("duplicate evidence field")
        result[key] = value
    return result


@dataclass(frozen=True)
class IssuerAuthority:
    role: str
    provider: str
    principal: str
    deployment: str
    public_key: bytes
    required_ci_checks: frozenset[str] = frozenset()
    ci_workflow_ref: str = ""
    ci_workflow_commit: str = ""

    def __post_init__(self):
        if self.role not in ROLES or not isinstance(self.public_key, bytes) or len(self.public_key) != 32:
            raise ProviderAcceptanceError("invalid issuer authority")
        for value in (self.provider, self.principal, self.deployment):
            _text(value)
        if self.provider != self.provider.casefold():
            raise ProviderAcceptanceError("provider family must be canonical lowercase")
        if not isinstance(self.required_ci_checks, frozenset):
            raise ProviderAcceptanceError("CI policy must be immutable")
        if self.role == "ci":
            if self.provider != "github-actions" or not self.ci_workflow_ref or re.fullmatch(r"[0-9a-f]{40}", self.ci_workflow_commit) is None:
                raise ProviderAcceptanceError("trusted CI workflow provenance required")
            _text(self.ci_workflow_ref)
            if not self.required_ci_checks or any(not isinstance(v, str) or not v for v in self.required_ci_checks):
                raise ProviderAcceptanceError("trusted CI checks required")
        elif self.required_ci_checks or self.ci_workflow_ref or self.ci_workflow_commit:
            raise ProviderAcceptanceError("provider issuer cannot hold CI role")
        elif self.provider not in {"openai", "claude", "cursor", "grok", "gemini"}:
            raise ProviderAcceptanceError("unknown provider family")


@dataclass(frozen=True)
class ReviewContext:
    activation: ActivationIdentity
    repository: str
    pull_request_number: int
    packet_digest: str
    challenge: str
    issued_at: int
    expires_at: int
    material_author_providers: frozenset[str]

    def __post_init__(self):
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is None:
            raise ProviderAcceptanceError("invalid repository")
        if type(self.pull_request_number) is not int or self.pull_request_number < 1:
            raise ProviderAcceptanceError("invalid pull request")
        for value in (self.packet_digest, self.challenge):
            _digest(value)
        for value in (self.activation.candidate_commit, self.activation.candidate_tree):
            if re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise ProviderAcceptanceError("invalid candidate")
        if type(self.issued_at) is not int or type(self.expires_at) is not int or not 0 < self.expires_at - self.issued_at <= 3600:
            raise ProviderAcceptanceError("invalid evidence lifetime")
        if not isinstance(self.material_author_providers, frozenset) or not self.material_author_providers:
            raise ProviderAcceptanceError("trusted material authorship required")
        for provider in self.material_author_providers:
            _text(provider)
            if provider not in {"openai", "claude", "cursor", "grok", "gemini", "perplexity", "firecrawl", "github-copilot", "microsoft-365-copilot"}:
                raise ProviderAcceptanceError("unknown material-author provider family")

    def document(self) -> dict:
        return dict(activation=asdict(self.activation), repository=self.repository,
                    pull_request_number=self.pull_request_number, packet_digest=self.packet_digest,
                    challenge=self.challenge, issued_at=self.issued_at, expires_at=self.expires_at,
                    material_author_providers=sorted(self.material_author_providers))


@dataclass(frozen=True)
class VerifiedProviderAcceptance:
    canonical_package: bytes
    package_digest: str
    challenge: str


def verify_provider_acceptance(raw: bytes, *, expected: ReviewContext,
                               authorities: Mapping[str, IssuerAuthority],
                               now: int) -> VerifiedProviderAcceptance:
    """Verify fresh evidence only. Durable first-use acceptance is caller-owned.

    Do not use this return value as an activation record. On readiness replay,
    reverify the stored package against its stored acceptance timestamp/context
    and immutable issuer registry, without consuming its challenge a second time.
    """
    if not isinstance(raw, bytes) or len(raw) > 131072:
        raise ProviderAcceptanceError("evidence exceeds bounded contract")
    if type(now) is not int or not expected.issued_at <= now < expected.expires_at:
        raise ProviderAcceptanceError("evidence expired or not yet issued")
    registry = dict(authorities)
    if set(registry) != ROLES or any(not isinstance(a, IssuerAuthority) or a.role != role for role, a in registry.items()):
        raise ProviderAcceptanceError("all independent issuer authorities required")
    for field in ("public_key", "principal", "deployment"):
        values = [getattr(a, field) for a in registry.values()]
        if field != "public_key":
            values = [v.casefold() for v in values]
        if len(set(values)) != 3:
            raise ProviderAcceptanceError("issuer authorities must be independent")
    reviewers = [registry[role] for role in ("security", "independent_review")]
    if reviewers[0].provider == reviewers[1].provider:
        raise ProviderAcceptanceError("reviewers must have distinct provider families")
    if any(a.provider in expected.material_author_providers for a in reviewers):
        raise ProviderAcceptanceError("material author cannot review")
    try:
        package = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as exc:
        raise ProviderAcceptanceError("invalid package JSON") from exc
    _closed(package, {"version", "context", "receipts"})
    if package["version"] != VERSION or canonical(package["context"]) != canonical(expected.document()):
        raise ProviderAcceptanceError("wrong version or exact review context")
    receipts = _closed(package["receipts"], set(ROLES))
    for role, authority in registry.items():
        receipt = _closed(receipts[role], {"role", "provider", "principal", "deployment",
                                         "session_id", "transport_evidence_digest", "verdict", "ci_checks", "ci_workflow_ref", "ci_workflow_commit", "signature"})
        for field in ("role", "provider", "principal", "deployment"):
            if receipt[field] != getattr(authority, field):
                raise ProviderAcceptanceError("receipt issuer binding mismatch")
        _text(receipt["session_id"])
        _digest(receipt["transport_evidence_digest"])
        if receipt["verdict"] != ("SUCCESS" if role == "ci" else "ACCEPT"):
            raise ProviderAcceptanceError("unsuccessful issuer verdict")
        if (receipt["ci_workflow_ref"] != authority.ci_workflow_ref or
            receipt["ci_workflow_commit"] != authority.ci_workflow_commit):
            raise ProviderAcceptanceError("untrusted CI workflow provenance")
        checks = receipt["ci_checks"]
        if role != "ci":
            if checks != []:
                raise ProviderAcceptanceError("provider cannot assert CI evidence")
        else:
            if not isinstance(checks, list) or len(checks) != len(authority.required_ci_checks):
                raise ProviderAcceptanceError("incomplete CI evidence")
            observed = set()
            for check in checks:
                _closed(check, {"name", "run_id", "status", "conclusion"})
                if (not isinstance(check["name"], str) or check["name"] in observed or
                    type(check["run_id"]) is not int or check["run_id"] < 1 or
                    check["status"] != "COMPLETED" or check["conclusion"] != "SUCCESS"):
                    raise ProviderAcceptanceError("untrusted or unsuccessful CI check")
                observed.add(check["name"])
            if observed != authority.required_ci_checks:
                raise ProviderAcceptanceError("required CI checks missing")
        unsigned = {k: v for k, v in receipt.items() if k != "signature"}
        payload = canonical(dict(version=VERSION, context=package["context"], receipt=unsigned))
        try:
            signature = base64.b64decode(receipt["signature"], validate=True)
            if len(signature) != 64:
                raise ValueError("invalid signature length")
            Ed25519PublicKey.from_public_bytes(authority.public_key).verify(signature, DOMAIN + payload)
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise ProviderAcceptanceError("issuer signature invalid") from exc
    encoded = canonical(package)
    return VerifiedProviderAcceptance(encoded, hashlib.sha256(encoded).hexdigest(), expected.challenge)

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 08:24 UTC | END]]
