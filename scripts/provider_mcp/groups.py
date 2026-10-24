"""Explicit MCP group views over the existing, separately gated contacts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import (admin_claude, admin_cursor, admin_openai, antigravity, claude,
               copilot, cursor, firecrawl, openai, perplexity, xai, zai)
from .common import MAX_INPUT, PROTOCOL, content, validate_text


@dataclass(frozen=True)
class Contact:
    name: str
    action: str
    status: Callable[[], dict]
    invoke: Callable[[str], dict]


ADMIN = (
    Contact("openai", "consult", admin_openai.status, admin_openai.consult),
    Contact("claude", "consult", admin_claude.status, admin_claude.consult),
    Contact("cursor", "consult", admin_cursor.status, admin_cursor.consult),
)
PROVIDERS = (
    Contact("openai", "ask", openai.status, openai.ask),
    Contact("claude", "ask", claude.status, claude.ask),
    Contact("cursor", "ask", cursor.status, cursor.ask),
    Contact("xai", "ask", xai.status, xai.ask),
    Contact("antigravity", "get_interaction", antigravity.status, antigravity.get_interaction),
    Contact("perplexity", "search_public", perplexity.status, perplexity.search),
    Contact("firecrawl", "search_public", firecrawl.status, firecrawl.search),
    Contact("copilot", "ask", copilot.status, copilot.ask),
    Contact("zai", "ask", zai.status, zai.ask),
)
ADMIN_NAMES = frozenset(contact.name for contact in ADMIN)
WORKERS = tuple(contact for contact in PROVIDERS if contact.name not in ADMIN_NAMES)

# Grok Bot is a desktop contact, not the xAI API. Its reviewed MCP adapter
# stays separate; this bridge sends one exact tool call to that adapter.
GROK_BOT_TOOLS = {
    "status": ("grok_bot_status", {}, True),
    "focus": ("grok_bot_focus", {}, True),
    "paste_review": ("grok_bot_paste_review", {"packet_path": {"type": "string"}}, False),
    "review_preview": ("grok_bot_review_preview", {"digest": {"type": "string"}}, True),
    "send_review": ("grok_bot_send_review",
                    {"digest": {"type": "string"}, "preview_sha256": {"type": "string"}}, False),
}


def grok_bot_script() -> Path | None:
    value = os.environ.get("GROK_BOT_MCP_SCRIPT", "")
    path = Path(value) if value else None
    return path if path and path.is_file() else None


def grok_bot_call(name: str, arguments: dict) -> dict:
    script = grok_bot_script()
    if script is None:
        raise ValueError("Grok Bot MCP adapter is not configured")
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    try:
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                   "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "PATHEXT",
                   "GROK_BOT_DISPATCH_SCRIPT", "GROK_BOT_EXECUTABLE",
                   "GROK_BOT_DESKTOP_INPUT_ENABLED", "GROK_BOT_DESKTOP_SEND_ENABLED"}
        child_env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        result = subprocess.run([sys.executable, str(script)], input=json.dumps(request) + "\n",
                                text=True, capture_output=True, timeout=50, check=False,
                                env=child_env)
    except subprocess.TimeoutExpired:
        raise ValueError("Grok Bot MCP adapter timed out") from None
    if result.returncode or not result.stdout:
        raise ValueError("Grok Bot MCP adapter failed") from None
    try:
        reply = json.loads(result.stdout.splitlines()[0])
        outcome = reply["result"]
        raw = outcome["content"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("Grok Bot MCP adapter returned an invalid response") from None
    if outcome.get("isError"):
        raise ValueError(str(raw)[:300])
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("Grok Bot MCP adapter returned an invalid response") from None
    if not isinstance(payload, dict):
        raise ValueError("Grok Bot MCP adapter returned an invalid response")
    return payload


def group_contacts(group: str) -> tuple[Contact, ...]:
    return ADMIN if group == "admin" else PROVIDERS if group == "providers" else WORKERS


def tools(group: str) -> list[dict]:
    listed = [{"name": f"{group}_status", "description": f"Report {group} contact readiness.",
               "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
               "annotations": {"readOnlyHint": True}}]
    for contact in group_contacts(group):
        listed.append({
            "name": f"{group}_{contact.name}_{contact.action}",
            "description": f"Contact only {contact.name} through its existing gated adapter.",
            "inputSchema": {"type": "object", "properties": {
                "text": {"type": "string", "maxLength": MAX_INPUT}},
                "required": ["text"], "additionalProperties": False},
            "annotations": {"readOnlyHint": False, "openWorldHint": True},
        })
    if group != "admin":
        for suffix, (_, properties, read_only) in GROK_BOT_TOOLS.items():
            listed.append({
                "name": f"{group}_grok_bot_{suffix}",
                "description": f"Use only the separate Grok Bot desktop adapter: {suffix}.",
                "inputSchema": {"type": "object", "properties": properties,
                                "required": list(properties), "additionalProperties": False},
                "annotations": {"readOnlyHint": read_only, "openWorldHint": not read_only},
            })
    return listed


def status(group: str) -> dict:
    contacts = {}
    for contact in group_contacts(group):
        try:
            contacts[contact.name] = contact.status()
        except Exception as exc:
            contacts[contact.name] = {"adapter_error": type(exc).__name__}
    if group != "admin":
        try:
            contacts["grok_bot"] = (grok_bot_call("grok_bot_status", {}) if grok_bot_script()
                                    else {"adapter_configured": False})
        except Exception as exc:
            contacts["grok_bot"] = {"adapter_error": type(exc).__name__}
    return {"wrapper": group, "authority_granted": False, "contacts": contacts}


def call(group: str, name: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    if name == f"{group}_status":
        if arguments:
            raise ValueError("status accepts no arguments")
        return status(group)
    for contact in group_contacts(group):
        if name == f"{group}_{contact.name}_{contact.action}":
            if set(arguments) != {"text"}:
                raise ValueError("only text is accepted")
            return contact.invoke(validate_text(arguments["text"]))
    if group != "admin":
        for suffix, (original, properties, _) in GROK_BOT_TOOLS.items():
            if name == f"{group}_grok_bot_{suffix}":
                if set(arguments) != set(properties):
                    raise ValueError("Grok Bot arguments do not match the schema")
                if any(not isinstance(value, str) or len(value) > MAX_INPUT
                       for value in arguments.values()):
                    raise ValueError("Grok Bot arguments must be bounded strings")
                return grok_bot_call(original, arguments)
    raise ValueError("unknown group tool")


def serve(group: str) -> None:
    if group not in {"admin", "providers", "workers"}:
        raise ValueError("unknown group")
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
                          "serverInfo": {"name": f"director-{group}", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools(group)}
            elif method == "tools/call":
                try:
                    params = request.get("params") or {}
                    value = call(group, params.get("name"), params.get("arguments") or {})
                    result = content(value, provider=group)
                except Exception as exc:
                    result = content({"refused": str(exc) if isinstance(exc, ValueError)
                                      else type(exc).__name__}, error=True, provider=group)
            else:
                raise ValueError("unknown MCP method")
            if request.get("id") is not None:
                reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            else:
                continue
        except Exception:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32603, "message": "Invalid MCP request"}}
        print(json.dumps(reply, ensure_ascii=False, separators=(",", ":")), flush=True)
