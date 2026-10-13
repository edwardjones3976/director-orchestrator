"""Dedicated Firecrawl Search API MCP contact for bounded public research."""
from __future__ import annotations

import os
from .common import credential, enabled, finish, post_json, reserve, serve


def status() -> dict:
    return {"provider": "firecrawl", "transport": "search_api",
            "credential_configured": bool(os.environ.get("FIRECRAWL_API_KEY")),
            "calls_enabled": enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("FIRECRAWL_MCP_ENABLED"),
            "production_credit": False}


def search(text: str) -> dict:
    if not (enabled("DIRECTOR_MCP_PAID_CALLS_ENABLED") and enabled("FIRECRAWL_MCP_ENABLED")):
        raise ValueError("Firecrawl MCP calls are disabled")
    if len(text) > 500:
        raise ValueError("public search query exceeds 500 characters")
    key = credential("FIRECRAWL_API_KEY")
    receipt = reserve("firecrawl", text, 40)
    try:
        response = post_json("https://api.firecrawl.dev/v2/search", {
            "query": text, "limit": 5, "sources": ["web"], "safe": True,
            "scrapeOptions": {},
        }, {"Authorization": f"Bearer {key}"})
        finish(receipt, "RETURNED", response)
        return {"provider": "firecrawl", "results": response.get("data", {}),
                "production_credit": False}
    except Exception:
        finish(receipt, "FAILED_OR_AMBIGUOUS")
        raise


if __name__ == "__main__":
    serve(name="firecrawl", tool_name="firecrawl_search_public",
          description="Search public web sources with Firecrawl; query must contain no private task context.",
          status=status, invoke=search)
