"""Dedicated Perplexity Search API MCP contact for public research only."""
from __future__ import annotations

import os
from .common import credential, enabled, finish, post_json, reserve, serve


def status() -> dict:
    return {"provider": "perplexity", "transport": "search_api",
            "credential_configured": bool(os.environ.get("PERPLEXITY_API_KEY")),
            "calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("PERPLEXITY_MCP_ENABLED"),
            "production_credit": False}


def search(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("PERPLEXITY_MCP_ENABLED")):
        raise ValueError("Perplexity MCP calls are disabled")
    if len(text) > 500:
        raise ValueError("public search query exceeds 500 characters")
    key = credential("PERPLEXITY_API_KEY")
    receipt = reserve("perplexity", text, 40)
    try:
        response = post_json("https://api.perplexity.ai/search", {"query": text, "max_results": 5},
                             {"Authorization": f"Bearer {key}"})
        finish(receipt, "RETURNED", response)
        return {"provider": "perplexity", "results": response.get("results", []),
                "production_credit": False}
    except Exception:
        finish(receipt, "FAILED_OR_AMBIGUOUS")
        raise


if __name__ == "__main__":
    serve(name="perplexity", tool_name="perplexity_search_public",
          description="Search public web sources with Perplexity; query must contain no private task context.",
          status=status, invoke=search)
