# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:46 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:42 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:46 UTC | BEGIN]]
"""Windows HTTP entry point for the authenticated host-provider bridge."""

from __future__ import annotations

import argparse
import json
import hmac
import os
import ssl
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .host_provider_bridge import (
    BRIDGE_PROTOCOL,
    BridgeClaimStore,
    BridgeRequest,
    HostProviderBridgeAmbiguous,
    HostProviderBridgeError,
    PostgresHostWriterFenceVerifier,
    WindowsHostProviderExecutor,
    bridge_response_signature,
    bridge_challenge_signature,
    dispatch_bridge_request,
    _digest,
    _LANE_AUTH_ENV,
    _SAFE_OS_ENV,
)

PROCESS_ENVIRONMENT_VERSION = "director-native-process-environment/v1"
# Set only by the launcher: the lane credentials it copied from the bridge profile.
PROFILE_AUTH_NAMES = "DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _commands() -> dict[str, tuple[str, ...]]:
    try:
        decoded = json.loads(_required("DIRECTOR_HOST_PROVIDER_COMMANDS_JSON"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("DIRECTOR_HOST_PROVIDER_COMMANDS_JSON is invalid") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("DIRECTOR_HOST_PROVIDER_COMMANDS_JSON must be an object")
    commands: dict[str, tuple[str, ...]] = {}
    for provider, argv in decoded.items():
        if not isinstance(provider, str) or not isinstance(argv, list) or not all(
            isinstance(item, str) for item in argv
        ):
            raise RuntimeError("host provider command bindings must be string arrays")
        commands[provider] = tuple(argv)
    return commands


def _apply_pinned_process_environment() -> bool:
    """Pin every process name that child processes and the digest read.

    Provider children and production_configuration_digest read only
    _SAFE_OS_ENV and _LANE_AUTH_ENV names. The safe names come from the pinned
    file. A lane credential survives only when the launcher copied it from the
    bridge profile and listed it in PROFILE_AUTH_NAMES; every other inherited
    credential is dropped, so neither the digest nor a provider's billing route
    depends on the launching shell. Nothing from the file is echoed on refusal.
    """
    source = os.environ.get("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE", "").strip()
    if not source:
        return False
    try:
        path = Path(source)
        if not path.is_absolute():
            raise ValueError("relative pinned environment path")
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError("oversized pinned environment")
        document = json.loads(raw)
        if set(document) != {"version", "environment"} or document["version"] != PROCESS_ENVIRONMENT_VERSION:
            raise ValueError("unknown pinned environment contract")
        pinned = document["environment"]
        if not isinstance(pinned, dict) or set(pinned) != _SAFE_OS_ENV:
            raise ValueError("pinned environment names differ")
        if not all(isinstance(value, str) and value and not any(c in value for c in "\r\n\0")
                   for value in pinned.values()):
            raise ValueError("pinned environment value is invalid")
        directories = [part for part in pinned["PATH"].split(os.pathsep) if part]
        directories += [pinned[name] for name in ("TEMP", "TMP", "SYSTEMROOT", "USERPROFILE")]
        if not all(Path(directory).is_dir() for directory in directories):
            raise ValueError("pinned environment directory is unavailable")
        lane_auth = frozenset().union(*_LANE_AUTH_ENV.values())
        from_profile = {name for name in os.environ.get(PROFILE_AUTH_NAMES, "").split(",") if name}
        if not from_profile <= lane_auth:
            raise ValueError("unknown profile credential name")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE is invalid") from None
    preserved = {
        key: value for key, value in os.environ.items()
        if key.upper() not in _SAFE_OS_ENV and (key.upper() not in lane_auth or key in from_profile)
    }
    os.environ.clear()
    os.environ.update(preserved)
    os.environ.update(pinned)
    return True


def _account_files() -> dict[str, tuple[str, ...]]:
    raw = os.environ.get("DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON", "").strip()
    try:
        account_files = json.loads(raw) if raw else {}
    except ValueError:
        account_files = None
    if not isinstance(account_files, dict) or any(
        not isinstance(values,list) or not all(isinstance(value,str) for value in values)
        for values in account_files.values()
    ):
        raise RuntimeError("validation account-file bindings malformed")
    return {key: tuple(values) for key, values in account_files.items()}


def configuration_digests(executor, account_files) -> dict[str, str]:
    """Per-lane production pins; digests only, never paths or values."""
    from .provider_validation import production_configuration_digest
    return {
        lane: production_configuration_digest(executor, lane, account_files=tuple(files))
        for lane, files in sorted(account_files.items())
        if lane in executor.commands and files
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "DirectorHostBridge/1"
    # A restart invalidates deadlines mapped from an earlier host process.
    _clock_id = secrets.token_hex(16)

    def log_message(self, format: str, *args: object) -> None:
        # Do not echo packet paths, identities, headers, or provider output.
        return

    def _json(self, status: int, body: dict[str, object]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/v1/provider/challenge":
            try:
                timestamp = int(self.headers.get("X-Director-Bridge-Timestamp", "0"))
                nonce = self.headers.get("X-Director-Bridge-Nonce", "")
                authorization = self.headers.get("Authorization", "")
                if (
                    self.headers.get("X-Director-Bridge-Protocol") != BRIDGE_PROTOCOL
                    or not authorization.startswith("Director-HMAC ")
                    or abs(int(time.time()) - timestamp) > 60
                    or len(nonce) != 64
                ):
                    raise HostProviderBridgeError("invalid bridge challenge")
                expected = bridge_challenge_signature(
                    self.server.bridge_secret, timestamp=timestamp, nonce=nonce  # type: ignore[attr-defined]
                )
                if not hmac.compare_digest(
                    expected, authorization.removeprefix("Director-HMAC ")
                ):
                    raise HostProviderBridgeError("bridge challenge authentication failed")
                configured = self.server.executor.available_providers()  # type: ignore[attr-defined]
                command_map_digest = "sha256:" + _digest(
                    json.dumps(configured, separators=(",", ":")).encode("utf-8")
                )
                host_monotonic = self._clock_id + ":" + time.monotonic().hex()
                self._json(200, {
                    "ok": True, "protocol": BRIDGE_PROTOCOL,
                    "configured_providers": configured,
                    "command_map_digest": command_map_digest,
                    "host_monotonic": host_monotonic,
                    "proof": bridge_challenge_signature(
                        self.server.bridge_secret, timestamp=timestamp, nonce=nonce,  # type: ignore[attr-defined]
                        command_map_digest=command_map_digest,
                        host_monotonic=host_monotonic,
                    ),
                })
            except (HostProviderBridgeError, ValueError):
                self._json(403, {"ok": False})
            return
        if self.path == "/v1/provider/configuration-digest":
            service = getattr(self.server, "validation_service", None)
            if service is None:
                self._json(503, {"ok": False, "reason": "validation_unconfigured"})
                return
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer ") or not hmac.compare_digest(
                authorization.removeprefix("Bearer ").encode("utf-8"),
                service.owner_token.encode("utf-8"),
            ):
                self._json(401, {"ok": False})
                return
            try:
                digests = configuration_digests(service.production, service.account_files)
            except Exception:
                self._json(409, {"ok": False, "reason": "configuration_unavailable"})
                return
            self._json(200, {
                "ok": True, "configuration_digests": digests,
                "pinned_environment": bool(getattr(self.server, "pinned_environment", False)),
            })
            return
        if self.path != "/health":
            self._json(404, {"ok": False})
            return
        # Unauthenticated health stays at protocol plus the configured roster.
        # Per-provider booleans are neither part of this contract nor stable
        # against the canonical lane names; the authenticated challenge endpoint
        # already proves the exact command map.
        self._json(
            200,
            {
                "ok": True,
                "protocol": BRIDGE_PROTOCOL,
                "configured_providers": sorted(
                    self.server.executor.commands  # type: ignore[attr-defined]
                ),
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/v1/provider/validate":
            try:
                from .provider_validation import ValidationRequest
                service = getattr(self.server, "validation_service", None)
                if service is None:
                    self._json(503, {"ok": False, "reason": "validation_unconfigured"})
                    return
                authorization = self.headers.get("Authorization", "")
                if not authorization.startswith("Bearer "):
                    raise ValueError("validation authentication missing")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("validation request size invalid")
                request = ValidationRequest.model_validate_json(self.rfile.read(length))
                result = service.run(request, authorization.removeprefix("Bearer "))
                self._json(200, {"ok": True, **result})
            except Exception:
                # No provider output, account paths, tokens, or DB details in errors.
                self._json(409, {"ok": False, "reason": "validation_rejected_or_ambiguous"})
            return
        if self.path != "/v1/provider/dispatch":
            self._json(404, {"ok": False})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_048_576:
                raise HostProviderBridgeError("invalid packet length")
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Director-HMAC "):
                raise HostProviderBridgeError("missing authentication")
            request = BridgeRequest(
                provider=self.headers.get("X-Director-Provider", ""),
                task_id=self.headers.get("X-Director-Task", ""),
                assignment_id=self.headers.get("X-Director-Assignment", ""),
                idempotency_digest=self.headers.get("X-Director-Idempotency-Digest", ""),
                writer_generation=int(self.headers.get("X-Director-Writer-Generation", "0")),
                packet_digest=self.headers.get("X-Director-Packet-Digest", ""),
                writer_owner_token_digest=self.headers.get(
                    "X-Director-Writer-Owner-Token-Digest", ""
                ),
                timestamp=int(self.headers.get("X-Director-Bridge-Timestamp", "0")),
                nonce=self.headers.get("X-Director-Bridge-Nonce", ""),
                signature=authorization.removeprefix("Director-HMAC "),
                packet=self.rfile.read(length),
                # Present only for a redacted lane. It is covered by the request
                # signature, so stripping or forging it fails authentication.
                work_packet_digest=self.headers.get(
                    "X-Director-Work-Packet-Digest", ""
                ),
                host_deadline=self.headers.get("X-Director-Host-Deadline", ""),
            )
            if request.host_deadline and request.host_deadline.split(":", 1)[0] != self._clock_id:
                raise HostProviderBridgeError("host deadline clock changed")
            result = dispatch_bridge_request(
                request,
                secret=self.server.bridge_secret,  # type: ignore[attr-defined]
                store=self.server.claim_store,  # type: ignore[attr-defined]
                executor=self.server.executor,  # type: ignore[attr-defined]
            )
            response_digest = _digest(result)
            proof = bridge_response_signature(
                self.server.bridge_secret,  # type: ignore[attr-defined]
                nonce=request.nonce,
                idempotency_digest=request.idempotency_digest,
                response_digest=response_digest,
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.send_header("X-Director-Response-Digest", response_digest)
            self.send_header("X-Director-Response-Proof", proof)
            self.end_headers()
            self.wfile.write(result)
        except HostProviderBridgeAmbiguous:
            self._json(409, {"ok": False, "reason": "reconciliation_required"})
        except (HostProviderBridgeError, ValueError):
            self._json(403, {"ok": False, "reason": "request_rejected"})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="director_orchestrator.host_provider_bridge_server")
    parser.add_argument("--check-environment", action="store_true")
    # Never read sys.argv here: callers (and tests) pass arguments explicitly.
    arguments = parser.parse_args([] if argv is None else argv)
    pinned = _apply_pinned_process_environment()
    if arguments.check_environment:
        # No secret, store, database or server: print the pins this process would bind.
        executor = WindowsHostProviderExecutor(
            _commands(), cwd=Path(_required("DIRECTOR_HOST_PROVIDER_WORKSPACE")).resolve(strict=True)
        )
        print(json.dumps({
            "configuration_digests": configuration_digests(executor, _account_files()),
            "pinned_environment": pinned, "provider_calls": 0,
        }, sort_keys=True))
        return
    secret = _required("LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET")
    if len(secret) < 32:
        raise RuntimeError("LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET is too short")
    cwd = Path(_required("DIRECTOR_HOST_PROVIDER_WORKSPACE")).resolve(strict=True)
    store = BridgeClaimStore(_required("DIRECTOR_HOST_PROVIDER_BRIDGE_STORE"))
    executor = WindowsHostProviderExecutor(
        _commands(),
        cwd=cwd,
        timeout_seconds=int(os.environ.get("DIRECTOR_HOST_PROVIDER_TIMEOUT_SECONDS", "900")),
        writer_fence_verifier=PostgresHostWriterFenceVerifier(
            _required("DIRECTOR_HOST_PROVIDER_POSTGRES_DSN"),
            schema=_required("LANGGRAPH_PRIMARY_POSTGRES_SCHEMA"),
        ),
    )
    bind = os.environ.get("DIRECTOR_HOST_PROVIDER_BIND", "127.0.0.1")
    port = int(os.environ.get("DIRECTOR_HOST_PROVIDER_PORT", "18765"))
    if bind not in {"127.0.0.1", "0.0.0.0"}:
        raise RuntimeError("host provider bridge bind address is unsupported")
    server = ThreadingHTTPServer((bind, port), _Handler)
    if bind == "0.0.0.0":
        certificate = _required("DIRECTOR_HOST_PROVIDER_TLS_CERT_FILE")
        private_key = _required("DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, private_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.bridge_secret = secret  # type: ignore[attr-defined]
    server.claim_store = store  # type: ignore[attr-defined]
    server.executor = executor  # type: ignore[attr-defined]
    server.pinned_environment = pinned  # type: ignore[attr-defined]
    if os.environ.get("DIRECTOR_PROVIDER_VALIDATION_ENABLED") == "true":
        from .provider_validation import PostgresValidationStore, ValidationBridgeService
        _required("DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON")
        account_files = _account_files()
        if any(hmac.compare_digest(secret, _required(name)) for name in (
            "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY", "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"
        )):
            raise RuntimeError("validation keys must differ from production bridge authentication")
        from .provider_validation_team import team_route_from_environment
        from .openai_responses_validation import responses_route_from_environment
        server.validation_service = ValidationBridgeService(  # type: ignore[attr-defined]
            store=PostgresValidationStore(_required("DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN")),
            production_executor=executor,
            capture_key=_required("DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY"),
            owner_token=_required("DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"),
            account_files=account_files,
            team_route=team_route_from_environment(),
            openai_responses_route=responses_route_from_environment(),
        )
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main(sys.argv[1:])


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:15 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]]
# [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 18:30 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 22:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:46 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]]
