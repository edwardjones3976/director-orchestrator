# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 18:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:49 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 04:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 03:56 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:42 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-21 | 22:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
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
import http.client
import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from functools import partial
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .langgraph_runtime import ProviderAdapterContext
from .packet_contracts import canonical_json_bytes


BRIDGE_PROTOCOL = "director-host-provider-bridge/v1"
SUPPORTED_HOST_LANES = frozenset(
    {"openai", "claude", "cursor", "grok", "antigravity", "github-copilot", "zai"}
)
_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PACKET_BYTES = 1_048_576
_MAX_RESULT_BYTES = 1_048_576
_BRIDGE_PATH = "/v1/provider/dispatch"
_BRIDGE_CHALLENGE_PATH = "/v1/provider/challenge"
_SAFE_OS_ENV = frozenset(
    {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC", "LOCALAPPDATA", "APPDATA", "USERPROFILE"}
)
# Proxy and CA names httpx honours in the bridge process (never passed to a
# host-command child). Compared upper-cased, so lowercase variants match.
_TRANSPORT_ENV = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    # ssl.create_default_context writes TLS session keys to this file.
    "SSLKEYLOGFILE",
})
_LANE_AUTH_ENV = {
    "openai": frozenset(),
    "cursor": frozenset(),
    "claude": frozenset({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
    "grok": frozenset({"XAI_API_KEY"}),
    "antigravity": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "github-copilot": frozenset({"GH_TOKEN", "GITHUB_TOKEN"}),
    # Read by the bridge process only: the Z.ai route passes it to its child as
    # ANTHROPIC_AUTH_TOKEN after checking the pinned key ID; never by this name.
    "zai": frozenset({"ZAI_API_KEY"}),
}


class HostProviderBridgeError(RuntimeError):
    """Fail-closed bridge or binding error."""


class HostProviderBridgeAmbiguous(HostProviderBridgeError):
    """An external attempt may have happened and cannot be resent."""


class HostProviderBridgeRefused(HostProviderBridgeError):
    """The executor proved it started nothing (for example an unsupported grant).

    Settled as REFUSED and answered with a signed refusal, like an attestation
    mismatch, so the Director can tell it apart from an ambiguous attempt.
    """


class HostBridgeRefusedBeforeExecution(RuntimeError):
    """A proven no-run: nothing was executed for this request.

    Raised only for the Director's own refusal before anything was signed or
    sent, or for the bridge's signed refusal (first answer or replay). An
    unsigned or unreadable error is never this; it stays ambiguous.
    """


# What a REFUSED claim records in its response column, so a replay repeats the
# same signed disposition. A claim settled before reasons were recorded holds
# NULL and keeps replaying as an attestation mismatch.
_REFUSAL_REASONS = ("attestation_mismatch", "refused_before_execution")


class HostProviderBridgeAttestationMismatch(HostProviderBridgeError):
    """The argv about to run is not the one the request's attestation pinned.

    Raised before any process is started, so nothing was executed.
    """


class HostBridgeAttestationStale(RuntimeError):
    """The bridge authenticated that it refused to run an unattested argv."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _socket_of(stream: object) -> Any:
    """The socket under an HTTP response, an HTTPError or a request file."""

    for path in (("fp", "raw", "_sock"), ("fp", "fp", "raw", "_sock"), ("raw", "_sock"),
                 ("_sock",)):
        current = stream
        for name in path:
            current = getattr(current, name, None)
            if current is None:
                break
        if current is not None and callable(getattr(current, "settimeout", None)):
            return current
    return None


def _clamp(sock: Any, remaining: float) -> None:
    """Clamp the next socket wait to what is left. A socket already closed
    (a fully buffered response) has nothing left to wait for."""

    if sock is None:
        return
    try:
        sock.settimeout(remaining)
    except OSError:
        pass


def read_until_deadline(stream: object, *, limit: int, deadline: float,
                        exact: bool = False, sock: Any = None) -> bytes:
    """Read at most ``limit + 1`` bytes (exactly ``limit`` when ``exact``),
    never past the absolute ``deadline``.

    Small chunks, each with its socket timeout clamped to what is left, and
    the clock checked after every chunk: a peer trickling bytes cannot keep a
    read open past the deadline the way a per-read inactivity timeout lets
    it. Raises TimeoutError at the deadline.
    """

    sock = sock if sock is not None else _socket_of(stream)
    target = limit if exact else limit + 1
    reader = getattr(stream, "read1", None)
    if not callable(reader):
        # A stream without chunked reads gets one read, still bounded to
        # what is left and checked against the clock after it.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("bounded read reached its deadline")
        _clamp(sock, remaining)
        data = stream.read(target)
        if time.monotonic() >= deadline:
            raise TimeoutError("bounded read reached its deadline")
        if exact and len(data) != limit:
            raise ValueError("request body ended before its declared length")
        return data
    chunks: list[bytes] = []
    size = 0
    while size < target:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("bounded read reached its deadline")
        _clamp(sock, remaining)
        chunk = reader(min(8192, target - size))
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("a bounded read returned something other than bytes")
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if time.monotonic() >= deadline:
            raise TimeoutError("bounded read reached its deadline")
    if exact and size != limit:
        raise ValueError("request body ended before its declared length")
    return b"".join(chunks)


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
    host_deadline: str = "",
    attested_argv_digest: str = "",
    attempt_budget_usd: str = "",
    total_token_limit: str = "",
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
    if host_deadline:
        material.extend(("host-monotonic-deadline", host_deadline))
    # A small read-only task's request carries the argv digest its lane was
    # attested under; appended only when present, like the other bindings.
    if attested_argv_digest:
        material.extend(("attested-argv", attested_argv_digest))
    # A small task's attempt limits, enforced by the host before launch.
    if attempt_budget_usd or total_token_limit:
        material.extend(("attempt-limits", attempt_budget_usd, total_token_limit))
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
    secret: str, *, timestamp: int, nonce: str, command_map_digest: str = "",
    host_monotonic: str = "", lane_attestation_digest: str = "",
) -> str:
    if len(secret) < 32:
        raise HostProviderBridgeError("bridge secret must contain at least 32 characters")
    material = "\n".join(
        (BRIDGE_PROTOCOL, "challenge", str(timestamp), nonce, command_map_digest)
    )
    if host_monotonic:
        material += "\nhost-monotonic-deadline\n" + host_monotonic
    # Appended only when present, so a bridge without attestations keeps a
    # byte-identical proof and a stripped attestation fails authentication.
    if lane_attestation_digest:
        material += "\nlane-attestations\n" + lane_attestation_digest
    material = material.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class HostBridgeChallengeEvidence:
    """One fresh authenticated observation of the bridge command map."""

    configured_providers: frozenset[str]
    command_map_digest: str
    observed_at: int
    host_monotonic: float | None = None
    clock_id: str = ""
    # lane -> argv digest, for lanes whose reported argv this side re-checked
    # and found carrying its whole read-only restriction set.
    read_only_attested: Mapping[str, str] | None = None
    # lane -> the exact argv the signed challenge reported for it (for example
    # the Z.ai lane's executable, GLM model, bounds and key-ID digest).
    lane_argv: Mapping[str, tuple[str, ...]] | None = None


def challenge_host_provider_bridge(
    endpoint: str,
    secret: str,
    *,
    timeout_seconds: float = 5,
    now: int | None = None,
    deadline: float | None = None,
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
    # One absolute bound for the whole challenge, never past the caller's.
    read_deadline = time.monotonic() + timeout_seconds
    if deadline is not None:
        read_deadline = min(read_deadline, deadline)
    try:
        with _bridge_opener(read_deadline) as (opener, watch):
            with opener.open(
                request, timeout=max(0.001, read_deadline - time.monotonic())
            ) as response:
                payload = read_until_deadline(response, limit=65_536, deadline=read_deadline)
            if watch.fired.is_set():
                raise TimeoutError("host bridge challenge reached its deadline")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        raise HostProviderBridgeError("host bridge challenge is unavailable") from exc
    if not payload or len(payload) > 65_536:
        raise HostProviderBridgeError("host bridge challenge response is not bounded")
    if deadline is not None and time.monotonic() >= deadline:
        raise HostProviderBridgeError("host bridge challenge deadline expired")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostProviderBridgeError("host bridge challenge response is malformed") from exc
    providers = decoded.get("configured_providers") if isinstance(decoded, dict) else None
    digest = decoded.get("command_map_digest") if isinstance(decoded, dict) else None
    proof = decoded.get("proof") if isinstance(decoded, dict) else None
    host_clock = decoded.get("host_monotonic", "") if isinstance(decoded, dict) else ""
    try:
        if not isinstance(host_clock, str):
            raise ValueError("invalid clock type")
        clock_id, clock_text = host_clock.split(":", 1) if host_clock else ("", "")
        if host_clock and re.fullmatch(r"[0-9a-f]{32}", clock_id) is None:
            raise ValueError("invalid clock identity")
        clock_value = float.fromhex(clock_text) if host_clock else None
        if clock_value is not None and (not math.isfinite(clock_value) or clock_value < 0):
            raise ValueError("invalid clock")
    except (TypeError, ValueError):
        raise HostProviderBridgeError("host bridge clock is invalid") from None
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
    attestations = decoded.get("lane_attestations")
    attestation_digest = ""
    attested: dict[str, str] = {}
    reported: dict[str, tuple[str, ...]] = {}
    if attestations is not None:
        from .bridge_attestation import argv_digest, read_only_attested
        if not isinstance(attestations, dict):
            raise HostProviderBridgeError("host bridge lane attestations are malformed")
        attestation_digest = "sha256:" + _digest(canonical_json_bytes(attestations))
        if decoded.get("lane_attestation_digest") != attestation_digest:
            raise HostProviderBridgeError("host bridge lane attestations are not bound")
        for lane, item in attestations.items():
            argv = item.get("argv") if isinstance(item, dict) else None
            if (not isinstance(argv, list) or not all(isinstance(value, str) for value in argv)
                    or item.get("argv_digest") != argv_digest(argv)):
                raise HostProviderBridgeError("host bridge lane attestation is malformed")
            reported[lane] = tuple(argv)
            # Re-checked here: the host's own verdict is required, never
            # trusted, and the lane must also enforce the attempt limits.
            # Whether the command also enforces the attempt limits before
            # launch is reported but not required (owner ruling 2026-09-24,
            # "Accept post-hoc cap"): the cap is checked on the result.
            if item.get("read_only_attested") is True and read_only_attested(lane, argv):
                attested[lane] = item["argv_digest"]
    expected = bridge_challenge_signature(
        secret, timestamp=timestamp, nonce=nonce, command_map_digest=digest,
        host_monotonic=host_clock, lane_attestation_digest=attestation_digest,
    )
    if not hmac.compare_digest(proof, expected):
        raise HostProviderBridgeError("host bridge challenge authentication failed")
    # Reported argv is trusted only after the proof above binds it.
    return HostBridgeChallengeEvidence(frozenset(providers), digest, timestamp, clock_value,
                                       clock_id, attested, reported)


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
    host_deadline: str = ""
    attested_argv_digest: str = ""
    attempt_budget_usd: str = ""
    total_token_limit: str = ""

    def validate(self, secret: str, *, now: int | None = None, max_age_seconds: int = 60) -> None:
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
        if not self.writer_owner_token_digest.startswith("sha256:") or _HEX_64.fullmatch(self.writer_owner_token_digest[7:]) is None:
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
            host_deadline=self.host_deadline,
            attested_argv_digest=self.attested_argv_digest,
            attempt_budget_usd=self.attempt_budget_usd,
            total_token_limit=self.total_token_limit,
        )
        if (self.attempt_budget_usd or self.total_token_limit) and not (
                re.fullmatch(r"\d+(?:\.\d+)?", self.attempt_budget_usd)
                and re.fullmatch(r"[1-9]\d{0,8}", self.total_token_limit)):
            raise HostProviderBridgeError("attempt limits are malformed")
        if self.attested_argv_digest and (
                not self.attested_argv_digest.startswith("sha256:")
                or _HEX_64.fullmatch(self.attested_argv_digest[7:]) is None):
            raise HostProviderBridgeError("attested argv digest is invalid")
        if not hmac.compare_digest(expected, self.signature):
            raise HostProviderBridgeError("host bridge authentication failed")
        if self.host_deadline:
            try:
                clock_id, clock_text = self.host_deadline.split(":", 1)
                if re.fullmatch(r"[0-9a-f]{32}", clock_id) is None:
                    raise ValueError("invalid clock identity")
                cutoff = float.fromhex(clock_text)
                remaining = cutoff - time.monotonic()
                if not math.isfinite(cutoff) or not 0 < remaining <= 3600:
                    raise ValueError("invalid deadline")
            except (TypeError, ValueError):
                raise HostProviderBridgeError("host bridge deadline expired or invalid") from None
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
                CREATE TABLE IF NOT EXISTS bridge_attempt_limits (
                    idempotency_digest TEXT PRIMARY KEY,
                    attested_argv_digest TEXT NOT NULL,
                    attempt_budget_usd TEXT NOT NULL,
                    total_token_limit TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bridge_attempts (
                    idempotency_digest TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    writer_generation INTEGER NOT NULL,
                    packet_digest TEXT NOT NULL,
                    writer_owner_token_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('INFLIGHT','SUCCEEDED','AMBIGUOUS','REFUSED')),
                    response_json BLOB,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            # A store created before the REFUSED state existed: rebuild the
            # table with the widened CHECK, keeping every row.
            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'bridge_attempts'"
            ).fetchone()[0]
            if "'REFUSED'" not in definition:
                connection.executescript(
                    "BEGIN IMMEDIATE;"
                    "ALTER TABLE bridge_attempts RENAME TO bridge_attempts_before_refused;"
                    + definition.replace("'AMBIGUOUS')", "'AMBIGUOUS','REFUSED')") + ";"
                    "INSERT INTO bridge_attempts SELECT * FROM bridge_attempts_before_refused;"
                    "DROP TABLE bridge_attempts_before_refused;"
                    "COMMIT;"
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
                stored = connection.execute(
                    "SELECT attested_argv_digest, attempt_budget_usd, total_token_limit "
                    "FROM bridge_attempt_limits WHERE idempotency_digest = ?",
                    (request.idempotency_digest,),
                ).fetchone()
                connection.commit()
                if (None if stored is None else tuple(stored)) != _attempt_limits(request):
                    # Signed under a different attestation or different
                    # limits than the attempt this key claimed: never
                    # replayed. An AMBIGUOUS attempt stays ambiguous; one
                    # that ran (SUCCEEDED) is refused as a binding conflict,
                    # never as a no-run, because its command did run; only a
                    # REFUSED attempt, which ran nothing, is the signed
                    # attestation refusal.
                    if row[6] == "REFUSED":
                        _raise_recorded_refusal(row[7],
                            "replay attestation or limits differ from the claimed attempt")
                    if row[6] == "SUCCEEDED":
                        raise HostProviderBridgeError(
                            "replay attestation or limits differ from the claimed attempt")
                    raise HostProviderBridgeAmbiguous(
                        "host provider attempt is not safely replayable")
                if row[6] == "SUCCEEDED" and isinstance(row[7], bytes):
                    return row[7]
                if row[6] == "REFUSED":
                    # Replayed as the same clean refusal: nothing ever ran.
                    _raise_recorded_refusal(row[7], "host provider attempt was refused before execution")
                raise HostProviderBridgeAmbiguous("host provider attempt is not safely replayable")
            connection.execute(
                "INSERT INTO bridge_attempts VALUES (?, ?, ?, ?, ?, ?, ?, 'INFLIGHT', NULL, ?)",
                (request.idempotency_digest, *bindings, int(time.time())),
            )
            limits = _attempt_limits(request)
            if limits is not None:
                # The attestation and limits this attempt was claimed under;
                # a replay must carry exactly these.
                connection.execute(
                    "INSERT INTO bridge_attempt_limits VALUES (?, ?, ?, ?)",
                    (request.idempotency_digest, *limits),
                )
            connection.commit()
        return None

    def settle(self, idempotency_digest: str, *, response: bytes | None,
               refused: bool = False, refusal_reason: str | None = None) -> None:
        """Settle an INFLIGHT claim: SUCCEEDED with its response, REFUSED when
        the executor proved it started nothing, otherwise AMBIGUOUS. A REFUSED
        claim stores its signed refusal reason for replay."""

        state = ("REFUSED" if refused else
                 "SUCCEEDED" if response is not None else "AMBIGUOUS")
        if refused:
            if response is not None or (refusal_reason is not None
                                        and refusal_reason not in _REFUSAL_REASONS):
                raise HostProviderBridgeError("a refused attempt records only its refusal reason")
            response = refusal_reason.encode("ascii") if refusal_reason else None
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE bridge_attempts SET state = ?, response_json = ?, updated_at = ? "
                "WHERE idempotency_digest = ? AND state = 'INFLIGHT'",
                (state, response, int(time.time()), idempotency_digest),
            )
            if cursor.rowcount != 1:
                raise HostProviderBridgeError("bridge attempt settlement lost its claim")


def _raise_recorded_refusal(recorded: object, message: str) -> None:
    """Replay a REFUSED claim with the disposition it was settled under."""
    if recorded == b"refused_before_execution":
        raise HostProviderBridgeRefused(message)
    raise HostProviderBridgeAttestationMismatch(message)


def _attempt_limits(request: object) -> tuple[str, str, str] | None:
    """The signed attestation digest and attempt limits a request carries, or
    None for a request that carries none of them."""

    values = tuple(str(getattr(request, name, "") or "") for name in (
        "attested_argv_digest", "attempt_budget_usd", "total_token_limit"))
    return values if any(values) else None


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
            if provider not in SUPPORTED_HOST_LANES:
                raise ValueError(f"unallowlisted bridge provider: {provider}")
            argv = tuple(str(item) for item in command)
            if not argv or any(not item or "\x00" in item for item in argv):
                raise ValueError("bridge commands must be explicit argv arrays")
            normalized[provider] = argv
        self.native_codex = None
        if "openai" in normalized:
            from .native_codex_transport import NativeCodexPacketTransport
            command = normalized["openai"]
            if len(command) != 2:
                raise ValueError("OpenAI host command must be [native executable, model]")
            self.native_codex = NativeCodexPacketTransport(*command)
        self.native_zai = None
        if "zai" in normalized:
            # Like native Codex: the entry configures an in-process transport
            # (the GLM-verifying Z.ai native transport), never a generic child.
            from .zai_native import ZaiHostCommand
            self.native_zai = ZaiHostCommand.parse(normalized["zai"])
        self.commands = normalized
        self.writer_fence_verifier = writer_fence_verifier

    def _zai_config(self):
        """The host's Z.ai configuration, or None when its key, key ID or CLI is unusable."""
        if self.native_zai is None:
            return None
        return self.native_zai.config(os.environ.get("ZAI_API_KEY", ""),
                                      timeout_seconds=self.timeout_seconds)

    def available_providers(self) -> list[str]:
        providers = sorted(self.commands)
        if self.native_codex is not None and not self.native_codex.authenticated():
            providers.remove("openai")
        if self.native_zai is not None and self._zai_config() is None:
            providers.remove("zai")
        return providers

    def _execute_zai(self, request: BridgeRequest, *, cutoff: float, execution_observer=None) -> bytes:
        """Run the Z.ai lane once through its GLM-verifying native transport."""
        from types import SimpleNamespace
        from .packet_contracts import PacketContractError, validate_packet
        from .zai_native import ZaiNativePacketTransport, tool_arguments

        config = self._zai_config()
        if config is None:
            raise HostProviderBridgeRefused("the Z.ai host binding is unavailable")
        try:
            work = validate_packet("work", json.loads(request.packet))
        except (PacketContractError, ValueError) as exc:
            raise HostProviderBridgeRefused("the Z.ai work packet is invalid") from exc
        context = SimpleNamespace(
            fencing_generation=request.writer_generation,
            timeout_seconds=max(1, min(self.timeout_seconds, int(work["limits"]["timeout_seconds"]))),
            budget_usd=Decimal(request.attempt_budget_usd) if request.attempt_budget_usd else None,
            total_token_limit=int(request.total_token_limit) if request.total_token_limit else None,
            allowed_tools=tuple(work["allowed_tools"]),
            allowed_external_effects=tuple(work["allowed_external_effects"]),
            execution_observer=execution_observer,
            deadline=cutoff,
        )
        try:
            # The same refusals the transport applies, first: nothing launches.
            tool_arguments(work, context, allow_repository_write=config.allow_repository_write)
            transport = ZaiNativePacketTransport(config, cwd=self.cwd)
        except (RuntimeError, ValueError, OSError) as exc:
            raise HostProviderBridgeRefused("the Z.ai request is outside this host's grant") from exc
        try:
            result = transport.send(request.packet, context=context)
        except (RuntimeError, OSError, ValueError) as exc:
            raise HostProviderBridgeAmbiguous("Z.ai outcome is unavailable or ambiguous") from exc
        return canonical_json_bytes(result)

    def execute(self, request: BridgeRequest, *, execution_observer=None) -> bytes:
        cutoff = time.monotonic() + self.timeout_seconds
        if request.host_deadline:
            cutoff = min(cutoff, float.fromhex(request.host_deadline.split(":", 1)[1]))
        command = self.commands.get(request.provider)
        if command is None:
            raise HostProviderBridgeError("host provider command is not configured")
        verifier = self.writer_fence_verifier
        if verifier is None or not callable(getattr(verifier, "verify", None)):
            raise HostProviderBridgeError("current Postgres writer verification is unavailable")
        verifier.verify(request)
        if time.monotonic() >= cutoff:
            raise HostProviderBridgeError("host bridge deadline expired before execution")
        attested = getattr(request, "attested_argv_digest", "")
        budget = getattr(request, "attempt_budget_usd", "")
        tokens = getattr(request, "total_token_limit", "")
        if attested:
            # Immediately before execution: the argv about to run must be the
            # one this request's lane was attested and pinned under.
            from .bridge_attestation import argv_digest
            if not hmac.compare_digest(argv_digest(command), attested):
                raise HostProviderBridgeAttestationMismatch(
                    "host argv differs from the attested argv")
        if attested and not (budget and tokens):
            # A small task's request always carries its attempt limits. They
            # reach the command as its limit variables; a command that cannot
            # honor them before launch (the Grok CLI has no such flag) is
            # still run, by the owner's ruling of 2026-09-24 ("Accept post-hoc
            # cap"): the Director checks reported usage against them after
            # the call, and an overage fails the task and trips the grant.
            raise HostProviderBridgeAttestationMismatch(
                "a small task's request lacks its attempt limits")
        if request.provider == "openai":
            from types import SimpleNamespace
            from .packet_contracts import validate_packet
            work = validate_packet("work", json.loads(request.packet))
            context = SimpleNamespace(
                fencing_generation=request.writer_generation,
                max_hops=work["limits"]["max_hops"],
                timeout_seconds=min(self.timeout_seconds, work["limits"]["timeout_seconds"]),
                allowed_external_effects=tuple(work["allowed_external_effects"]),
                execution_observer=execution_observer,
                deadline=cutoff,
            )
            try:
                result = self.native_codex.send(request.packet, context=context)
            except (RuntimeError, OSError, ValueError) as exc:
                raise HostProviderBridgeAmbiguous("native Codex outcome is unavailable or ambiguous") from exc
            return canonical_json_bytes(result)
        if request.provider == "zai":
            return self._execute_zai(request, cutoff=cutoff, execution_observer=execution_observer)
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in _SAFE_OS_ENV or name in _LANE_AUTH_ENV[request.provider]
        }
        if getattr(request, "total_token_limit", ""):
            # The same names NativeCommandPacketTransport sets for a command.
            environment["DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD"] = request.attempt_budget_usd
            environment["DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT"] = request.total_token_limit
        try:
            if request.host_deadline:
                # Reuse the existing process-tree owner; killing only the shim
                # can otherwise leave its model CLI running after the cutoff.
                from .primary_transports import _run_bounded_command
                completed = _run_bounded_command(
                    command, cwd=self.cwd, environment=environment,
                    input_bytes=request.packet, cutoff=cutoff, what="host provider",
                )
            else:
                completed = subprocess.run(
                    command, cwd=self.cwd, input=request.packet,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    shell=False, check=False, timeout=self.timeout_seconds,
                    env=environment,
                )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
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
        if execution_observer is not None:
            execution_observer(request.provider, completed.stdout, None)
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


class _SocketWatch:
    """The sockets one bridge request opened, and whether its deadline fired."""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.fired = threading.Event()
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()

    def register(self, sock: socket.socket) -> socket.socket:
        with self._lock:
            self._sockets.append(sock)
            fired = self.fired.is_set()
        if fired:
            self._shut(sock)
        return sock

    def fire(self) -> None:
        with self._lock:
            self.fired.set()
            sockets = list(self._sockets)
        for sock in sockets:
            self._shut(sock)

    @staticmethod
    def _shut(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# At most this many name lookups may be outstanding at once, counting ones
# that timed out and still hold a daemon thread in getaddrinfo(); past it a
# connect fails closed instead of starting another thread.
_RESOLVER_SLOTS = threading.BoundedSemaphore(8)


def _time_left(deadline: float, cap: Any = None) -> float:
    """Seconds before the monotonic ``deadline``, rounded DOWN to the
    microsecond and clamped to a numeric ``cap``: ``(t + 0.4) - t`` in floating
    point can come out as 0.40000000002, and a timeout derived from the
    deadline must never exceed its cap or the time actually left."""

    left = math.floor((deadline - time.monotonic()) * 1_000_000) / 1_000_000
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        left = min(float(cap), left)
    return left


def connect_until_deadline(
    address: tuple[str, int], timeout: Any = None, source_address: Any = None, *,
    deadline: float, socket_factory: Callable[..., socket.socket] = socket.socket,
    resolve: Callable[..., Any] = socket.getaddrinfo,
) -> socket.socket:
    """Resolve and connect, never past the absolute ``deadline``.

    Name resolution runs in a daemon thread given only the time remaining
    (it cannot otherwise be interrupted), and at most ``_RESOLVER_SLOTS``
    lookups may be outstanding; each resolved address is then tried with its
    connect timeout clamped to what is left, stopping once the deadline
    passes, where socket.create_connection gives every address the whole
    timeout again. A socket that connects only after the deadline is closed.
    """

    host, port = address
    remaining = _time_left(deadline)
    if remaining <= 0:
        raise TimeoutError("bridge connect deadline reached before resolving")
    if not _RESOLVER_SLOTS.acquire(blocking=False):
        raise TimeoutError("bridge name resolution capacity exhausted")
    found: dict[str, Any] = {}
    slot_released = False
    slot_lock = threading.Lock()

    def release_slot() -> None:
        nonlocal slot_released
        with slot_lock:
            if slot_released:
                return
            slot_released = True
        _RESOLVER_SLOTS.release()

    def lookup() -> None:
        try:
            found["infos"] = resolve(host, port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            found["error"] = exc
        finally:
            release_slot()

    try:
        resolver = threading.Thread(target=lookup, daemon=True)
        resolver.start()
    except BaseException:
        release_slot()
        raise
    resolver.join(remaining)
    if resolver.is_alive():
        raise TimeoutError("bridge name resolution reached its deadline")
    if "error" in found:
        raise found["error"]
    last: OSError | None = None
    for family, kind, protocol, _name, sockaddr in found.get("infos") or ():
        remaining = _time_left(deadline, timeout)
        if remaining <= 0:
            raise TimeoutError("bridge connect reached its deadline")
        sock = socket_factory(family, kind, protocol)
        try:
            sock.settimeout(remaining)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
        except OSError as exc:
            last = exc
            sock.close()
            continue
        left = _time_left(deadline, timeout)
        if left <= 0:
            sock.close()
            raise TimeoutError("bridge connect completed after its deadline")
        sock.settimeout(left)
        return sock
    raise last if last is not None else OSError("bridge host resolved to no address")


def _watched(connection_class: type, watch: _SocketWatch) -> Callable[..., Any]:
    def build(*args: Any, **kwargs: Any) -> Any:
        connection = connection_class(*args, **kwargs)
        # Resolution and connect spend the request's absolute deadline, and
        # the raw TCP socket is registered with the watchdog before any TLS
        # handshake or header.
        connection._create_connection = lambda address, timeout=None, source_address=None: (
            watch.register(connect_until_deadline(
                address, timeout, source_address, deadline=watch.deadline)))
        return connection
    return build


class _WatchedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, watch: _SocketWatch) -> None:
        super().__init__()
        self._watch = watch

    def http_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_watched(http.client.HTTPConnection, self._watch), req)


class _WatchedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, watch: _SocketWatch) -> None:
        super().__init__()
        self._watch = watch

    def https_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_watched(http.client.HTTPSConnection, self._watch), req,
                            context=self._context)


