"""Dedicated GitHub Copilot CLI MCP contact; not the GitHub repository MCP."""
from __future__ import annotations

from .common import enabled, native_call, native_executable, serve


def status() -> dict:
    return {"provider": "github-copilot", "transport": "copilot_cli_native",
            "cli_installed": bool(native_executable("copilot", "COPILOT_MCP_EXECUTABLE")),
            "calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("COPILOT_MCP_ENABLED"),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("COPILOT_MCP_ENABLED")):
        raise ValueError("Copilot MCP calls are disabled")
    exe = native_executable("copilot", "COPILOT_MCP_EXECUTABLE")
    if not exe:
        raise ValueError("GitHub Copilot CLI is not installed")
    # Only read is available; no shell, write, URL, memory, or MCP tools.
    return native_call("github-copilot", text, [exe, "-p", text, "-s", "--no-ask-user",
                                               "--available-tools=read"])


if __name__ == "__main__":
    serve(name="copilot", tool_name="copilot_ask", description="Ask GitHub Copilot CLI once with only read available in an empty workspace.",
          status=status, invoke=ask)
