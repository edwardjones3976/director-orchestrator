"""One MCP entry for the three named administrators with explicit tools."""
from __future__ import annotations

import json
import sys

from . import admin_claude, admin_cursor, admin_openai
from .common import PROTOCOL, content, validate_text

CONTACTS = {
    "admin_crew_openai_consult": admin_openai.consult,
    "admin_crew_claude_consult": admin_claude.consult,
    "admin_crew_cursor_consult": admin_cursor.consult,
}


def status() -> dict:
    return {"wrapper": "admin-crew", "authority_granted": False,
            "contacts": {"openai": admin_openai.status(),
                         "claude": admin_claude.status(),
                         "cursor": admin_cursor.status()}}


def tools() -> list[dict]:
    listed = [{"name": "admin_crew_status", "description": "Report readiness of the three admin contacts.",
               "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
               "annotations": {"readOnlyHint": True}}]
    for name in CONTACTS:
        listed.append({"name": name, "description": "Consult only the named administrator once; no project authority.",
                       "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 24000}},
                                       "required": ["text"], "additionalProperties": False},
                       "annotations": {"readOnlyHint": False, "openWorldHint": True}})
    return listed


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
                          "serverInfo": {"name": "director-admin-crew", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools()}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    if name == "admin_crew_status":
                        if args:
                            raise ValueError("status accepts no arguments")
                        value = status()
                    elif name in CONTACTS:
                        if set(args) != {"text"}:
                            raise ValueError("only text is accepted")
                        value = CONTACTS[name](validate_text(args["text"]))
                    else:
                        raise ValueError("unknown admin crew tool")
                    result = content(value, provider="admin-crew")
                except Exception as exc:
                    result = content({"refused": str(exc) if isinstance(exc, ValueError)
                                      else type(exc).__name__}, error=True, provider="admin-crew")
            else:
                raise ValueError("unknown MCP method")
            if request.get("id") is None:
                continue
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32603, "message": "Invalid MCP request"}}
        print(json.dumps(reply, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    serve()
