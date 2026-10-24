"""Dedicated advisory xAI Grok MCP server."""
from __future__ import annotations

import os
from .common import credential, enabled, finish, post_json, reserve, serve


def status() -> dict:
    return {"provider": "xai", "credential_configured": bool(os.environ.get("XAI_API_KEY")),
            "paid_calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("XAI_MCP_ENABLED"),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("XAI_MCP_ENABLED")):
        raise ValueError("xAI MCP calls are disabled")
    key = credential("XAI_API_KEY")
    receipt = reserve("xai", text, 40)
    try:
        response = post_json("https://api.x.ai/v1/responses", {
            "model": os.environ.get("GROK_MODEL", "grok-4.6"),
            "input": [{"role": "user", "content": text}],
            "max_output_tokens": 4000, "tools": [], "store": False,
            "truncation": "disabled",
        }, {"Authorization": f"Bearer {key}"})
        finish(receipt, "RETURNED", response)
        return {"provider": "xai", "status": response.get("status"),
                "output": response.get("output"), "usage": response.get("usage"),
                "production_credit": False}
    except Exception:
        finish(receipt, "FAILED_OR_AMBIGUOUS")
        raise


if __name__ == "__main__":
    serve(name="xai", tool_name="xai_ask", description="Ask Grok one bounded advisory question. Paid, no tools or production credit.",
          status=status, invoke=ask)
