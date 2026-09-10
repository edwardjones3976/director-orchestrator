# [[Grok Bot | unreported | 2026-09-10 | 16:06 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | BEGIN]]
"""Explicit two-party acceptance; v2 receipts are never converted or reinterpreted.

This consumes trusted, signed observations. It does not install/qualify a native
product, acquire credentials, sign provider receipts, or enable execution.
"""
from __future__ import annotations

import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .provider_acceptance_v2 import (
    IssuerAuthority, ReviewContext, VerifiedProviderAcceptance, ProviderAcceptanceError,
    canonical, _closed, _digest, _pairs, _text,
)

MODE = "provider-v3-two-party"
VERSION = "director-provider-acceptance/v3-two-party"
DOMAIN = (VERSION + "\0").encode()
REGISTRY_VERSION = "director-acceptance-registry/v3-two-party"
ENVELOPE_VERSION = "director-provider-acceptance-envelope/v3-two-party"
OBSERVATION_VERSION = "director-native-product-observation/v1"
OBSERVATION_DOMAIN = (OBSERVATION_VERSION + "\0").encode()
ADOPTION_VERSION = "director-two-party-adoption/v1"
ADOPTION_DOMAIN = (ADOPTION_VERSION + "\0").encode()
ROLES = frozenset({"security", "independent_review", "ci"})
# These modules exist in the image as well as the owner-reviewed source tree.
POLICY_MODULES = ("provider_acceptance_v2.py", "provider_acceptance_v3.py",
    "acceptance_journal.py", "ci_acceptance_issuer.py", "native_acceptance_issuer.py", "native_v3_review_issuer.py",
    "primary_activation_evidence.py",
    "primary_generation.py", "config.py", "primary_default.py", "policy.py")

def implementation_digest() -> str:
    """Digest actual installed policy bytes; a commit label is not installation proof."""
    root = Path(__file__).parent
    return hashlib.sha256(canonical({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                    for name in POLICY_MODULES})).hexdigest()


def _signature(key, signature, domain, document):
    try:
        decoded = base64.b64decode(signature, validate=True)
        if len(decoded) != 64:
            raise ValueError("invalid signature size")
        Ed25519PublicKey.from_public_bytes(key).verify(decoded, domain + canonical(document))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise ProviderAcceptanceError("authenticated evidence signature invalid") from exc


def _anchor(value):
    _closed(value, {"principal", "deployment", "public_key"})
    _text(value["principal"])
    _text(value["deployment"])
    key = base64.b64decode(value["public_key"], validate=True)
    if len(key) != 32:
        raise ProviderAcceptanceError("invalid trusted observation key")
    return key


def validate_adoption(policy, authorities):
    """Owner-pinned registry + owner signature adopt exact code and candidate."""
    owner = policy["owner_authority"]
    observer = policy["observation_authority"]
    owner_key, observer_key = _anchor(owner), _anchor(observer)
    anchors = [owner, observer] + [asdict(a) for a in authorities.values()]
    keys = [owner_key, observer_key] + [a.public_key for a in authorities.values()]
    if len(set(keys)) != len(keys) or any(len({a[f].casefold() for a in anchors}) != len(anchors)
                                        for f in ("principal", "deployment")):
        raise ProviderAcceptanceError("owner, observer and receipt authorities must be isolated")
    if any(a["deployment"] in policy["revoked_deployments"] for a in (owner, observer)):
        raise ProviderAcceptanceError("owner or observation authority revoked")
    envelope = _closed(policy["owner_adoption"], {"document", "signature"})
    doc = _closed(envelope["document"], {"version", "decision", "policy_commit", "policy_tree",
        "implementation_digest", "registry_digest", "activation", "repository", "pull_request_number", "packet_digest"})
    if (doc["version"] != ADOPTION_VERSION or doc["decision"] != "ADOPT_TWO_PARTY_V3"
            or doc["implementation_digest"] != implementation_digest()
            or doc["registry_digest"] != hashlib.sha256(canonical({k:v for k,v in policy.items()
                if k not in {"owner_adoption", "revoked_deployments"}})).hexdigest()
            or any(doc[name] != policy[name] for name in
                   ("activation", "repository", "pull_request_number", "packet_digest"))):
        raise ProviderAcceptanceError("owner has not adopted this implementation and exact candidate")
    for field in ("policy_commit", "policy_tree"):
        if not isinstance(doc[field], str) or re.fullmatch(r"[0-9a-f]{40}", doc[field]) is None:
            raise ProviderAcceptanceError("exact reviewed policy commit and tree required")
    _signature(owner_key, envelope["signature"], ADOPTION_DOMAIN, doc)
    # OIDC proves CI authority from GitHub, not merely a provisioned service key.
    if policy["ci_oidc_policy"] is None:
        raise ProviderAcceptanceError("two-party acceptance requires authenticated GitHub OIDC CI")