@contextmanager
def _bridge_opener(deadline: float):
    """An opener whose whole request - connect, TLS, status line, headers and
    body - ends at the absolute ``deadline``.

    A per-read socket timeout only bounds inactivity, so a peer that trickles
    header bytes could hold ``open()`` past the deadline. A watchdog timer
    shuts down every socket the request opened when the deadline arrives;
    the blocked read then fails, and ``watch.fired`` tells the caller the
    failure (or any short result) is the deadline, never a success.
    """

    watch = _SocketWatch(deadline)
    timer = threading.Timer(max(0.0, deadline - time.monotonic()), watch.fire)
    timer.daemon = True
    timer.start()
    try:
        yield urllib.request.build_opener(
            _NoRedirectHandler(), _WatchedHTTPHandler(watch), _WatchedHTTPSHandler(watch)), watch
    finally:
        timer.cancel()


def _enforce_native_result_attempt_limits(
    result: Mapping[str, Any], *, context: ProviderAdapterContext
) -> dict[str, Any]:
    """Retain actual usage while refusing a result beyond its admitted attempt."""
    normalized = dict(result)
    usage = result.get("usage")
    if not isinstance(usage, Mapping):
        return normalized
    tokens = getattr(context, "total_token_limit", None)
    actual_tokens = usage.get("total_tokens")
    counts = (usage.get("input_tokens"), usage.get("output_tokens"))
    if all(type(value) is int and value >= 0 for value in counts):
        # Claude reports separate counts without a total. Compare the genuine
        # counts without adding a synthetic field to the retained usage record.
        actual_tokens = max(
            sum(counts), actual_tokens if type(actual_tokens) is int else 0
        )
    budget = getattr(context, "budget_usd", None)
    actual_cost = usage.get("cost_usd")
    over_tokens = (
        type(tokens) is int and type(actual_tokens) is int and actual_tokens > tokens
    )
    over_cost = False
    if budget is not None and actual_cost is not None:
        if isinstance(actual_cost, bool) or not isinstance(actual_cost, (int, float, Decimal)):
            raise RuntimeError("provider result reported cost is malformed")
        cost = Decimal(str(actual_cost))
        if not cost.is_finite():
            raise RuntimeError("provider result reported cost is malformed")
        over_cost = cost > Decimal(str(budget))
    if over_tokens or over_cost:
        blockers = result.get("blockers")
        if not isinstance(blockers, list):
            raise RuntimeError("provider result blockers are malformed")
        reason = "Provider usage exceeds the admitted attempt limit"
        normalized["status"] = "FAILED"
        normalized["blockers"] = list(blockers) + ([] if reason in blockers else [reason])
    return normalized


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
        read_only_argv_digest: str | None = None,
        request_guard: Callable[[Mapping[str, Any], Any], None] | None = None,
    ) -> None:
        if provider not in SUPPORTED_HOST_LANES:
            raise ValueError("provider is not supported by the Windows host bridge")
        # A redacted lane still requires both halves of its closed boundary.
        # Claude/Grok can also return native envelopes after seeing the full
        # packet; normalize those replies without changing the outbound bytes.
        # Z.ai returns its host-verified result for the full packet and is
        # re-checked by its own normalizer.
        native_full_packet = (
            provider in {"claude", "grok", "zai"} and outbound_payload_builder is None
        )
        if provider == "zai" and (result_normalizer is None or request_guard is None):
            raise ValueError("the Z.ai bridge lane requires its guard and result normalizer")
        if ((outbound_payload_builder is None) != (result_normalizer is None)
                and not native_full_packet):
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
        if not writer_owner_token_digest.startswith("sha256:") or _HEX_64.fullmatch(writer_owner_token_digest[7:]) is None:
            raise ValueError("host bridge writer owner token digest is invalid")
        self.endpoint = endpoint
        self.secret = secret
        self.writer_owner_token_digest = writer_owner_token_digest
        self.outbound_payload_builder = outbound_payload_builder
        self.result_normalizer = result_normalizer
        # Set only from a fresh authenticated attestation whose argv digest
        # equals the Director's pinned binding for this lane.
        self.read_only_argv_digest = read_only_argv_digest
        # Director-side refusals checked before anything is signed or sent.
        self.request_guard = request_guard

    def read_only_attestation_current(self, *, deadline: float | None = None) -> bool:
        """Re-challenge the bridge: the lane is still attested with the same argv.

        Any failure, a changed argv, or a lost attestation is False.
        """

        if self.read_only_argv_digest is None:
            return False
        # The HTTP timeout is what is left of the caller's shared deadline,
        # never a fresh window; nothing left refuses without a request.
        timeout = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
        if timeout <= 0:
            return False
        try:
            evidence = challenge_host_provider_bridge(
                self.endpoint, self.secret, timeout_seconds=timeout, deadline=deadline)
        except HostProviderBridgeError:
            return False
        return (self.provider in evidence.configured_providers
                and (evidence.read_only_attested or {}).get(self.provider)
                == self.read_only_argv_digest)

    def preflight(self, work_packet_json: bytes, *, context) -> None:
        """Check deadline support through the authenticated, non-dispatch probe."""
        if getattr(context, "deadline", None) is not None:
            self._host_cutoff(context)

    def _host_cutoff(self, context) -> tuple[float, str]:
        from .primary_transports import _command_cutoff
        if self.provider == "openai":
            raise RuntimeError("native Codex host route lacks process-tree deadline support")
        cutoff = _command_cutoff(context, 3600, "host bridge")
        remaining = cutoff - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("host bridge timeout is exhausted")
        evidence = challenge_host_provider_bridge(
            self.endpoint, self.secret,
            timeout_seconds=min(5, remaining), deadline=cutoff,
        )
        remaining = cutoff - time.monotonic()
        if (remaining <= 0 or evidence.host_monotonic is None
                or self.provider not in evidence.configured_providers):
            raise RuntimeError("host bridge deadline support is unavailable")
        # Subtract the entire return trip: this remote cutoff is conservative.
        # Monotonic clocks need not share an epoch or wall-clock synchronization.
        return cutoff, evidence.clock_id + ":" + float(evidence.host_monotonic + remaining).hex()

    def send(self, work_packet_json: bytes, *, context: ProviderAdapterContext) -> Mapping[str, object]:
        if not work_packet_json or len(work_packet_json) > _MAX_PACKET_BYTES:
            raise RuntimeError("host bridge work packet is not bounded")
        if self.request_guard is not None:
            # Refused here, nothing is challenged, signed or sent: a proven no-run.
            try:
                self.request_guard(json.loads(work_packet_json), context)
            except HostBridgeRefusedBeforeExecution:
                raise
            except RuntimeError as exc:
                raise HostBridgeRefusedBeforeExecution(
                    "the Director refused the request before sending it: " + str(exc)) from exc
        cutoff, host_deadline = (self._host_cutoff(context)
            if getattr(context, "deadline", None) is not None else (None, ""))
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
        # The final authority recheck, after the outbound bytes are built (a
        # cache miss may read files) and immediately before they are signed
        # and sent: nothing slow runs between it and the request.
        authority_recheck = getattr(context, "authority_recheck", None)
        if callable(authority_recheck):
            authority_recheck()
        # The transport signature binds the exact bytes that leave this process.
        packet_digest = _digest(outbound)
        # A small read-only task's packet (read tool only, no effect) binds the
        # argv this lane was attested under; the host refuses any other argv.
        attested_argv_digest = (
            self.read_only_argv_digest
            if self.read_only_argv_digest is not None
            and packet.get("allowed_tools") == ["READ_REPOSITORY"]
            and packet.get("allowed_external_effects") == []
            else "")
        # With it, the attempt's own limits, enforced by the host before launch.
        attempt_budget_usd = total_token_limit = ""
        if attested_argv_digest:
            budget = getattr(context, "budget_usd", None)
            tokens = getattr(context, "total_token_limit", None)
            if budget is None or type(tokens) is not int or tokens < 1:
                raise HostBridgeAttestationStale("a small task's attempt limits are missing")
            attempt_budget_usd = format(Decimal(str(budget)).normalize(), "f")
            total_token_limit = str(tokens)
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
            host_deadline=host_deadline,
            attested_argv_digest=attested_argv_digest,
            attempt_budget_usd=attempt_budget_usd,
            total_token_limit=total_token_limit,
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
        if host_deadline:
            headers["X-Director-Host-Deadline"] = host_deadline
        if attested_argv_digest:
            headers["X-Director-Attested-Argv-Digest"] = attested_argv_digest
            headers["X-Director-Attempt-Budget-Usd"] = attempt_budget_usd
            headers["X-Director-Total-Token-Limit"] = total_token_limit
        request = urllib.request.Request(self.endpoint, data=outbound, headers=headers, method="POST")
        remaining = context.timeout_seconds if cutoff is None else cutoff - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("host bridge deadline expired before dispatch")
        if cutoff is not None:
            remaining = cutoff - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("host bridge deadline expired before dispatch")
        read_deadline = (cutoff if cutoff is not None
                         else time.monotonic() + remaining)
        try:
            with _bridge_opener(read_deadline) as (opener, watch):
                with opener.open(request, timeout=remaining) as response:
                    payload = read_until_deadline(
                        response, limit=_MAX_RESULT_BYTES, deadline=read_deadline)
                    response_digest = response.headers.get("X-Director-Response-Digest", "")
                    response_proof = response.headers.get("X-Director-Response-Proof", "")
                if watch.fired.is_set():
                    raise TimeoutError("host bridge response reached its deadline")
        except urllib.error.HTTPError as exc:
            # Only an authenticated refusal proves nothing ran: an attested
            # argv mismatch, or a signed refusal before execution.
            if exc.code == 403:
                try:
                    body = read_until_deadline(exc, limit=4095, deadline=read_deadline)
                except (TimeoutError, OSError):
                    raise RuntimeError(
                        "host provider bridge outcome is ambiguous") from exc
                proof = bridge_response_signature(
                    self.secret, nonce=nonce, idempotency_digest=idempotency_digest,
                    response_digest=_digest(body))
                signed = (exc.headers.get("X-Director-Response-Digest", "") == _digest(body)
                          and hmac.compare_digest(
                              exc.headers.get("X-Director-Response-Proof", ""), proof))
                try:
                    reason = json.loads(body).get("reason") if signed else None
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    reason = None
                if reason == "attestation_mismatch" and attested_argv_digest:
                    raise HostBridgeAttestationStale(
                        "the bridge refused an argv other than the attested one") from exc
                if reason == "refused_before_execution":
                    raise HostBridgeRefusedBeforeExecution(
                        "the bridge refused the request before running anything") from exc
            raise RuntimeError("host provider bridge outcome is ambiguous") from exc
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            # The deadline watchdog, or any transport failure after the
            # request may have been sent: never a success.
            raise RuntimeError("host provider bridge outcome is ambiguous") from exc
        if cutoff is not None and time.monotonic() >= cutoff:
            raise RuntimeError("host provider bridge outcome is ambiguous: deadline expired")
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
        # Bind native output against the original work packet, including when
        # the host was only allowed to see a redacted document.
        normalized = self.result_normalizer(packet, decoded)
        if not isinstance(normalized, Mapping):
            raise RuntimeError("host provider bridge normalization is malformed")
        if self.provider in {"claude", "grok", "zai"}:
            return _enforce_native_result_attempt_limits(normalized, context=context)
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
    except (HostProviderBridgeAttestationMismatch, HostProviderBridgeRefused) as exc:
        # Raised before any process starts: a clean, replayable refusal, never
        # an ambiguous attempt. The reason is kept so a replay repeats it.
        store.settle(request.idempotency_digest, response=None, refused=True,
                     refusal_reason=("attestation_mismatch"
                                     if isinstance(exc, HostProviderBridgeAttestationMismatch)
                                     else "refused_before_execution"))
        raise
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

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:18 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-21 | 22:36 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:42 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 03:56 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-23 | 04:03 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 01:08 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 02:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:49 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 17:21 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 18:29 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 19:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 00:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | END]]
