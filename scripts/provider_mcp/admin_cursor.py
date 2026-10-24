"""Administrator contact wrapper for the Cursor lane."""
from __future__ import annotations

from . import cursor
from .common import serve


def status() -> dict:
    return {**cursor.status(), "administrator": "Cursor",
            "adapter_scope": "advisory_only", "authority_granted": False}


def consult(text: str) -> dict:
    return {**cursor.ask(text), "administrator": "Cursor",
            "adapter_scope": "advisory_only", "authority_granted": False}


if __name__ == "__main__":
    serve(name="admin_cursor", tool_name="admin_cursor_consult",
          description="Consult the Cursor administrator contact once; no assignment or approval authority.",
          status=status, invoke=consult)
