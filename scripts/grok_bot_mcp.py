"""Local MCP contact for the Grok Bot desktop app, separate from xAI's API.

The existing guarded PowerShell dispatcher owns all GUI input. This server
checks its exact bytes before invoking it and keeps paste and send separate.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROTOCOL = "2025-03-26"
SCRIPT_SHA256 = "32a92ae34b2df428d707dea1812c220ccb46cfb6e6e1f12d7f4397f94fa7834b"
SCRIPT = Path(os.environ.get("GROK_BOT_DISPATCH_SCRIPT", str(Path(__file__).resolve().parent / "grok-bot-chat" / "grok_bot_chat.ps1")))
APP = Path(os.environ.get("GROK_BOT_EXECUTABLE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Grok Bot" / "Grok Bot.exe")))
ARTIFACT_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "director-orchestrator" / "grok-bot"
VERDICT = re.compile(r"(?m)^VERDICT: ACCEPT \| CHANGES_REQUIRED \| SERIOUS_SAFETY_STOP bound to [0-9a-f]{40}\s*$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def script_ready() -> tuple[bool, str]:
    try:
        digest = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    except (OSError, PermissionError):
        return False, "dispatcher is inaccessible; check Bitdefender Quarantine for grok_bot_chat.ps1"
    if digest != SCRIPT_SHA256:
        return False, "dispatcher bytes differ from the reviewed Git version"
    return True, "ready"


def status() -> dict:
    ready, reason = script_ready()
    return {
        "contact": "Grok Bot desktop app",
        "app_installed": APP.is_file(),
        "dispatcher_ready": ready,
        "dispatcher_status": reason,
        "input_enabled": enabled("GROK_BOT_DESKTOP_INPUT_ENABLED"),
        "send_enabled": enabled("GROK_BOT_DESKTOP_SEND_ENABLED"),
        "production_credit": False,
    }


def dispatch(action: str, **options: str) -> dict:
    ready, reason = script_ready()
    if not ready:
        raise ValueError(reason)
    if not APP.is_file():
        raise ValueError("Grok Bot desktop executable is missing")
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(SCRIPT), "-Action", action, "-Executable", str(APP)]
    for key, value in options.items():
        command.extend([f"-{key}", value])
    result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        # PowerShell errors can contain local packet paths. Keep the MCP error bounded.
        raise ValueError(f"Grok Bot {action} refused; inspect the local dispatcher error (exit {result.returncode})")
    return {"action": action, "output": result.stdout.strip()[:3000]}


def paste(packet_path: str) -> dict:
    if not enabled("GROK_BOT_DESKTOP_INPUT_ENABLED"):
        raise ValueError("desktop input is disabled")
    packet = Path(packet_path).resolve(strict=True)
    if not packet.is_relative_to(ARTIFACT_ROOT.resolve()):
        raise ValueError("packet must be under the local Grok Bot artifact directory")
    raw = packet.read_bytes()
    if not 0 < len(raw) <= 24_000 or any(b > 127 for b in raw):
        raise ValueError("packet must be nonempty ASCII of at most 24 KB")
    text = raw.decode("ascii")
    if not VERDICT.search(text):
        raise ValueError("packet requires a whole-line exact-SHA VERDICT template")
    if re.search(r"(?im)(?:api[_ -]?key|secret|password|authorization)\s*[:=]", text):
        raise ValueError("packet appears to contain credentials")
    return dispatch("paste", Packet=str(packet))


def preview(digest: str) -> dict:
    if not HEX64.fullmatch(digest):
        raise ValueError("digest must be lowercase SHA-256")
    path = ARTIFACT_ROOT / f"{digest}.preview.png"
    if not path.is_file():
        raise ValueError("preview does not exist; paste first")
    return {"path": str(path), "preview_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "instruction": "Inspect this full-display image before calling grok_bot_send_review."}


def send(digest: str, preview_sha256: str) -> dict:
    if not enabled("GROK_BOT_DESKTOP_SEND_ENABLED"):
        raise ValueError("desktop send is disabled")
    if not HEX64.fullmatch(preview_sha256):
        raise ValueError("preview_sha256 must be lowercase SHA-256")
    current = preview(digest)
    if current["preview_sha256"] != preview_sha256:
        raise ValueError("preview changed since inspection")
    return dispatch("send", Digest=digest)


TOOLS = [
    ("grok_bot_status", {}, True, "Report desktop route readiness without input."),
    ("grok_bot_focus", {}, True, "Report current keyboard focus without input."),
    ("grok_bot_paste_review", {"packet_path": {"type": "string"}}, False,
     "Paste one local read-only review packet into Grok Bot; does not send."),
    ("grok_bot_review_preview", {"digest": {"type": "string"}}, True,
     "Get the full-display paste preview path and SHA-256 for human inspection."),
    ("grok_bot_send_review", {"digest": {"type": "string"}, "preview_sha256": {"type": "string"}}, False,
     "Send a previously previewed review packet once to Grok Bot."),
]


def call(name: str, args: dict) -> dict:
    functions = {
        "grok_bot_status": lambda: status(),
        "grok_bot_focus": lambda: dispatch("focus"),
        "grok_bot_paste_review": lambda: paste(args["packet_path"]),
        "grok_bot_review_preview": lambda: preview(args["digest"]),
        "grok_bot_send_review": lambda: send(args["digest"], args["preview_sha256"]),
    }
    if name not in functions:
        raise KeyError("unknown Grok Bot tool")
    required = next(properties for tool, properties, _, _ in TOOLS if tool == name)
    if set(args) != set(required):
        raise ValueError("tool arguments do not match the schema")
    return functions[name]()


def serve() -> None:
    listed = [{"name": name, "description": description,
               "inputSchema": {"type": "object", "properties": properties,
                               "required": list(properties), "additionalProperties": False},
               "annotations": {"readOnlyHint": read_only, "openWorldHint": not read_only}}
              for name, properties, read_only, description in TOOLS]
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            method = request.get("method")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "director-grok-bot-desktop", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": listed}
            elif method == "tools/call":
                try:
                    params = request.get("params") or {}
                    value = call(params.get("name"), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
                except (KeyError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
                    result = {"content": [{"type": "text", "text": str(exc)[:300]}], "isError": True}
            else:
                raise ValueError("unknown MCP method")
            reply = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except (ValueError, AttributeError, TypeError, json.JSONDecodeError):
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32600, "message": "Invalid MCP request"}}
        if request is not None and isinstance(request, dict) and request.get("id") is not None:
            print(json.dumps(reply, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    serve()
