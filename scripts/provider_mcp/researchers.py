"""Research-only MCP wrapper for Perplexity and Firecrawl.

Results are returned to the invoking MCP client. No tool dispatches to another
provider, sends a message, or turns search results into an asserted verdict.
"""
from __future__ import annotations

import json
import sys

from . import firecrawl, perplexity
from .common import PROTOCOL, content, validate_text

SEARCHERS = {
    "researchers_perplexity_search": ("perplexity", perplexity.search),
    "researchers_firecrawl_search": ("firecrawl", firecrawl.search),
}
MAX_QUERY = 500


def status() -> dict:
    return {
        "wrapper": "Researchers",
        "role": "public_research_only",
        "delivery": "returned_to_caller",
        "authority_granted": False,
        "contacts": {
            "perplexity": perplexity.status(),
            "firecrawl": firecrawl.status(),
        },
    }


def tools() -> list[dict]:
    listed = [{
        "name": "researchers_status",
        "description": "Report Perplexity and Firecrawl research readiness without searching.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    }]
    for name, (provider, _) in SEARCHERS.items():
        listed.append({
            "name": name,
            "description": f"Search public sources with {provider} and return the original results to this caller.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {
                    "type": "string", "minLength": 1, "maxLength": MAX_QUERY,
                    "description": "Public research question or search terms; omit private task context.",
                }},
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "openWorldHint": True},
        })
    return listed


def call(name: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    if name == "researchers_status":
        if arguments:
            raise ValueError("status accepts no arguments")
        return status()
    if name not in SEARCHERS:
        raise ValueError("unknown Researchers tool")
    if set(arguments) != {"query"}:
        raise ValueError("only query is accepted")
    query = validate_text(arguments["query"])
    if len(query) > MAX_QUERY:
        raise ValueError("public search query exceeds 500 characters")
    provider, search = SEARCHERS[name]
    result = search(query)
    return {
        "provider": provider,
        "query": query,
        "results": result["results"],
        "delivery": "returned_to_caller",
        "production_credit": False,
    }


def serve() -> None:
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            method = request.get("method")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "director-Researchers", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools()}
            elif method == "tools/call":
                try:
                    params = request.get("params") or {}
                    value = call(params.get("name"), params.get("arguments") or {})
                    # common.content redacts configured credential values first.
                    result = content(value, provider="Researchers")
                    result["structuredContent"] = json.loads(result["content"][0]["text"])
                except Exception as exc:
                    result = content({"refused": str(exc) if isinstance(exc, (ValueError, RuntimeError))
                                      else type(exc).__name__}, error=True, provider="Researchers")
            else:
                raise ValueError("unknown MCP method")
            if request.get("id") is None:
                continue
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32603, "message": "Invalid MCP request"}}
        print(json.dumps(reply, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    serve()
