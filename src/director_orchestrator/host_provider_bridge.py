# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:42 UTC | BEGIN]]
"""Authenticated single-dispatch bridge between Linux containers and Windows CLIs.

The bridge is deliberately a narrow packet transport.  It neither discovers
providers nor grants authority.  Every request binds the provider, canonical
work-packet digest, task, assignment, idempotency key, and writer generation to
an HMAC-authenticated envelope.  A durable SQLite claim is committed before the
single ``shell=False`` subprocess attempt.  Ambiguous attempts are never rerun.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .langgraph_runtime import ProviderAdapterContext
from .packet_contracts import canonical_json_bytes


BRIDGE_PROTOCOL = "director-host-provider-bridge/v1"
SUPPORTED_HOST_LANES = frozenset(
    {"claude", "grok", "antigravity", "github-copilot"}
)
UNSUPPORTED_HOST_LANES = frozenset({"cursor", "microsoft-365-copilot"})
_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PACKET_BYTES = 1_048_576
_MAX_RESULT_BYTES = 1_048_576
_BRIDGE_PATH = "/v1/provider/dispatch"
_BRIDGE_CHALLENGE_PATH = "/v1/provider/challenge"
_SAFE_OS_ENV = frozenset(
    {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC", "LOCALAPPDATA", "APPDATA", "USERPROFILE"}
)
_LANE_AUTH_ENV = {
    "claude": frozenset({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
    "grok": frozenset({"XAI_API_KEY"}),
    "antigravity": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "github-copilot": frozenset({"GH_TOKEN", "GITHUB_TOKEN"}),
}


class HostProviderBridgeError(RuntimeError):
    """Fail-closed bridge or binding error."""


class HostProviderBridgeAmbiguous(HostProviderBridgeError):
    """An external attempt may have happened and cannot be resent."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_token(value: str, name: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise HostProviderBridgeError(f"invalid {name}")
    return value


def _signature_payload(
    *,
    timestamp: int,
    nonce: str,
    provider: str,
    task_id: str,
    assignment_id: str,
    idempotency_digest: str,
    writer_generation: int,
    packet_digest: str,
    writer_owner_token_digest: str,
    work_packet_digest: str = "",
) -> bytes:
    material = [
        BRIDGE_PROTOCOL,
        str(timestamp),
        nonce,
        provider,
        task_id,
        assignment_id,
        idempotency_digest,
        str(writer_generation),
        packet_digest,
        writer_owner_token_digest,
    ]
    # A redacted lane also authenticates the opaque digest of the original work
    # packet it was derived from. The element is appended only when it exists,
    # so every unredacted lane keeps a byte-identical signature payload, and a
    # stripped or forged binding changes the signature and fails closed.
    if work_packet_digest:
        material.append(work_packet_digest)
    return "\n".join(material).encode("utf-8")


def bridge_signature(secret: str, **bindings: object) -> str:
    if not isinstance(secret, str) or len(secret) < 32:
        raise HostProviderBridgeError("bridge secret must contain at least 32 characters")
    return hmac.new(
        secret.encode("utf-8"),
        _signature_payload(**bindings),  # type: ignore[arg-type]
        hashlib.sha256,
    ).hexdigest()


