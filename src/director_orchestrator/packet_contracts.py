# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:55 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | BEGIN]]
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping

import rfc8785
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012


PacketKind = Literal["work", "result"]
JsonObject = dict[str, Any]

MAX_DOCUMENT_BYTES = 1_048_576
# Kinds no work packet may ever grant, whatever a result packet reports.
PROHIBITED_EXTERNAL_EFFECT_KINDS = frozenset(
    {"MERGE_PULL_REQUEST", "DEPLOY", "PUBLISH"}
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCHEMA_FILENAMES = {
    "work": "model-work-packet-v1.schema.json",
    "result": "model-result-packet-v1.schema.json",
    "replay": "model-replay-run-v1.schema.json",
    "view": "director-compact-work-view-v1.schema.json",
}
_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "docs" / "schemas"


class PacketContractError(ValueError):
    """A packet or replay-run document failed closed contract verification."""


@dataclass(frozen=True)
class VerifiedReplayRun:
    document: JsonObject
    digest: str
    work_packet_digest: str
    result_packet_digest: str


def _reject_constant(value: str) -> None:
    raise PacketContractError(f"non-finite JSON number is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise PacketContractError("duplicate JSON object key is not allowed")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, source: str) -> JsonObject:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PacketContractError(f"{source} is not valid UTF-8") from exc

    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except PacketContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PacketContractError(f"{source} is not valid JSON") from exc

    if not isinstance(value, dict):
        raise PacketContractError(f"{source} must contain one JSON object")
    return value


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise PacketContractError(f"cannot read JSON document: {path}") from exc
    if len(raw) > max_bytes:
        raise PacketContractError(f"JSON document exceeds the {max_bytes}-byte limit")
    return raw


def load_json_document(
    path: str | os.PathLike[str], *, max_bytes: int = MAX_DOCUMENT_BYTES
) -> JsonObject:
    document_path = Path(path)
    return _decode_json(
        _read_bounded(document_path, max_bytes=max_bytes),
        source=str(document_path),
    )


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    try:
        return rfc8785.dumps(document)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise PacketContractError("document cannot be canonicalized as RFC 8785 JSON") from exc


def document_digest(document: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(document)).hexdigest()}"


def _enforce_canonical_size(document: Mapping[str, Any], *, label: str) -> None:
    if len(canonical_json_bytes(document)) > MAX_DOCUMENT_BYTES:
        raise PacketContractError(f"{label} exceeds the {MAX_DOCUMENT_BYTES}-byte limit")


def _schema_path(kind: Literal["work", "result", "replay", "view"]) -> Path:
    return _SCHEMA_ROOT / _SCHEMA_FILENAMES[kind]


@lru_cache(maxsize=1)
def result_contract_properties() -> dict[str, frozenset[str]]:
    """Allowed property names for each closed object in the result packet.

    Several result fields set ``additionalProperties: false``, so a provider
    that reports one extra key fails validation and its whole capture is
    rejected. Callers normalizing a native envelope project onto these sets
    first. Deriving them from the schema rather than restating them keeps the
    projection from drifting the next time the contract changes.

    Array fields map to their item properties; object fields to their own.
    """

    schema = _validators()["result"].schema
    allowed: dict[str, frozenset[str]] = {}
    for name, spec in schema.get("properties", {}).items():
        if not isinstance(spec, dict):
            continue
        target = spec.get("items") if isinstance(spec.get("items"), dict) else spec
        if target.get("additionalProperties") is False:
            allowed[name] = frozenset(target.get("properties", {}))
    return allowed


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    schemas = {
        kind: load_json_document(_schema_path(kind), max_bytes=256_000)
        for kind in ("work", "result", "replay", "view")
    }
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    registry: Registry = Registry()
    for schema in schemas.values():
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise PacketContractError("contract schema is missing its $id")
        registry = registry.with_resource(schema_id, DRAFT202012.create_resource(schema))

    return {
        kind: Draft202012Validator(schema, registry=registry)
        for kind, schema in schemas.items()
    }


def _validate(kind: Literal["work", "result", "replay", "view"], document: JsonObject) -> None:
    errors = sorted(_validators()[kind].iter_errors(document), key=lambda item: list(item.path))
    if not errors:
        return
    error = errors[0]
    location = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )
    raise PacketContractError(
        f"{kind} document fails schema at {location} ({error.validator})"
    )