def _observation(receipt, *, authority, context, policy, now):
    envelope = _closed(receipt["native_observation"], {"document", "signature"})
    doc = _closed(envelope["document"], {"version", "context", "product", "provider_family",
        "served_model", "session_id", "transport", "authentication", "capture_digest",
        "installation_digest", "issued_at", "expires_at"})
    _signature(_anchor(policy["observation_authority"]), envelope["signature"], OBSERVATION_DOMAIN, doc)
    for field in ("capture_digest", "installation_digest"):
        _digest(doc[field])
    _text(doc["served_model"])
    if (doc["served_model"].casefold() in {"unknown", "unavailable", "unverified", "default", "auto"}
            or doc["version"] != OBSERVATION_VERSION
            or canonical(doc["context"]) != canonical(context.document())
            or doc["provider_family"] != authority.provider
            or doc["session_id"] != receipt["session_id"]
            or doc["capture_digest"] != receipt["transport_evidence_digest"]
            or doc["authentication"] != "native-account-observed"
            or type(doc["issued_at"]) is not int or type(doc["expires_at"]) is not int
            or not context.issued_at <= doc["issued_at"] <= now < doc["expires_at"] <= context.expires_at):
        raise ProviderAcceptanceError("native product identity, capture, session or freshness unqualified")
    if authority.role == "security":
        if (authority.provider != "openai" or doc["product"] != "codex-security"
                or doc["transport"] != "codex-security-product"):
            raise ProviderAcceptanceError("mandatory Codex Security product evidence required")
    elif (doc["product"] not in {"codex", "grok-bot"}
            or doc["transport"] != "native-product-capture"
            or (doc["product"] == "codex" and authority.provider != "openai")):
        raise ProviderAcceptanceError("qualified Codex or actual Grok Bot review required")
    return doc


