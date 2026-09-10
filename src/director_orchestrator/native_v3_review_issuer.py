# [[Grok Bot | unreported | 2026-09-10 | 15:45 UTC | BEGIN]]
"""Collect-only v3 independent-review receipt producer for actual Grok Bot.

Consumes an already-protected native review capture, a separately signed native
observation, and the exact adopted challenge context. Validates custody and
binding before signing. Does not launch providers, mint observer evidence or
owner adoption, or accept unqualified alias-only analytics as identity.
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

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .primary_activation_evidence import ActivationIdentity
from .provider_acceptance_v2 import ReviewContext, canonical, _closed, _digest, _pairs, _text
from .provider_acceptance_v3 import (
    DOMAIN, OBSERVATION_DOMAIN, OBSERVATION_VERSION, VERSION,
)

MAX_CAPTURE = 1_048_576
MAX_JSON = 131_072
DIGEST = re.compile(r"[0-9a-f]{64}")
# Alias-only or placeholder model labels never qualify a native product session.
UNQUALIFIED_MODELS = frozenset({
    "unknown", "unavailable", "unverified", "default", "auto",
    "grok-bot-default", "grok-default", "default-model",
})
ROLE = "independent_review"
PRODUCT = "grok-bot"
TRANSPORT = "native-product-capture"
AUTH = "native-account-observed"


class IssuerError(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_bytes(path: Path, limit: int) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise IssuerError("protected input path is not a regular file")
        size = path.stat().st_size
        if size > limit:
            raise IssuerError("protected input exceeds bounded read")
        raw = path.read_bytes()
    except OSError as exc:
        raise IssuerError("protected input is unreadable") from exc
    if len(raw) != size:
        raise IssuerError("protected input changed during read")
    return raw


def read_json(path: Path, limit: int = MAX_JSON) -> dict:
    try:
        return json.loads(read_bytes(path, limit), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise IssuerError("protected JSON input is invalid") from exc


def write_new(path: Path, value: object) -> None:
    """Write-once publication; a second collect for the same path refuses."""
    encoded = canonical(value)
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise IssuerError("receipt already published; write-once publication refuses replay") from exc
    except OSError as exc:
        raise IssuerError("receipt publication failed") from exc


def context_from(value: dict) -> ReviewContext:
    if set(value) != {
        "activation", "repository", "pull_request_number", "packet_digest",
        "challenge", "issued_at", "expires_at", "material_author_providers",
    }:
        raise IssuerError("unknown or incomplete review context fields")
    try:
        return ReviewContext(
            **{
                **value,
                "activation": ActivationIdentity(**value["activation"]),
                "material_author_providers": frozenset(value["material_author_providers"]),
            }
        )
    except (TypeError, ValueError) as exc:
        raise IssuerError("review context is invalid") from exc


@dataclass(frozen=True)
class Config:
    """Installed independent-review signer identity; never taken from capture labels."""

    root: Path
    provider: str
    principal: str
    deployment: str
    public_key: bytes
    private_key: bytes
    observation_public_key: bytes

    @classmethod
    def load(cls, path: Path, expected_digest: str) -> "Config":
        if not DIGEST.fullmatch(expected_digest):
            raise IssuerError("installed configuration digest mismatch")
        raw = read_bytes(path, MAX_JSON)
        if digest(raw) != expected_digest:
            raise IssuerError("installed configuration digest mismatch")
        value = json.loads(raw, object_pairs_hook=_pairs)
        required = {
            "root", "role", "provider", "principal", "deployment",
            "public_key", "private_key", "observation_public_key",
        }
        if set(value) != required:
            raise IssuerError("installed configuration contract is invalid")
        if value["role"] != ROLE:
            raise IssuerError("this producer signs independent_review only")
        if value["provider"] != value["provider"].casefold():
            raise IssuerError("provider family must be canonical lowercase")
        try:
            _text(value["provider"])
            _text(value["principal"])
            _text(value["deployment"])
        except Exception as exc:
            raise IssuerError("installed identity fields are invalid") from exc
        if value["provider"] not in {"grok"}:
            # This collect-only producer is the Grok Bot independent-review lane.
            raise IssuerError("installed provider family is not grok")
        try:
            public = base64.b64decode(value["public_key"], validate=True)
            private = base64.b64decode(value["private_key"], validate=True)
            observer = base64.b64decode(value["observation_public_key"], validate=True)
        except (ValueError, TypeError) as exc:
            raise IssuerError("installed key material is invalid") from exc
        if len(public) != 32 or len(private) != 32 or len(observer) != 32:
            raise IssuerError("installed key material is invalid")
        key = Ed25519PrivateKey.from_private_bytes(private)
        derived = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        if derived != public:
            raise IssuerError("installed signer public key does not match private key")
        if public == observer:
            raise IssuerError("signer and observation authorities must be isolated")
        root = Path(value["root"])
        if not root.is_absolute() or root.is_symlink():
            raise IssuerError("issuer root must be an absolute regular directory")
        return cls(
            root=root,
            provider=value["provider"],
            principal=value["principal"],
            deployment=value["deployment"],
            public_key=public,
            private_key=private,
            observation_public_key=observer,
        )


def require_v3_mode() -> None:
    mode = os.environ.get("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "github-v1")
    if mode != "provider-v3-two-party":
        raise IssuerError("v3 collect-only issuer requires provider-v3-two-party mode")


def _verify_observation_signature(envelope: dict, *, observer_key: bytes) -> dict:
    closed = _closed(envelope, {"document", "signature"})
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
) -> dict:
    """Refuse unqualified alias-only identity and bind observation to capture/context."""
    try:
        doc = _closed(
            document,
            {
                "version", "context", "product", "provider_family", "served_model",
                "session_id", "transport", "authentication", "capture_digest",
                "installation_digest", "issued_at", "expires_at",
            },
        )
    except Exception as exc:
        raise IssuerError("native observation document is invalid") from exc
    try:
        _digest(doc["capture_digest"])
        _digest(doc["installation_digest"])
        _text(doc["served_model"])
        _text(doc["session_id"])
    except Exception as exc:
        raise IssuerError("native observation digests or identities are invalid") from exc
    if doc["version"] != OBSERVATION_VERSION:
        raise IssuerError("native observation version is unsupported")
    if doc["product"] != PRODUCT or doc["transport"] != TRANSPORT or doc["authentication"] != AUTH:
        raise IssuerError("observation is not a qualified grok-bot native product capture")
    if doc["provider_family"] != provider:
        raise IssuerError("observation provider family does not match installed reviewer")
    if doc["served_model"].casefold() in UNQUALIFIED_MODELS:
        raise IssuerError(
            "unqualified alias-only model identity cannot corroborate native family/session"
        )
    if canonical(doc["context"]) != canonical(context.document()):
        raise IssuerError("observation context differs from adopted challenge context")
    if doc["capture_digest"] != capture_digest:
        raise IssuerError("observation capture digest does not match protected capture")
    if type(doc["issued_at"]) is not int or type(doc["expires_at"]) is not int:
        raise IssuerError("observation lifetime is invalid")
    if not (context.issued_at <= doc["issued_at"] <= now < doc["expires_at"] <= context.expires_at):
        raise IssuerError("observation or challenge evidence is expired or not yet valid")
    return doc


def collect(
    cfg: Config,
    *,
    context_path: Path,
    capture_path: Path,
    observation_path: Path,
    output_path: Path,
    now: int | None = None,
) -> dict:
    """Validate protected inputs and publish one write-once independent_review receipt."""
    require_v3_mode()
    now = int(time.time()) if now is None else now
    if type(now) is not int:
        raise IssuerError("collector clock must be an integer unix time")

    context = context_from(read_json(context_path))
    if not context.issued_at <= now < context.expires_at:
        raise IssuerError("adopted challenge context is expired or not yet valid")
    if cfg.provider in context.material_author_providers:
        raise IssuerError("material author cannot independently review")

    # Capture must live under the installed issuer root (custody boundary).
    capture_path = capture_path.resolve()
    if cfg.root not in capture_path.parents and capture_path.parent != cfg.root:
        raise IssuerError("native capture is outside the protected issuer root")
    capture = read_bytes(capture_path, MAX_CAPTURE)
    capture_digest = digest(capture)

    observation_path = observation_path.resolve()
    if cfg.root not in observation_path.parents and observation_path.parent != cfg.root:
        raise IssuerError("native observation is outside the protected issuer root")
    envelope = read_json(observation_path)
    document = _verify_observation_signature(envelope, observer_key=cfg.observation_public_key)
    observation = validate_observation(
        document,
        context=context,
        provider=cfg.provider,
        now=now,
        capture_digest=capture_digest,
    )

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
    key = Ed25519PrivateKey.from_private_bytes(cfg.private_key)
    signature = base64.b64encode(key.sign(DOMAIN + payload)).decode()
    receipt = {**unsigned, "signature": signature}
    output = dict(version=VERSION, context=context.document(), receipt=receipt)
    write_new(output_path, output)
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["collect"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--observation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    cfg = Config.load(args.config, args.config_sha256)
    if args.action == "collect":
        print(canonical(collect(
            cfg,
            context_path=args.context,
            capture_path=args.capture,
            observation_path=args.observation,
            output_path=args.output,
        )).decode())


if __name__ == "__main__":
    main()
# [[Grok Bot | unreported | 2026-09-10 | 15:45 UTC | END]]