def _as_json_object(document: Any, *, label: str) -> JsonObject:
    """Coerce an untrusted payload to a JSON object or fail the packet contract.

    A provider reply arrives as whatever the transport decoded. A list, string, or
    `None` is not a schema violation the validator can describe, and `dict()` on
    such a value raises a bare `TypeError`/`ValueError` that escapes the packet
    contract and reaches callers as an unclassified error. Normalizing here keeps
    every malformed payload on the same fail-closed path while leaving genuine
    schema failures to the validator, which reports the offending location.
    """

    if not isinstance(document, Mapping):
        raise PacketContractError(f"{label} must be one JSON object")
    if any(not isinstance(key, str) for key in document):
        raise PacketContractError(f"{label} must use string member names")
    return copy.deepcopy(dict(document))


def validate_packet(kind: PacketKind, document: Mapping[str, Any]) -> JsonObject:
    packet = _as_json_object(document, label=f"{kind} packet")
    _enforce_canonical_size(packet, label=f"{kind} packet")
    _validate(kind, packet)
    return packet


def validate_compact_view(document: Mapping[str, Any]) -> JsonObject:
    """Validate a director-compact-work-view/v1 document before any transport sends it."""

    view = _as_json_object(document, label="compact work view")
    _enforce_canonical_size(view, label="compact work view")
    _validate("view", view)
    return view


def _require_matching(left: JsonObject, right: JsonObject, field: str) -> None:
    if left.get(field) != right.get(field):
        raise PacketContractError(f"work/result replay linkage mismatch: {field}")


def _verify_work_result_linkage(work_packet: JsonObject, result_packet: JsonObject) -> None:
    for field in (
        "schema_version",
        "task_id",
        "assignment_id",
        "idempotency_key",
        "authority_snapshot_digest",
        "correlation",
    ):
        _require_matching(work_packet, result_packet, field)


def build_replay_run(
    work_packet: Mapping[str, Any], result_packet: Mapping[str, Any]
) -> JsonObject:
    work = validate_packet("work", work_packet)
    result = validate_packet("result", result_packet)
    _verify_work_result_linkage(work, result)

    replay: JsonObject = {
        "schema_version": "1.0",
        "document_type": "director-replay-run",
        "work_packet_digest": document_digest(work),
        "result_packet_digest": document_digest(result),
        "work_packet": work,
        "result_packet": result,
    }
    replay["replay_run_digest"] = document_digest(replay)
    _enforce_canonical_size(replay, label="replay-run document")
    _validate("replay", replay)
    return replay


def verify_replay_run(
    document: Mapping[str, Any], *, expected_digest: str | None = None
) -> VerifiedReplayRun:
    replay = _as_json_object(document, label="replay-run document")
    _enforce_canonical_size(replay, label="replay-run document")
    _validate("replay", replay)

    embedded_run_digest = replay["replay_run_digest"]
    digest_payload = {key: value for key, value in replay.items() if key != "replay_run_digest"}
    actual_run_digest = document_digest(digest_payload)
    if not hmac.compare_digest(embedded_run_digest, actual_run_digest):
        raise PacketContractError("replay-run digest mismatch")

    if expected_digest is not None:
        if not _DIGEST_PATTERN.fullmatch(expected_digest):
            raise PacketContractError("expected replay-run digest is malformed")
        if not hmac.compare_digest(expected_digest, actual_run_digest):
            raise PacketContractError("replay-run digest does not match the trusted reference")

    work = replay["work_packet"]
    result = replay["result_packet"]
    actual_work_digest = document_digest(work)
    actual_result_digest = document_digest(result)
    if not hmac.compare_digest(replay["work_packet_digest"], actual_work_digest):
        raise PacketContractError("work-packet digest mismatch")
    if not hmac.compare_digest(replay["result_packet_digest"], actual_result_digest):
        raise PacketContractError("result-packet digest mismatch")
    _verify_work_result_linkage(work, result)

    return VerifiedReplayRun(
        document=replay,
        digest=actual_run_digest,
        work_packet_digest=actual_work_digest,
        result_packet_digest=actual_result_digest,
    )


def write_replay_run(
    path: str | os.PathLike[str],
    work_packet: Mapping[str, Any],
    result_packet: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> VerifiedReplayRun:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    replay = build_replay_run(work_packet, result_packet)
    encoded = canonical_json_bytes(replay) + b"\n"
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise PacketContractError(
            f"replay-run document exceeds the {MAX_DOCUMENT_BYTES}-byte limit"
        )

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    return verify_replay_run(replay)


def load_replay_run(
    path: str | os.PathLike[str],
    *,
    expected_digest: str | None = None,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> VerifiedReplayRun:
    return verify_replay_run(
        load_json_document(path, max_bytes=max_bytes),
        expected_digest=expected_digest,
    )
# [[Claude | Claude Opus 5 | 2026-08-25 | 02:41 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:55 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]]