def bridge_response_signature(
    secret: str, *, nonce: str, idempotency_digest: str, response_digest: str
) -> str:
    if len(secret) < 32:
        raise HostProviderBridgeError("bridge secret must contain at least 32 characters")
    material = "\n".join(
        (BRIDGE_PROTOCOL, "response", nonce, idempotency_digest, response_digest)
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def bridge_challenge_signature(
    secret: str, *, timestamp: int, nonce: str, command_map_digest: str = ""
) -> str:
    if len(secret) < 32:
        raise HostProviderBridgeError("bridge secret must contain at least 32 characters")
    material = "\n".join(
        (BRIDGE_PROTOCOL, "challenge", str(timestamp), nonce, command_map_digest)
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class HostBridgeChallengeEvidence:
    """One fresh authenticated observation of the bridge command map."""

    configured_providers: frozenset[str]
    command_map_digest: str
    observed_at: int


def challenge_host_provider_bridge(
    endpoint: str,
    secret: str,
    *,
    timeout_seconds: int = 5,
    now: int | None = None,
) -> HostBridgeChallengeEvidence:
    """Prove bridge liveness and its allowlisted command map without provider I/O."""

    parsed = urlsplit(endpoint)
    if (
        parsed.path != _BRIDGE_PATH
        or parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"host.docker.internal", "localhost", "127.0.0.1", "::1"}
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise HostProviderBridgeError("host bridge endpoint is not the dispatch path")
    challenge_url = endpoint[: -len(_BRIDGE_PATH)] + _BRIDGE_CHALLENGE_PATH
    timestamp = int(time.time()) if now is None else int(now)
    nonce = hashlib.sha256(f"challenge:{time.time_ns()}:{endpoint}".encode()).hexdigest()
    request = urllib.request.Request(
        challenge_url,
        headers={
            "X-Director-Bridge-Protocol": BRIDGE_PROTOCOL,
            "X-Director-Bridge-Timestamp": str(timestamp),
            "X-Director-Bridge-Nonce": nonce,
            "Authorization": "Director-HMAC "
            + bridge_challenge_signature(secret, timestamp=timestamp, nonce=nonce),
        },
        method="GET",
    )
    try:
        with urllib.request.build_opener(_NoRedirectHandler()).open(
            request, timeout=timeout_seconds
        ) as response:
            payload = response.read(65_537)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HostProviderBridgeError("host bridge challenge is unavailable") from exc
    if not payload or len(payload) > 65_536:
        raise HostProviderBridgeError("host bridge challenge response is not bounded")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostProviderBridgeError("host bridge challenge response is malformed") from exc
    providers = decoded.get("configured_providers") if isinstance(decoded, dict) else None
    digest = decoded.get("command_map_digest") if isinstance(decoded, dict) else None
    proof = decoded.get("proof") if isinstance(decoded, dict) else None
    if (
        decoded.get("ok") is not True
        or decoded.get("protocol") != BRIDGE_PROTOCOL
        or not isinstance(providers, list)
        or providers != sorted(set(providers))
        or any(provider not in SUPPORTED_HOST_LANES for provider in providers)
        or not isinstance(digest, str)
        or digest != "sha256:" + _digest(canonical_json_bytes(providers))
        or not isinstance(proof, str)
    ):
        raise HostProviderBridgeError("host bridge challenge bindings are invalid")
    expected = bridge_challenge_signature(
        secret, timestamp=timestamp, nonce=nonce, command_map_digest=digest
    )
    if not hmac.compare_digest(proof, expected):
        raise HostProviderBridgeError("host bridge challenge authentication failed")
    return HostBridgeChallengeEvidence(frozenset(providers), digest, timestamp)


@dataclass(frozen=True, slots=True)
class BridgeRequest:
    provider: str
    task_id: str
    assignment_id: str
    idempotency_digest: str
    writer_generation: int
    packet_digest: str
    writer_owner_token_digest: str
    timestamp: int
    nonce: str
    signature: str
    packet: bytes
    # Present only for a redacted lane: the opaque digest of the original work
    # packet the posted body was derived from. It is authenticated, never used
    # to reconstruct anything the host was deliberately not shown.
    work_packet_digest: str = ""

    def validate(self, secret: str, *, now: int | None = None, max_age_seconds: int = 60) -> None:
        if self.provider in UNSUPPORTED_HOST_LANES:
            raise HostProviderBridgeError(f"unsupported host provider lane: {self.provider}")
        if self.provider not in SUPPORTED_HOST_LANES:
            raise HostProviderBridgeError("unallowlisted host provider lane")
        _require_token(self.task_id, "task id")
        _require_token(self.assignment_id, "assignment id")
        _require_token(self.nonce, "nonce")
        if _HEX_64.fullmatch(self.idempotency_digest) is None:
            raise HostProviderBridgeError("invalid idempotency digest")
        if _HEX_64.fullmatch(self.packet_digest) is None or _digest(self.packet) != self.packet_digest:
            raise HostProviderBridgeError("work-packet digest mismatch")
        if self.work_packet_digest and _HEX_64.fullmatch(self.work_packet_digest) is None:
            raise HostProviderBridgeError("origin work-packet digest is invalid")
        if self.writer_generation < 1:
            raise HostProviderBridgeError("writer generation is not active")
        if not self.writer_owner_token_digest.startswith("sha256:") or len(self.writer_owner_token_digest) != 71:
            raise HostProviderBridgeError("writer owner token digest is invalid")
        observed_now = int(time.time()) if now is None else int(now)
        if abs(observed_now - self.timestamp) > max_age_seconds:
            raise HostProviderBridgeError("stale host bridge request")
        expected = bridge_signature(
            secret,
            timestamp=self.timestamp,
            nonce=self.nonce,
            provider=self.provider,
            task_id=self.task_id,
            assignment_id=self.assignment_id,
            idempotency_digest=self.idempotency_digest,
            writer_generation=self.writer_generation,
            packet_digest=self.packet_digest,
            writer_owner_token_digest=self.writer_owner_token_digest,
            work_packet_digest=self.work_packet_digest,
        )
        if not hmac.compare_digest(expected, self.signature):
            raise HostProviderBridgeError("host bridge authentication failed")
        try:
            packet = json.loads(self.packet)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostProviderBridgeError("work packet is not valid JSON") from exc
        expected_values = {
            "task_id": self.task_id,
            "assignment_id": self.assignment_id,
        }
        if not isinstance(packet, dict) or any(packet.get(k) != v for k, v in expected_values.items()):
            raise HostProviderBridgeError("work-packet identity does not match bridge bindings")
        packet_key = packet.get("idempotency_key")
        if not isinstance(packet_key, str) or _digest(packet_key.encode("utf-8")) != self.idempotency_digest:
            raise HostProviderBridgeError("work-packet idempotency binding mismatch")


class BridgeClaimStore:
    """Durable replay and attempt claims for the Windows bridge process."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bridge_nonces (
                    nonce_digest TEXT PRIMARY KEY,
                    observed_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bridge_attempts (
                    idempotency_digest TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    writer_generation INTEGER NOT NULL,
                    packet_digest TEXT NOT NULL,
                    writer_owner_token_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('INFLIGHT','SUCCEEDED','AMBIGUOUS')),
                    response_json BLOB,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def claim(self, request: BridgeRequest) -> bytes | None:
        nonce_digest = _digest(request.nonce.encode("utf-8"))
        bindings = (
            request.provider,
            request.task_id,
            request.assignment_id,
            request.writer_generation,
            request.packet_digest,
            request.writer_owner_token_digest,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO bridge_nonces(nonce_digest, observed_at) VALUES (?, ?)",
                    (nonce_digest, request.timestamp),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise HostProviderBridgeError("bridge nonce replay") from exc
            row = connection.execute(
                "SELECT provider, task_id, assignment_id, writer_generation, packet_digest, writer_owner_token_digest, state, response_json "
                "FROM bridge_attempts WHERE idempotency_digest = ?",
                (request.idempotency_digest,),
            ).fetchone()
            if row is not None:
                if tuple(row[:6]) != bindings:
                    connection.rollback()
                    raise HostProviderBridgeError("idempotency key binding conflict")
                connection.commit()
                if row[6] == "SUCCEEDED" and isinstance(row[7], bytes):
                    return row[7]
                raise HostProviderBridgeAmbiguous("host provider attempt is not safely replayable")
            connection.execute(
                "INSERT INTO bridge_attempts VALUES (?, ?, ?, ?, ?, ?, ?, 'INFLIGHT', NULL, ?)",
                (request.idempotency_digest, *bindings, int(time.time())),
            )
            connection.commit()
        return None

    def settle(self, idempotency_digest: str, *, response: bytes | None) -> None:
        state = "SUCCEEDED" if response is not None else "AMBIGUOUS"
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE bridge_attempts SET state = ?, response_json = ?, updated_at = ? "
                "WHERE idempotency_digest = ? AND state = 'INFLIGHT'",
                (state, response, int(time.time()), idempotency_digest),
            )
            if cursor.rowcount != 1:
                raise HostProviderBridgeError("bridge attempt settlement lost its claim")


class WindowsHostProviderExecutor:
    """Execute one preconfigured Windows command once, without a shell."""

    def __init__(
        self,
        commands: Mapping[str, Sequence[str]],
        *,
        cwd: str | Path,
        timeout_seconds: int = 900,
        writer_fence_verifier: object | None = None,
    ) -> None:
        self.cwd = Path(cwd).resolve(strict=True)
        self.timeout_seconds = int(timeout_seconds)
        if self.timeout_seconds < 1 or self.timeout_seconds > 3600:
            raise ValueError("bridge timeout must be between 1 and 3600 seconds")
        normalized: dict[str, tuple[str, ...]] = {}
        for provider, command in commands.items():
            if provider in UNSUPPORTED_HOST_LANES:
                raise ValueError(f"unsupported bridge provider: {provider}")
            if provider not in SUPPORTED_HOST_LANES:
                raise ValueError(f"unallowlisted bridge provider: {provider}")
            argv = tuple(str(item) for item in command)
            if not argv or any(not item or "\x00" in item for item in argv):
                raise ValueError("bridge commands must be explicit argv arrays")
            normalized[provider] = argv
        self.commands = normalized
        self.writer_fence_verifier = writer_fence_verifier

    def execute(self, request: BridgeRequest) -> bytes:
        command = self.commands.get(request.provider)
        if command is None:
            raise HostProviderBridgeError("host provider command is not configured")
        verifier = self.writer_fence_verifier
        if verifier is None or not callable(getattr(verifier, "verify", None)):
            raise HostProviderBridgeError("current Postgres writer verification is unavailable")
        verifier.verify(request)
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in _SAFE_OS_ENV or name in _LANE_AUTH_ENV[request.provider]
        }
        try:
            completed = subprocess.run(
                command,
                cwd=self.cwd,
                input=request.packet,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                check=False,
                timeout=self.timeout_seconds,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostProviderBridgeAmbiguous("host provider outcome is ambiguous") from exc
        if completed.returncode != 0:
            raise HostProviderBridgeAmbiguous("host provider outcome is ambiguous")
        if not completed.stdout or len(completed.stdout) > _MAX_RESULT_BYTES:
            raise HostProviderBridgeAmbiguous("host provider returned no bounded result")
        try:
            result = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostProviderBridgeAmbiguous("host provider returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise HostProviderBridgeAmbiguous("host provider result is not a JSON object")
        return completed.stdout


class PostgresHostWriterFenceVerifier:
    """Read the authoritative writer row immediately before Windows CLI I/O."""

    def __init__(self, dsn: str, *, schema: str) -> None:
        if not dsn or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("host bridge Postgres writer verifier is misconfigured")
        self.dsn = dsn
        self.schema = schema

    def verify(self, request: BridgeRequest) -> None:
        try:
            import psycopg

            with psycopg.connect(self.dsn, connect_timeout=5) as connection:
                row = connection.execute(
                    f"SELECT engine_owner, owner_token, fencing_generation, "
                    f"lease_expires_at > now() AS lease_live "
                    f"FROM {self.schema}.director_primary_task_writer WHERE task_id = %s",
                    (request.task_id,),
                ).fetchone()
        except Exception as exc:
            raise HostProviderBridgeError("current Postgres writer verification failed") from exc
        if row is None:
            raise HostProviderBridgeError("current Postgres writer is unavailable")
        token_digest = "sha256:" + hashlib.sha256(str(row[1]).encode("utf-8")).hexdigest()
        if (
            str(row[0]) != "langgraph"
            or not hmac.compare_digest(token_digest, request.writer_owner_token_digest)
            or int(row[2]) != request.writer_generation
            or row[3] is not True
        ):
            raise HostProviderBridgeError("current Postgres writer fence does not match")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


class HostBridgePacketTransport:
    """Linux-side one-request client with signed packet and writer bindings."""

    def __init__(
        self,
        provider: str,
        endpoint: str,
        secret: str,
        *,
        writer_owner_token_digest: str,
        outbound_payload_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]]
        | None = None,
        result_normalizer: Callable[
            [Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]
        ]
        | None = None,
    ) -> None:
        if provider in UNSUPPORTED_HOST_LANES or provider not in SUPPORTED_HOST_LANES:
            raise ValueError("provider is not supported by the Windows host bridge")
        # Redaction and local normalization are one decision: a lane that is not
        # shown the work packet cannot have its result bound by the host either.
        if (outbound_payload_builder is None) != (result_normalizer is None):
            raise ValueError(
                "host bridge redaction requires both a payload builder and a "
                "result normalizer"
            )
        parsed = urlsplit(endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("host bridge endpoint port is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"host.docker.internal", "localhost", "127.0.0.1", "::1"}
            or (
                parsed.scheme == "http"
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != _BRIDGE_PATH
        ):
            raise ValueError("host bridge endpoint is not an exact local dispatch URL")
        if len(secret) < 32:
            raise ValueError("host bridge secret must contain at least 32 characters")
        self.provider = provider
        if not writer_owner_token_digest.startswith("sha256:") or len(writer_owner_token_digest) != 71:
            raise ValueError("host bridge writer owner token digest is invalid")
        self.endpoint = endpoint
        self.secret = secret
        self.writer_owner_token_digest = writer_owner_token_digest
        self.outbound_payload_builder = outbound_payload_builder
        self.result_normalizer = result_normalizer

    def send(self, work_packet_json: bytes, *, context: ProviderAdapterContext) -> Mapping[str, object]:
        if not work_packet_json or len(work_packet_json) > _MAX_PACKET_BYTES:
            raise RuntimeError("host bridge work packet is not bounded")
        packet = json.loads(work_packet_json)
        task_id = _require_token(str(packet.get("task_id", "")), "task id")
        assignment_id = _require_token(str(packet.get("assignment_id", "")), "assignment id")
        idempotency_key = str(packet.get("idempotency_key", ""))
        if not idempotency_key:
            raise RuntimeError("host bridge packet lacks idempotency")
        work_packet_digest = ""
        outbound = work_packet_json
        if self.outbound_payload_builder is not None:
            # A redacted lane posts a freshly built closed document. The work
            # packet stays Director-side and is only ever referenced by digest.
            outbound = canonical_json_bytes(self.outbound_payload_builder(packet))
            if not outbound or len(outbound) > _MAX_PACKET_BYTES:
                raise RuntimeError("host bridge redacted payload is not bounded")
            work_packet_digest = _digest(work_packet_json)
        # The transport signature binds the exact bytes that leave this process.
        packet_digest = _digest(outbound)
        idempotency_digest = _digest(idempotency_key.encode("utf-8"))
        timestamp = int(time.time())
        nonce = hashlib.sha256(f"{time.time_ns()}:{packet_digest}".encode()).hexdigest()
        bindings = dict(
            timestamp=timestamp,
            nonce=nonce,
            provider=self.provider,
            task_id=task_id,
            assignment_id=assignment_id,
            idempotency_digest=idempotency_digest,
            writer_generation=context.fencing_generation,
            packet_digest=packet_digest,
            writer_owner_token_digest=self.writer_owner_token_digest,
            work_packet_digest=work_packet_digest,
        )
        headers = {
            "Content-Type": "application/json",
            "X-Director-Bridge-Protocol": BRIDGE_PROTOCOL,
            "X-Director-Bridge-Timestamp": str(timestamp),
            "X-Director-Bridge-Nonce": nonce,
            "X-Director-Provider": self.provider,
            "X-Director-Task": task_id,
            "X-Director-Assignment": assignment_id,
            "X-Director-Idempotency-Digest": idempotency_digest,
            "X-Director-Writer-Generation": str(context.fencing_generation),
            "X-Director-Packet-Digest": packet_digest,
            "X-Director-Writer-Owner-Token-Digest": self.writer_owner_token_digest,
            "Authorization": "Director-HMAC " + bridge_signature(self.secret, **bindings),
        }
        if work_packet_digest:
            headers["X-Director-Work-Packet-Digest"] = work_packet_digest
        request = urllib.request.Request(self.endpoint, data=outbound, headers=headers, method="POST")
        try:
            with urllib.request.build_opener(_NoRedirectHandler()).open(
                request, timeout=context.timeout_seconds
            ) as response:
                payload = response.read(_MAX_RESULT_BYTES + 1)
                response_digest = response.headers.get("X-Director-Response-Digest", "")
                response_proof = response.headers.get("X-Director-Response-Proof", "")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("host provider bridge outcome is ambiguous") from exc
        if not payload or len(payload) > _MAX_RESULT_BYTES:
            raise RuntimeError("host provider bridge returned no bounded result")
        observed_digest = _digest(payload)
        expected_proof = bridge_response_signature(
            self.secret,
            nonce=nonce,
            idempotency_digest=idempotency_digest,
            response_digest=observed_digest,
        )
        if response_digest != observed_digest or not hmac.compare_digest(
            response_proof, expected_proof
        ):
            raise RuntimeError("host provider bridge response authentication failed")
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise RuntimeError("host provider bridge result is malformed")
        if self.result_normalizer is None:
            return decoded
        # The host only ever saw the redacted document, so it cannot bind the
        # result. Normalization runs here, against the original work packet.
        normalized = self.result_normalizer(packet, decoded)
        if not isinstance(normalized, Mapping):
            raise RuntimeError("host provider bridge normalization is malformed")
        return dict(normalized)


def dispatch_bridge_request(
    request: BridgeRequest,
    *,
    secret: str,
    store: BridgeClaimStore,
    executor: WindowsHostProviderExecutor,
    now: int | None = None,
) -> bytes:
    """Validate, claim, execute once, and durably settle one bridge request."""

    request.validate(secret, now=now)
    replay = store.claim(request)
    if replay is not None:
        return replay
    try:
        result = executor.execute(request)
    except Exception:
        store.settle(request.idempotency_digest, response=None)
        raise
    store.settle(request.idempotency_digest, response=result)
    return result


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:42 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
