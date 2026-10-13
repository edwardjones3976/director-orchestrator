"""Dedicated advisory Z.ai GLM MCP server; production activation required."""
from __future__ import annotations

import os
import re
from .common import credential, enabled, finish, post_json, reserve, serve


def activated() -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", os.environ.get("ZAI_PRODUCTION_ACTIVATION", "")))


def status() -> dict:
    return {"provider": "zai", "credential_configured": bool(os.environ.get("ZAI_API_KEY")),
            "activation_record_configured": activated(),
            "paid_calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("ZAI_MCP_ENABLED") and activated(),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("ZAI_MCP_ENABLED")):
        raise ValueError("Z.ai MCP calls are disabled")
    if not activated():
        raise ValueError("Z.ai production activation record is required; the trial has expired")
    key = credential("ZAI_API_KEY")
    receipt = reserve("zai", text, 20)
    try:
        response = post_json("https://api.z.ai/api/anthropic/v1/messages", {
            "model": os.environ.get("ZAI_MODEL", "glm-5.3-flash"),
            "max_tokens": 4000, "messages": [{"role": "user", "content": text}],
        }, {"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"})
        finish(receipt, "RETURNED", response)
        return {"provider": "zai", "content": response.get("content"),
                "stop_reason": response.get("stop_reason"), "usage": response.get("usage"),
                "production_credit": False}
    except Exception:
        finish(receipt, "FAILED_OR_AMBIGUOUS")
        raise


if __name__ == "__main__":
    serve(name="zai", tool_name="zai_ask", description="Ask GLM one bounded advisory question after Z.ai production activation. Paid, no tools or production credit.",
          status=status, invoke=ask)
