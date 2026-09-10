# [[Grok Bot | unreported | 2026-09-10 | 16:00 UTC | BEGIN]]
"""Collect-only v3 independent-review receipt producer.

Consumes a protected internal capture contract, a separately signed native
observation, and an owner-authorized challenge under an adopted v3 registry.
Does not launch providers, mint observer/owner evidence, invent a native product
wire schema, or treat chat branding as qualification.

Enrolled independent reviewers may be Codex (product ``codex``, family openai)
or actual Grok Bot (product ``grok-bot``, family grok). Product identity is
distinct from the observed canonical provider family.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .native_acceptance_issuer import (
    IssuerError as NativeIssuerError,
    claim,
    canonical_native_path,
    contained_native_path,
    context_from as native_context_from,
    digest,
    write_new,
)
from .provider_acceptance_v2 import (
    IssuerAuthority,
    ProviderAcceptanceError,
    ReviewContext,
    canonical,
    _closed,
    _digest,
    _pairs,
    _text,
)
from .provider_acceptance_v3 import (
    DOMAIN,
    OBSERVATION_DOMAIN,
    OBSERVATION_VERSION,
    VERSION,
    MODE,
    validate_adoption,
)

# Bounded reads: size checked via lstat before open; no Path.read_bytes after a
# separate TOCTOU-prone size check without the same fd.
MAX_CAPTURE = 1_048_576
MAX_JSON = 131_072
DIGEST = re.compile(r"[0-9a-f]{64}")
UNQUALIFIED_MODELS = frozenset({
    "unknown", "unavailable", "unverified", "default", "auto",
    "grok-bot-default", "grok-default", "default-model",
})
ROLE = "independent_review"
PROTECTED_CAPTURE_VERSION = "director-protected-independent-review-capture/v1"
# Internal protected capture contract only — not a claim of native product wire
# qualification. Native product qualification remains the signed observation.
CAPTURE_FIELDS = frozenset({
    "version", "verdict", "challenge", "packet_digest",
    "candidate_commit", "candidate_tree", "session_id",
    "provider_family", "product",
})
PRODUCT_FOR_FAMILY = {
    "openai": "codex",
    "grok": "grok-bot",
}
ALLOWED_INDEPENDENT_FAMILIES = frozenset(PRODUCT_FOR_FAMILY)


class IssuerError(ValueError):
    pass


def require_v3_mode() -> None:
    mode = os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1")
    if mode != MODE:
        raise IssuerError("v3 collect-only issuer requires provider-v3-two-party mode")


def _map_native(exc: Exception) -> IssuerError:
    return IssuerError(str(exc) or exc.__class__.__name__)


def read_bounded(path: Path, limit: int) -> bytes:
    """Open once: refuse symlinks, bound size from the same fd, refuse growth."""
    try:
        if path.is_symlink() or not path.is_file():
            raise IssuerError("protected input path is not a regular file")
        with path.open("rb") as handle:
            st = os.fstat(handle.fileno())
            if st.st_size > limit:
                raise IssuerError("protected input exceeds bounded read")
            raw = handle.read(limit + 1)
            if len(raw) > limit or len(raw) != st.st_size:
                raise IssuerError("protected input exceeds bounded read or changed during read")
            return raw
    except OSError as exc:
        raise IssuerError("protected input is unreadable") from exc


def read_json(path: Path, limit: int = MAX_JSON) -> dict:
    try:
        return json.loads(read_bounded(path, limit), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise IssuerError("protected JSON input is invalid") from exc


def context_from(value: dict) -> ReviewContext:
    try:
        return native_context_from(value)
    except (NativeIssuerError, TypeError, ValueError, ProviderAcceptanceError) as exc:
        raise IssuerError("review context is invalid") from exc


def expected_product(provider: str) -> str:
    try:
        return PRODUCT_FOR_FAMILY[provider]
    except KeyError as exc:
        raise IssuerError("installed provider family is not enrolled for independent review") from exc


@dataclass(frozen=True)
class Config:
    """Installed collect-only signer. Private key bytes never travel in caller JSON."""

    root: Path
    provider: str
    principal: str
    deployment: str
    public_key: bytes
    observation_public_key: bytes
    registry_path: Path
    registry_sha256: str
    policy: dict
    authority: IssuerAuthority

    @classmethod
    def load(cls, path: Path, expected_digest: str) -> "Config":
        # Mode gate before any secret or registry material is opened.
        require_v3_mode()
        if not DIGEST.fullmatch(expected_digest):
            raise IssuerError("installed configuration digest mismatch")
        try:
            cfg_path = canonical_native_path(str(path))
        except NativeIssuerError as exc:
            raise _map_native(exc) from exc
        raw = read_bounded(cfg_path, MAX_JSON)
        if digest(raw) != expected_digest:
            raise IssuerError("installed configuration digest mismatch")
        value = json.loads(raw, object_pairs_hook=_pairs)
        required = {
            "root", "role", "provider", "principal", "deployment",
            "public_key", "observation_public_key", "registry_path", "registry_sha256",
        }
        if set(value) != required:
            raise IssuerError("installed configuration contract is invalid")
        if "private_key" in value:
            raise IssuerError("caller JSON must not carry private key material")
        if value["role"] != ROLE:
            raise IssuerError("this producer signs independent_review only")
        if value["provider"] != value["provider"].casefold():
            raise IssuerError("provider family must be canonical lowercase")
        if value["provider"] not in ALLOWED_INDEPENDENT_FAMILIES:
            raise IssuerError("installed provider family is not enrolled for independent review")
        try:
            _text(value["provider"])
            _text(value["principal"])
            _text(value["deployment"])
        except ProviderAcceptanceError as exc:
            raise IssuerError("installed identity fields are invalid") from exc
        try:
            public = base64.b64decode(value["public_key"], validate=True)
            observer = base64.b64decode(value["observation_public_key"], validate=True)
        except (ValueError, TypeError) as exc:
            raise IssuerError("installed key material is invalid") from exc
        if len(public) != 32 or len(observer) != 32 or public == observer:
            raise IssuerError("installed key material is invalid or not isolated")
        if not DIGEST.fullmatch(value["registry_sha256"]):
            raise IssuerError("registry digest pin is invalid")
        try:
            root = canonical_native_path(value["root"])
            if not root.is_dir():
                raise IssuerError("issuer root must be a directory")
            registry_path = contained_native_path(value["registry_path"], [str(root)])
        except NativeIssuerError as exc:
            raise _map_native(exc) from exc
        # Private key lives only under the protected broker directory.
        try:
            key_path = contained_native_path(str(root / "broker" / "private.key"), [str(root)])
        except NativeIssuerError as exc:
            raise _map_native(exc) from exc
        private = read_bounded(key_path, 32)
        if len(private) != 32:
            raise IssuerError("installed broker private key is invalid")
        derived = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        if derived != public:
            raise IssuerError("installed signer public key does not match broker private key")
        registry_raw = read_bounded(registry_path, MAX_JSON)
        if digest(registry_raw) != value["registry_sha256"]:
            raise IssuerError("owner-pinned registry digest mismatch")
        try:
            policy = json.loads(registry_raw, object_pairs_hook=_pairs)
        except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
            raise IssuerError("owner-pinned registry is invalid") from exc
        try:
            authorities = {
                role: IssuerAuthority(
                    role=fields["role"],
                    provider=fields["provider"],
                    principal=fields["principal"],
                    deployment=fields["deployment"],
                    public_key=base64.b64decode(fields["public_key"], validate=True),
                    required_ci_checks=frozenset(fields.get("required_ci_checks") or ()),
                    ci_workflow_ref=fields.get("ci_workflow_ref") or "",
                    ci_workflow_commit=fields.get("ci_workflow_commit") or "",
                )
                for role, fields in policy["authorities"].items()
            }
            validate_adoption(policy, authorities)
        except (KeyError, TypeError, ValueError, ProviderAcceptanceError) as exc:
            raise IssuerError("owner adoption or enrolled registry authorities are invalid") from exc
        authority = authorities.get(ROLE)
        if authority is None or authority.public_key != public or authority.provider != value["provider"]:
            raise IssuerError("installed signer is not the enrolled independent_review authority")
        if authority.principal != value["principal"] or authority.deployment != value["deployment"]:
            raise IssuerError("installed principal/deployment do not match enrolled authority")
        observer_key = base64.b64decode(policy["observation_authority"]["public_key"], validate=True)
        if observer_key != observer:
            raise IssuerError("installed observation key is not the enrolled observation authority")
        if authority.deployment in policy.get("revoked_deployments", ()) or policy["observation_authority"]["deployment"] in policy.get("revoked_deployments", ()):
            raise IssuerError("enrolled independent_review or observation authority is revoked")
        return cls(
            root=root,
            provider=value["provider"],
            principal=value["principal"],
            deployment=value["deployment"],
            public_key=public,
            observation_public_key=observer,
            registry_path=registry_path,
            registry_sha256=value["registry_sha256"],
            policy=policy,
            authority=authority,
        )

    def private_key(self) -> bytes:
        path = contained_native_path(str(self.root / "broker" / "private.key"), [str(self.root)])
        raw = read_bounded(path, 32)
        if len(raw) != 32:
            raise IssuerError("installed broker private key is invalid")
        return raw


def authorization_context(cfg: Config, challenge: str) -> ReviewContext:
    if not DIGEST.fullmatch(challenge):
        raise IssuerError("invalid challenge ID")
    try:
        path = contained_native_path(str(cfg.root / "authorizations" / f"{challenge}.json"), [str(cfg.root)])
    except NativeIssuerError as exc:
        raise _map_native(exc) from exc
    value = read_json(path)
    if set(value) != {"base", "context"}:
        raise IssuerError("authorization can contain only protected base and context")
    context = context_from(value["context"])
    if context.challenge != challenge:
        raise IssuerError("authorization challenge mismatch")
    if context.repository != cfg.policy["repository"]:
        raise IssuerError("authorization repository is not the adopted candidate repository")
    if context.pull_request_number != cfg.policy["pull_request_number"]:
        raise IssuerError("authorization pull request is not the adopted candidate")
    if context.packet_digest != cfg.policy["packet_digest"]:
        raise IssuerError("authorization packet digest is not the adopted candidate packet")
    from dataclasses import asdict
    if asdict(context.activation) != cfg.policy["activation"]:
        raise IssuerError("authorization activation is not the adopted candidate identity")
    if cfg.provider in context.material_author_providers:
        raise IssuerError("material author cannot independently review")
    return context


def parse_protected_capture(
    raw: bytes,
    *,
    context: ReviewContext,
    provider: str,
) -> dict:
    """Internal protected capture contract — not native product wire qualification."""
    try:
        document = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise IssuerError("protected capture is not valid JSON") from exc
    try:
        doc = _closed(document, CAPTURE_FIELDS)
    except ProviderAcceptanceError as exc:
        raise IssuerError("protected capture fields are incomplete or unknown") from exc
    if doc["version"] != PROTECTED_CAPTURE_VERSION:
        raise IssuerError("protected capture version is unsupported")
    if doc["verdict"] != "ACCEPT":
        raise IssuerError("protected capture terminal review is not a successful ACCEPT")
    try:
        _digest(doc["challenge"])
        _digest(doc["packet_digest"])
        _text(doc["session_id"])
        _text(doc["provider_family"])
        _text(doc["product"])
    except ProviderAcceptanceError as exc:
        raise IssuerError("protected capture digests or identities are invalid") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", doc["candidate_commit"]):
        raise IssuerError("protected capture candidate commit is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", doc["candidate_tree"]):
        raise IssuerError("protected capture candidate tree is invalid")
    if doc["challenge"] != context.challenge:
        raise IssuerError("protected capture challenge does not match authorized challenge")
    if doc["packet_digest"] != context.packet_digest:
        raise IssuerError("protected capture packet digest does not match authorized context")
    if doc["candidate_commit"] != context.activation.candidate_commit:
        raise IssuerError("protected capture candidate commit does not match authorized context")
    if doc["candidate_tree"] != context.activation.candidate_tree:
        raise IssuerError("protected capture candidate tree does not match authorized context")
    if doc["provider_family"] != provider:
        raise IssuerError("protected capture provider family does not match installed reviewer")
    if doc["product"] != expected_product(provider):
        raise IssuerError("protected capture product does not match enrolled product for family")
    return doc


def _verify_observation_signature(envelope: dict, *, observer_key: bytes) -> dict:
    try:
        closed = _closed(envelope, {"document", "signature"})
    except ProviderAcceptanceError as exc:
        raise IssuerError("native observation envelope is invalid") from exc
    document = closed["document"]
    try:
        signature = base64.b64decode(closed["signature"], validate=True)
        if len(signature) != 64:
            raise ValueError("invalid signature size")
        Ed25519PublicKey.from_public_bytes(observer_key).verify(
            signature, OBSERVATION_DOMAIN + canonical(document)
        )
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise IssuerError("native observation signature is invalid") from exc
    return document


def validate_observation(
    document: dict,
    *,
    context: ReviewContext,
    provider: str,
    now: int,
    capture_digest: str,
    session_id: str,
    product: str,
) -> dict:
    try:
        doc = _closed(
            document,
            {
                "version", "context", "product", "provider_family", "served_model",
                "session_id", "transport", "authentication", "capture_digest",
                "installation_digest", "issued_at", "expires_at",
            },
        )
    except ProviderAcceptanceError as exc:
        raise IssuerError("native observation document is invalid") from exc
    try:
        _digest(doc["capture_digest"])
        _digest(doc["installation_digest"])
        _text(doc["served_model"])
        _text(doc["session_id"])
    except ProviderAcceptanceError as exc:
        raise IssuerError("native observation digests or identities are invalid") from exc
    if doc["version"] != OBSERVATION_VERSION:
        raise IssuerError("native observation version is unsupported")
    if doc["transport"] != "native-product-capture" or doc["authentication"] != "native-account-observed":
        raise IssuerError("observation transport or authentication is unqualified")
    if doc["product"] != product or doc["provider_family"] != provider:
        raise IssuerError("observation product/family does not match protected capture and enrollment")
    if product == "codex" and provider != "openai":
        raise IssuerError("codex product requires openai provider family")
    if product == "grok-bot" and provider != "grok":
        raise IssuerError("grok-bot product requires grok provider family")
    if doc["served_model"].casefold() in UNQUALIFIED_MODELS:
        raise IssuerError(
            "unqualified alias-only model identity cannot corroborate native family/session"
        )
    if canonical(doc["context"]) != canonical(context.document()):
        raise IssuerError("observation context differs from authorized challenge context")
    if doc["capture_digest"] != capture_digest:
        raise IssuerError("observation capture digest does not match protected capture")
    if doc["session_id"] != session_id:
        raise IssuerError("observation session does not match protected capture session")
    if type(doc["issued_at"]) is not int or type(doc["expires_at"]) is not int:
        raise IssuerError("observation lifetime is invalid")
    if not (context.issued_at <= doc["issued_at"] <= now < doc["expires_at"] <= context.expires_at):
        raise IssuerError("observation or challenge evidence is expired or not yet valid")
    return doc


def collect(cfg: Config, *, challenge: str, now: int | None = None) -> dict:
    """Validate protected custody inputs and publish one challenge-bound receipt."""
    require_v3_mode()
    now = int(time.time()) if now is None else now
    if type(now) is not int:
        raise IssuerError("collector clock must be an integer unix time")
    context = authorization_context(cfg, challenge)
    if not context.issued_at <= now < context.expires_at:
        raise IssuerError("authorized challenge context is expired or not yet valid")

    try:
        capture_path = contained_native_path(
            str(cfg.root / "captures" / f"{challenge}.json"), [str(cfg.root)]
        )
        observation_path = contained_native_path(
            str(cfg.root / "observations" / f"{challenge}.json"), [str(cfg.root)]
        )
        public_dir = contained_native_path(str(cfg.root / "public"), [str(cfg.root)])
        broker_dir = contained_native_path(str(cfg.root / "broker"), [str(cfg.root)])
    except NativeIssuerError as exc:
        raise _map_native(exc) from exc
    output_path = public_dir / f"{challenge}.json"
    claims_path = broker_dir / "claims.sqlite3"

    capture_raw = read_bounded(capture_path, MAX_CAPTURE)
    capture_digest = digest(capture_raw)
    capture = parse_protected_capture(capture_raw, context=context, provider=cfg.provider)

    envelope = read_json(observation_path)
    document = _verify_observation_signature(envelope, observer_key=cfg.observation_public_key)
    observation = validate_observation(
        document,
        context=context,
        provider=cfg.provider,
        now=now,
        capture_digest=capture_digest,
        session_id=capture["session_id"],
        product=capture["product"],
    )

    # Durable single-claim before signing/publication. Failures remain claimed.
    try:
        claim(claims_path, challenge)
    except NativeIssuerError as exc:
        raise IssuerError("challenge already claimed; write-once publication refuses replay") from exc

    unsigned = {
        "role": ROLE,
        "provider": cfg.provider,
        "principal": cfg.principal,
        "deployment": cfg.deployment,
        "session_id": observation["session_id"],
        "transport_evidence_digest": capture_digest,
        "verdict": "ACCEPT",
        "ci_checks": [],
        "ci_workflow_ref": "",
        "ci_workflow_commit": "",
        "native_observation": envelope,
    }
    payload = canonical(dict(version=VERSION, context=context.document(), receipt=unsigned))
    key = Ed25519PrivateKey.from_private_bytes(cfg.private_key())
    signature = base64.b64encode(key.sign(DOMAIN + payload)).decode()
    receipt = {**unsigned, "signature": signature}
    output = dict(version=VERSION, context=context.document(), receipt=receipt)
    try:
        write_new(output_path, output)
    except FileExistsError as exc:
        raise IssuerError("receipt already published; write-once publication refuses replay") from exc
    except OSError as exc:
        raise IssuerError("receipt publication failed") from exc
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["collect"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--challenge", required=True)
    args = parser.parse_args(argv)
    require_v3_mode()
    cfg = Config.load(args.config, args.config_sha256)
    if args.action == "collect":
        print(canonical(collect(cfg, challenge=args.challenge)).decode())


if __name__ == "__main__":
    main()
# [[Grok Bot | unreported | 2026-09-10 | 16:00 UTC | END]]