def verify_two_party_acceptance(raw: bytes, *, expected: ReviewContext, authorities,
                                now: int, policy) -> VerifiedProviderAcceptance:
    if not isinstance(raw, bytes) or len(raw) > 131072:
        raise ProviderAcceptanceError("evidence exceeds bounded contract")
    if type(now) is not int or not expected.issued_at <= now < expected.expires_at:
        raise ProviderAcceptanceError("evidence expired or not yet issued")
    registry = dict(authorities)
    if set(registry) != ROLES or any(not isinstance(a, IssuerAuthority) or a.role != role
                                   for role, a in registry.items()):
        raise ProviderAcceptanceError("all three receipt authorities required")
    validate_adoption(policy, registry)
    from .acceptance_journal import REQUIRED_ACTIVATION_CHECKS
    if not REQUIRED_ACTIVATION_CHECKS <= registry["ci"].required_ci_checks:
        raise ProviderAcceptanceError("production acceptance requires all six baseline CI checks")
    reviewer = registry["independent_review"]
    if reviewer.provider in expected.material_author_providers:
        raise ProviderAcceptanceError("material author cannot independently review")
    package = _closed(json.loads(raw, object_pairs_hook=_pairs), {"version", "context", "receipts"})
    if package["version"] != VERSION or canonical(package["context"]) != canonical(expected.document()):
        raise ProviderAcceptanceError("wrong version or exact review context")
    receipts = _closed(package["receipts"], set(ROLES))
    for role, authority in registry.items():
        fields = {"role", "provider", "principal", "deployment", "session_id",
            "transport_evidence_digest", "verdict", "ci_checks", "ci_workflow_ref",
            "ci_workflow_commit", "signature"}
        if role != "ci":
            fields.add("native_observation")
        if role == "security":
            fields.add("analysis")
        receipt = _closed(receipts[role], fields)
        if any(receipt[field] != getattr(authority, field)
               for field in ("role", "provider", "principal", "deployment", "ci_workflow_ref", "ci_workflow_commit")):
            raise ProviderAcceptanceError("receipt issuer or trusted CI workflow differs")
        _text(receipt["session_id"])
        _digest(receipt["transport_evidence_digest"])
        if receipt["verdict"] != ("SUCCESS" if role == "ci" else "ANALYZED" if role == "security" else "ACCEPT"):
            raise ProviderAcceptanceError("required successful evidence missing")
        if role == "ci":
            checks = receipt["ci_checks"]
            if not isinstance(checks, list) or len(checks) != len(authority.required_ci_checks):
                raise ProviderAcceptanceError("incomplete CI evidence")
            observed = set()
            for check in checks:
                _closed(check, {"name", "run_id", "status", "conclusion"})
                if (not isinstance(check["name"], str) or check["name"] in observed
                        or type(check["run_id"]) is not int or check["run_id"] < 1
                        or check["status"] != "COMPLETED" or check["conclusion"] != "SUCCESS"):
                    raise ProviderAcceptanceError("untrusted or unsuccessful CI check")
                observed.add(check["name"])
            if observed != authority.required_ci_checks:
                raise ProviderAcceptanceError("required CI checks missing")
        else:
            if receipt["ci_checks"] != []:
                raise ProviderAcceptanceError("provider cannot assert CI")
            _observation(receipt, authority=authority, context=expected, policy=policy, now=now)
        if role == "security":
            analysis = _closed(receipt["analysis"], {"scan_id", "manifest_digest", "report_digest",
                "coverage_complete", "unresolved_findings", "author_family_analysis"})
            _text(analysis["scan_id"])
            _digest(analysis["manifest_digest"])
            _digest(analysis["report_digest"])
            if (analysis["coverage_complete"] is not True or type(analysis["unresolved_findings"]) is not int
                    or analysis["unresolved_findings"] != 0
                    or analysis["author_family_analysis"] is not (authority.provider in expected.material_author_providers)):
                raise ProviderAcceptanceError("incomplete security analysis or incorrect authorship label")
        unsigned = {k: v for k, v in receipt.items() if k != "signature"}
        _signature(authority.public_key, receipt["signature"], DOMAIN,
                   dict(version=VERSION, context=package["context"], receipt=unsigned))
    encoded = canonical(package)
    return VerifiedProviderAcceptance(encoded, hashlib.sha256(encoded).hexdigest(), expected.challenge)


# Reuse transaction, challenge consumption, immutable snapshot and replay logic.
from .acceptance_journal import AcceptanceJournal, _activation_errors


class TwoPartyAcceptanceJournal(AcceptanceJournal):
    policy_version = REGISTRY_VERSION
    envelope_version = ENVELOPE_VERSION
    policy_extra_fields = frozenset({"owner_authority", "owner_adoption", "observation_authority"})

    @_activation_errors
    def policy(self):
        doc = super().policy()
        validate_adoption(doc, self.authorities(doc))
        return doc

    @staticmethod
    def verify_package(raw, *, expected, authorities, now, policy):
        return verify_two_party_acceptance(raw, expected=expected, authorities=authorities, now=now, policy=policy)
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | END]]
# [[Grok Bot | unreported | 2026-09-10 | 16:06 UTC | END]]
