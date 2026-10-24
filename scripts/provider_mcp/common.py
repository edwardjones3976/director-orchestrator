"""Small stdio MCP transport shared by single-provider servers.

These tools are advisory only. They do not create Director assignments, reviews,
production credit, or authority to merge or publish.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROTOCOL = "2025-03-26"
MAX_INPUT = 24_000
MAX_RESPONSE = 1_000_000
SECRET_PATTERN = re.compile(
    r"(?im)(?:^|\s)(?:[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)|Authorization)\s*[:=]"
)


def enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def validate_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text must be a nonempty string")
    if len(value.encode("utf-8")) > MAX_INPUT:
        raise ValueError("text exceeds 24 KB")
    if SECRET_PATTERN.search(value):
        raise ValueError("text appears to contain a credential binding")
    return value


def credential(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} is not configured")
    return value


def reserve(provider: str, text: str, daily_cap: int) -> Path:
    root = Path.home() / ".codex" / "provider-mcp-receipts" / provider
    root.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = root / f"{day}-{digest}.json"
    # A failed or ambiguous call is never replayed automatically.
    with path.open("x", encoding="utf-8") as out:
        json.dump({"provider": provider, "request_sha256": digest,
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "status": "ATTEMPT_RESERVED", "production_credit": False}, out)
    # Exclusive slot files enforce the daily cap across concurrent MCP hosts.
    for slot in range(daily_cap):
        try:
            (root / f"{day}-{slot:03d}.slot").open("x").close()
        except FileExistsError:
            continue
        return path
    finish(path, "REFUSED_DAILY_CAP")
    raise ValueError(f"{provider} daily call cap reached")


def finish(path: Path, status: str, response: dict | None = None) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(status=status, finished_at=datetime.now(timezone.utc).isoformat())
    if response is not None:
        record["response_id"] = response.get("id")
        record["usage"] = response.get("usage")
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def post_json(url: str, body: dict, headers: dict[str, str]) -> dict:
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, method="POST",
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=120) as result:
            raw = result.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        # Provider error bodies can contain submitted text or credentials.
        raise RuntimeError(f"provider returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("provider connection failed or timed out") from None
    if len(raw) > MAX_RESPONSE:
        raise RuntimeError("provider response exceeds 1 MB")
    response = json.loads(raw)
    if not isinstance(response, dict):
        raise RuntimeError("provider response is not an object")
    return response


def get_json(url: str, headers: dict[str, str]) -> dict:
    request = Request(url, method="GET", headers=headers)
    try:
        with urlopen(request, timeout=30) as result:
            raw = result.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        raise RuntimeError(f"provider returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("provider connection failed or timed out") from None
    if len(raw) > MAX_RESPONSE:
        raise RuntimeError("provider response exceeds 1 MB")
    response = json.loads(raw)
    if not isinstance(response, dict):
        raise RuntimeError("provider response is not an object")
    return response


def native_executable(name: str, override: str) -> str | None:
    candidate = os.environ.get(override)
    return candidate if candidate and Path(candidate).is_file() else shutil.which(name)


def native_call(provider: str, text: str, command: list[str], *, timeout: int = 180) -> dict:
    """One bounded CLI call in an empty directory with a minimal child environment."""
    receipt = reserve(provider, text, 20)
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
               "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "PATHEXT"}
    child_env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    try:
        with tempfile.TemporaryDirectory(prefix=f"director-{provider}-mcp-") as cwd:
            result = subprocess.run(command, cwd=cwd, env=child_env, capture_output=True,
                                    text=True, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError(f"{provider} CLI returned exit {result.returncode}")
        output = result.stdout.strip()
        if not output or len(output.encode("utf-8")) > 100_000:
            raise RuntimeError(f"{provider} CLI returned empty or oversized output")
        finish(receipt, "RETURNED")
        return {"provider": provider, "output": output, "production_credit": False}
    except Exception:
        finish(receipt, "FAILED_OR_AMBIGUOUS")
        raise


def content(value: object, *, error: bool = False, provider: str) -> dict:
    payload = json.dumps(value, ensure_ascii=False)
    for name in ("XAI_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "CURSOR_API_KEY", "GEMINI_API_KEY", "PERPLEXITY_API_KEY",
                 "FIRECRAWL_API_KEY", "COPILOT_GITHUB_TOKEN"):
        secret = os.environ.get(name, "")
        if secret:
            payload = payload.replace(secret, "[REDACTED]")
    return {"content": [{"type": "text", "text": payload}],
            "isError": error}


def serve(*, name: str, tool_name: str, description: str, status, invoke) -> None:
    tools = [
        {"name": f"{name}_status", "description": f"Report {name} MCP readiness without exposing credentials.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
         "annotations": {"readOnlyHint": True}},
        {"name": tool_name, "description": description,
         "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": MAX_INPUT}},
                         "required": ["text"], "additionalProperties": False},
         "annotations": {"readOnlyHint": False, "openWorldHint": True}},
    ]
    for line in sys.stdin.buffer:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": f"director-{name}", "version": "0.1.0"}}
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                params = request.get("params") or {}
                if params.get("name") == f"{name}_status":
                    result = content(status(), provider=name)
                elif params.get("name") == tool_name:
                    try:
                        args = params.get("arguments") or {}
                        if set(args) != {"text"}:
                            raise ValueError("only text is accepted")
                        result = content(invoke(validate_text(args["text"])), provider=name)
                    except Exception as exc:
                        result = content({"refused": str(exc) if isinstance(exc, ValueError)
                                          else type(exc).__name__}, error=True, provider=name)
                else:
                    raise KeyError("unknown tool")
            else:
                if request_id is None:
                    continue
                raise KeyError("unknown method")
            if request_id is not None:
                reply = {"jsonrpc": "2.0", "id": request_id, "result": result}
            else:
                continue
        except Exception:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32603, "message": "Invalid MCP request"}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
