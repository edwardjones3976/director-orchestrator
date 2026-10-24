"""Dedicated OpenAI Responses API MCP contact; separate from native Codex."""
from __future__ import annotations

import os
from .common import credential, enabled, finish, post_json, reserve, serve


def status() -> dict:
    return {"provider": "openai", "transport": "responses_api",
            "credential_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "paid_calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("OPENAI_MCP_ENABLED"),
            "production_credit": False}


def ask(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("OPENAI_MCP_ENABLED")):
        raise ValueError("OpenAI MCP calls are disabled")
    key = credential("OPENAI_API_KEY")
    receipt = reserve("openai", text, 40)
    try:
        response = post_json("https://api.openai.com/v1/responses", {
            "model": os.environ.get("OPENAI_MCP_MODEL", "gpt-5.6"),
            "input": [{"role": "user", "content": text}],
            "max_output_tokens": 4000, "tools": [], "store": False,
            "truncation": "disabled",
        }, {"Authorization": f"Bearer {key}"})
        finish(receipt, "RETURNED", response)
        return {"provider": "openai", "status": response.get("status"),
                "output": response.get("output"), "usage": response.get("usage"),
                "production_credit": False}
    except Exception:
        finish(receipt, "FAILED_OR_AMBIGUOUS")
        raise


if __name__ == "__main__":
    serve(name="openai", tool_name="openai_ask", description="Ask OpenAI one bounded advisory question. Paid, no tools or production credit.",
          status=status, invoke=ask)
