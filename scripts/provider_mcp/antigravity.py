"""Dedicated Antigravity managed-agent MCP observation contact.

Starting an Antigravity agent remains in the Director's bounded grant flow.
This MCP can read an already authorized interaction by exact id.
"""
from __future__ import annotations

import os
import re
from .common import credential, enabled, get_json, serve

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def authorized_ids() -> set[str]:
    """Exact interaction ids approved in this MCP process configuration."""
    raw = os.environ.get("ANTIGRAVITY_MCP_ALLOWED_INTERACTION_IDS", "")
    ids = {item.strip() for item in raw.split(",") if item.strip()}
    if not all(RUN_ID.fullmatch(item) for item in ids):
        return set()
    return ids


def status() -> dict:
    return {"provider": "antigravity", "transport": "gemini_interactions_api",
            "credential_configured": bool(os.environ.get("GEMINI_API_KEY")),
            "read_enabled": enabled("ANTIGRAVITY_MCP_READ_ENABLED") and bool(authorized_ids()),
            "start_supported": False, "production_credit": False}


def get_interaction(text: str) -> dict:
    if not enabled("ANTIGRAVITY_MCP_READ_ENABLED"):
        raise ValueError("Antigravity MCP reads are disabled")
    if not RUN_ID.fullmatch(text):
        raise ValueError("interaction id must be one safe path segment")
    if text not in authorized_ids():
        raise ValueError("interaction id has no exact authorization")
    key = credential("GEMINI_API_KEY")
    payload = get_json(f"https://generativelanguage.googleapis.com/v1beta/interactions/{text}",
                       {"x-goog-api-key": key})
    return {"provider": "antigravity", "interaction": payload,
            "production_credit": False}


if __name__ == "__main__":
    serve(name="antigravity", tool_name="antigravity_get_interaction",
          description="Read one existing Antigravity interaction by id; does not start an agent.",
          status=status, invoke=get_interaction)
