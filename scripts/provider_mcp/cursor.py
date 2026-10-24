"""Dedicated Cursor Agent native MCP contact in read-only ask mode."""
from __future__ import annotations

import os
import json
import re
import subprocess
from pathlib import Path
from .common import enabled, native_call, native_executable, serve


def executable() -> str | None:
    configured = native_executable("cursor-agent", "CURSOR_MCP_EXECUTABLE")
    if configured:
        return configured
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / "cursor-agent.ps1"
    return str(local) if local.is_file() else None


def workspace_read_boundary() -> bool:
    """Require Cursor's global workspace read boundary, not just Ask mode."""
    config_root = Path(os.environ.get("CURSOR_CONFIG_DIR") or
                       (Path(os.environ.get("USERPROFILE", "")) / ".cursor"))
    try:
        config = json.loads((config_root / "cli-config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    sandbox = config.get("sandbox") if isinstance(config, dict) else None
    return isinstance(sandbox, dict) and sandbox.get("readBoundary") == "workspace"


def supports_read_boundary(exe: str) -> bool:
    command = [exe, "--version"]
    if exe.lower().endswith(".ps1"):
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *command]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    match = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})-", result.stdout.strip())
    return result.returncode == 0 and bool(match) and tuple(map(int, match.groups())) >= (2026, 10, 1)


def status() -> dict:
    return {"provider": "cursor", "transport": "cursor_agent_native",
            "cli_installed": bool(executable()),
            "workspace_read_boundary": workspace_read_boundary(),
            "calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("CURSOR_MCP_ENABLED")
            and workspace_read_boundary(),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("CURSOR_MCP_ENABLED")):
        raise ValueError("Cursor MCP calls are disabled")
    exe = executable()
    if not exe:
        raise ValueError("Cursor Agent CLI is not installed")
    if not workspace_read_boundary() or not supports_read_boundary(exe):
        raise ValueError("Cursor workspace read boundary is not configured or supported")
    # The CLI's ask mode is read-only. Do not pass --force, --yolo, or MCP approval.
    command = [exe, "--print", "--mode", "ask", "--model", "composer-2.5",
               "--sandbox", "enabled", "--trust", text]
    if exe.lower().endswith(".ps1"):
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *command]
    return native_call("cursor", text, command)


if __name__ == "__main__":
    serve(name="cursor", tool_name="cursor_ask", description="Ask Cursor Agent once with workspace read boundary and read-only ask mode.",
          status=status, invoke=ask)
