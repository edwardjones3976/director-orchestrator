"""Stdio MCP for bounded, read-only PowerShell inspection of Director worktrees.

The caller chooses an operation and a path, never a PowerShell command string.
All paths are resolved against explicitly configured roots before PowerShell runs.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

PROTOCOL = "2025-03-26"
MAX_FILE_BYTES = 512_000
MAX_OUTPUT_BYTES = 1_000_000
EXCLUDED_PROJECT_PARTS = {"llm", "homemade-llm", "homemade_llm"}
SECRET_SUFFIXES = {".key", ".pem", ".pfx", ".p12"}


def roots() -> tuple[Path, ...]:
    value = os.environ.get("FOREMAN_READ_ROOTS", "")
    configured = []
    for item in value.split(";"):
        if not item:
            continue
        path = Path(item)
        if not path.is_absolute() or not path.is_dir():
            continue
        configured.append(path.resolve(strict=True))
    return tuple(configured)


def allowed_path(value: object, *, directory: bool) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("path must be a bounded absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise ValueError("path does not exist") from None
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots()):
        raise ValueError("path is outside configured Director worktrees")
    if any(part.casefold() in EXCLUDED_PROJECT_PARTS for part in resolved.parts):
        raise ValueError("path belongs to an excluded project")
    if not directory and (resolved.name.casefold().startswith(".env")
                          or resolved.suffix.casefold() in SECRET_SUFFIXES):
        raise ValueError("credential files are outside this read route")
    if directory:
        if not resolved.is_dir():
            raise ValueError("path must be a directory")
    else:
        if not resolved.is_file():
            raise ValueError("path must be a file")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file exceeds read limit")
    return resolved


def powershell(action: str, path: Path, needle: str = "") -> dict:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        raise ValueError("Windows PowerShell is unavailable")
    exe = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = Path(__file__).with_name("read_only.ps1")
    if not exe.is_file() or not script.is_file():
        raise ValueError("Windows PowerShell read runner is unavailable")
    child_env = {key: value for key, value in os.environ.items()
                 if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}}
    command = [str(exe), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
               str(script), "-Action", action, "-Path", str(path)]
    if action == "Search":
        command.extend(["-NeedleBase64", base64.b64encode(needle.encode("utf-8")).decode("ascii")])
    try:
        result = subprocess.run(command, env=child_env, capture_output=True, timeout=20,
                                check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("PowerShell read operation timed out") from None
    if result.returncode:
        raise ValueError("PowerShell read operation failed")
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise ValueError("PowerShell output exceeds limit")
    try:
        payload = json.loads(result.stdout.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise ValueError("PowerShell returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("PowerShell returned invalid JSON")
    return payload


def call(name: str, arguments: object) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    if name == "foreman_read_status":
        if arguments:
            raise ValueError("status takes no arguments")
        return {"transport": "windows_powershell", "read_only": True,
                "configured_roots": len(roots()), "arbitrary_commands": False}
    if name == "foreman_read_file" and set(arguments) == {"path"}:
        path = allowed_path(arguments["path"], directory=False)
        return {"path": str(path), **powershell("Read", path)}
    if name == "foreman_list_directory" and set(arguments) == {"path"}:
        path = allowed_path(arguments["path"], directory=True)
        return {"path": str(path), **powershell("List", path)}
    if name == "foreman_search_file" and set(arguments) == {"path", "needle"}:
        path = allowed_path(arguments["path"], directory=False)
        needle = arguments["needle"]
        if not isinstance(needle, str) or not needle or len(needle) > 200:
            raise ValueError("needle must be a nonempty string of at most 200 characters")
        return {"path": str(path), **powershell("Search", path, needle)}
    raise ValueError("unknown tool or arguments")


def tool_list() -> list[dict]:
    path = {"type": "string", "maxLength": 4096}
    tools = [
        ("foreman_read_status", {}, []),
        ("foreman_read_file", {"path": path}, ["path"]),
        ("foreman_list_directory", {"path": path}, ["path"]),
        ("foreman_search_file", {"path": path,
                                 "needle": {"type": "string", "maxLength": 200}},
         ["path", "needle"]),
    ]
    return [{"name": name, "description": "Bounded read-only Foreman inspection.",
             "inputSchema": {"type": "object", "properties": properties,
                             "required": required, "additionalProperties": False},
             "annotations": {"readOnlyHint": True, "openWorldHint": False}}
            for name, properties, required in tools]


def serve() -> None:
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            method = request.get("method")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "foreman-powershell-readonly", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_list()}
            elif method == "tools/call":
                params = request.get("params") or {}
                try:
                    value = call(params.get("name"), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(value)}],
                              "structuredContent": value, "isError": False}
                except Exception as exc:
                    message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    result = {"content": [{"type": "text", "text": json.dumps({"refused": message})}],
                              "isError": True}
            else:
                raise ValueError("unknown MCP method")
            if request.get("id") is not None:
                reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            else:
                continue
        except Exception:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32603, "message": "Invalid MCP request"}}
        print(json.dumps(reply, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    serve()
