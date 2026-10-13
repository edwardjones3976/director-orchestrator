"""Dedicated Cursor Agent native MCP contact in read-only ask mode."""
from __future__ import annotations

import os
from pathlib import Path
from .common import enabled, native_call, native_executable, serve


def executable() -> str | None:
    configured = native_executable("cursor-agent", "CURSOR_MCP_EXECUTABLE")
    if configured:
        return configured
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / "cursor-agent.ps1"
    return str(local) if local.is_file() else None


def status() -> dict:
    return {"provider": "cursor", "transport": "cursor_agent_native",
            "cli_installed": bool(executable()),
            "calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("CURSOR_MCP_ENABLED"),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("CURSOR_MCP_ENABLED")):
        raise ValueError("Cursor MCP calls are disabled")
    exe = executable()
    if not exe:
        raise ValueError("Cursor Agent CLI is not installed")
    # The CLI's ask mode is read-only. Do not pass --force, --yolo, or MCP approval.
    command = [exe, "--print", "--mode", "ask", "--sandbox", "enabled", text]
    if exe.lower().endswith(".ps1"):
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *command]
    return native_call("cursor", text, command)


if __name__ == "__main__":
    serve(name="cursor", tool_name="cursor_ask", description="Ask Cursor Agent once in read-only ask mode, in an empty workspace.",
          status=status, invoke=ask)
