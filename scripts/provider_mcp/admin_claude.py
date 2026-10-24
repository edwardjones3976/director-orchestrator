"""Administrator contact wrapper for the Claude lane."""
from __future__ import annotations

from . import claude
from .common import serve


def status() -> dict:
    return {**claude.status(), "administrator": "Claude",
            "adapter_scope": "advisory_only", "authority_granted": False}


def consult(text: str) -> dict:
    return {**claude.ask(text), "administrator": "Claude",
            "adapter_scope": "advisory_only", "authority_granted": False}


if __name__ == "__main__":
    serve(name="admin_claude", tool_name="admin_claude_consult",
          description="Consult the Claude administrator contact once; no assignment or approval authority.",
          status=status, invoke=consult)
