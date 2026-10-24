"""Dedicated Claude Code native MCP contact using the signed-in local account."""
from __future__ import annotations

from .common import enabled, native_call, native_executable, serve


def status() -> dict:
    return {"provider": "claude", "transport": "claude_code_native",
            "cli_installed": bool(native_executable("claude", "CLAUDE_MCP_EXECUTABLE")),
            "calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("CLAUDE_MCP_ENABLED"),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("CLAUDE_MCP_ENABLED")):
        raise ValueError("Claude MCP calls are disabled")
    exe = native_executable("claude", "CLAUDE_MCP_EXECUTABLE")
    if not exe:
        raise ValueError("Claude Code CLI is not installed")
    return native_call("claude", text, [exe, "-p", text, "--safe-mode", "--restricted",
                                       "--tools", "", "--strict-mcp-config", "--mcp-config",
                                       '{"mcpServers":{}}', "--no-session-persistence",
                                       "--permission-prompts", "none", "--max-turns", "1"])


if __name__ == "__main__":
    serve(name="claude", tool_name="claude_ask", description="Ask signed-in Claude Code once with no tools or MCP servers. No production credit.",
          status=status, invoke=ask)
