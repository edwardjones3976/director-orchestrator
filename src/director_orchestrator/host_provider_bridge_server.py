# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:46 UTC | BEGIN]]
"""Windows HTTP entry point for the authenticated host-provider bridge."""

from __future__ import annotations

import json
import hmac
import os
import ssl
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
)


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


class _Handler(BaseHTTPRequestHandler):
    server_version = "DirectorHostBridge/1"

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
                configured = sorted(self.server.executor.commands)  # type: ignore[attr-defined]
                command_map_digest = "sha256:" + _digest(
                    json.dumps(configured, separators=(",", ":")).encode("utf-8")
                )
                self._json(200, {
                    "ok": True, "protocol": BRIDGE_PROTOCOL,
                    "configured_providers": configured,
                    "command_map_digest": command_map_digest,
                    "proof": bridge_challenge_signature(
                        self.server.bridge_secret, timestamp=timestamp, nonce=nonce,  # type: ignore[attr-defined]
                        command_map_digest=command_map_digest,
                    ),
                })
            except (HostProviderBridgeError, ValueError):
                self._json(403, {"ok": False})
            return
        if self.path != "/health":
            self._json(404, {"ok": False})
            return
        configured = sorted(self.server.executor.commands)  # type: ignore[attr-defined]
        self._json(
            200,
            {
                "ok": True,
                "protocol": BRIDGE_PROTOCOL,
                "configured_providers": configured,
                "cursor": False,
                "windows_copilot": False,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
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
            )
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


def main() -> None:
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
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()


# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 17:46 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:46 UTC | END]]
