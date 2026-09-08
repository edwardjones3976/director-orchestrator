# [[OpenAI Codex | GPT-6 | 2026-09-07 | 23:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]]
"""Strict native Codex, AGY and Grok receipt captures; never an enrollment authority.

Stream shapes follow the installed native transport evidence. No stream here
observes an account identity or a served backend model. Those remain unknown.
The AGY lane therefore fails closed: its only authentication field is a
producer-controlled request label, so it cannot authorize an acceptance receipt
until protected host enrollment supplies trusted native identity readback. The
Grok parser follows documented ACP metadata; account and effect qualification
remain separate from syntactic completion.
"""
import json
import re
import uuid

from .native_acceptance_issuer import IssuerError, _pairs, canonical

PROTOCOLS = {"openai": "codex-exec/v1", "gemini": "agy-stream/v1", "grok": "grok-cli-stream/v1"}
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | BEGIN]]
# Requested, never observed here: this label cannot establish native account
# authentication or substitute for protected host qualification.
GROK_AUTH_LABEL = "native-build-cli-login-requested"
# Documentation-derived ACP vocabulary, not proof of a successful native run.
GROK_STREAM_EVENTS = frozenset({"available_commands", "thought", "text", "usage", "plan"})
GROK_TOOL_EVENTS = frozenset({"tool_call", "tool_call_update"})
GROK_TERMINAL_EVENTS = frozenset({"end"})
# Explicit canonical IDs; an empty --tools value and wildcard tool name do not
# prove tool removal. MCP meta-tools remain available according to vendor docs.
GROK_DENY_TOOLS = "run_terminal_cmd,search_replace,web_search,web_fetch,Agent"


def require_grok_terminal_qualification():
    """Deployment gate, distinct from the documentation-derived stream parser.

    No qualified native account/effect containment evidence is installed for
    this lane. A plausible stream or model label must never remove this gate.
    """
    raise IssuerError("native Grok acceptance lane unavailable: protected account and "
                      "effective tool containment qualification are not implemented")


def grok_session_id(run_nonce):
    """Bind the native session identifier to this exact request's run nonce."""
    if not isinstance(run_nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", run_nonce):
        raise IssuerError("native run nonce must bind this exact attempt")
    return str(uuid.UUID(hex=run_nonce))


def grok_review_argv(command, *, prompt_file, run_nonce, cwd, model):
    """Documented one-turn ACP invocation; requested restrictions need host proof."""
    if not isinstance(model, str) or not model.strip():
        raise IssuerError("native requested model is missing")
    return [*command, "--prompt-file", str(prompt_file), "--cwd", str(cwd),
            "--model", model, "--output-format", "streaming-json",
            "--session-id", grok_session_id(run_nonce), "--max-turns", "1",
            "--disallowed-tools", GROK_DENY_TOOLS, "--no-subagents",
            "--disable-web-search", "--no-auto-update", "--permission-mode", "dontAsk",
            "--deny", "Bash", "--deny", "Edit", "--deny", "Write", "--deny", "MCPTool"]


def grok_attempt(command, *, cwd, env, model, request, timeout):
    """One bounded native attempt, currently refused before any work or I/O."""
    from pathlib import Path

    from .native_acceptance_capture import prompt_for, run_bounded
    # No paid attempt may start while the lane cannot qualify its own result.
    require_grok_terminal_qualification()
    prompt_file = Path(cwd) / "review-prompt.txt"
    prompt_file.write_text(prompt_for(request), encoding="utf-8")
    code, raw = run_bounded(grok_review_argv(command, prompt_file=prompt_file,
                                             run_nonce=request["run_nonce"], cwd=cwd, model=model),
                            cwd=cwd, env=env, timeout=timeout)
    return code, capture_document(raw, provider="grok", model=model, cwd=cwd, auth=GROK_AUTH_LABEL)


def parse_grok_events(events, *, model, request):
    """Validate documented ACP structure only; return a request-bound session.

    This is not an account assertion, provider signature, or permission to issue
    a receipt. modelUsage is CLI-reported metadata, not authenticated identity.
    Messages wire format is intentionally unsupported on this ACP-only route.
    """
    if not isinstance(events, list) or not events or not all(isinstance(e, dict) for e in events):
        raise IssuerError("incomplete native event stream")
    from .native_acceptance_capture import _verdict
    text, terminal = [], None
    for event in events:
        if terminal is not None:
            raise IssuerError("event after native terminal completion")
        kind = event.get("type")
        if not isinstance(kind, str):
            raise IssuerError("unsupported native event")
        if kind in GROK_TOOL_EVENTS:
            raise IssuerError("tools are prohibited in native review")
        if kind == "end":
            terminal = event
        elif kind in {"text", "thought"}:
            chunk = event.get("data")
            if not isinstance(chunk, str):
                raise IssuerError("native text event is not a text document")
            if kind == "text":
                text.append(chunk)
        elif kind not in GROK_STREAM_EVENTS:
            raise IssuerError("unsupported native event")
    if terminal is None or terminal.get("stopReason") != "end_turn":
        raise IssuerError("missing successful native terminal completion")
    session = terminal.get("sessionId")
    if session != grok_session_id(request.get("run_nonce")):
        raise IssuerError("native session does not bind this attempt")
    request_id = terminal.get("requestId")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
        raise IssuerError("missing native request identifier")
    usage = terminal.get("modelUsage")
    if (not isinstance(usage, dict) or set(usage) != {model}
        or not isinstance(usage[model], dict)
        or type(usage[model].get("modelCalls")) is not int
        or usage[model]["modelCalls"] != 1
        or type(terminal.get("num_turns")) is not int
        or terminal["num_turns"] != 1
        or terminal.get("usage_is_incomplete", False) is not False):
        raise IssuerError("missing or inconsistent native model/turn metadata")
    _verdict("".join(text), request)
    return session


def _parse_grok(capture, *, model, request):
    if capture["protocol"] != PROTOCOLS["grok"] or capture["auth"] != GROK_AUTH_LABEL:
        raise IssuerError("unsupported native capture")
    parse_grok_events(capture["events"], model=model, request=request)
    # Successful syntax validation cannot establish account identity or contain
    # side effects. Keep the receipt gate separate and closed until enrolled.
    require_grok_terminal_qualification()
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | END]]


def parse_additional(capture, *, provider, model, request, native_auth=None):
    from .native_acceptance_capture import _object, _verdict
    if set(capture) != {"protocol", "requested_model", "observed_model", "observed_account", "cwd", "events", "auth"}:
        raise IssuerError("native capture fields differ")
    if capture["requested_model"] != model or capture["observed_model"] is not None or capture["observed_account"] is not None:
        raise IssuerError("requested model mismatch or fabricated observation")
    if not isinstance(capture["cwd"], str) or not capture["cwd"]:
        raise IssuerError("missing native execution directory")
    events = capture["events"]
    if not isinstance(events, list) or len(events) < 2 or not all(isinstance(e, dict) for e in events):
        raise IssuerError("incomplete native event stream")
    if provider == "grok":
        return _parse_grok(capture, model=model, request=request)
    if provider == "openai":
        if capture["protocol"] != "codex-exec/v1" or capture["auth"] != "Logged in using ChatGPT":
            raise IssuerError("native ChatGPT authentication required")
        session, started, done, message = None, False, False, None
        completed_items = set()
        for event in events:
            kind = event.get("type")
            if done:
                raise IssuerError("event after native terminal completion")
            if kind == "thread.started" and session is None and not started:
                session = event.get("thread_id")
                if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", session):
                    raise IssuerError("invalid native session")
            elif kind == "turn.started" and session and not started:
                started = True
            elif kind in {"item.started", "item.updated", "item.completed"} and started:
                item = _object(event.get("item", {}), "native item is not an object")
                if item.get("type") not in {"reasoning", "agent_message"}:
                    raise IssuerError("tools are prohibited in native review")
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id or item_id in completed_items:
                    raise IssuerError("invalid or duplicate native item")
                if kind == "item.completed":
                    completed_items.add(item_id)
                    if item["type"] == "agent_message":
                        if message is not None:
                            raise IssuerError("multiple native verdicts")
                        message = item.get("text")
            elif kind == "turn.completed" and started and message is not None:
                done = True
            else:
                raise IssuerError("unsupported native event order")
        if not done:
            raise IssuerError("missing native terminal completion")
        _verdict(message, request)
        return session
    if provider != "gemini" or capture["protocol"] != "agy-stream/v1" or capture["auth"] != "native-cached-credentials-requested":
        raise IssuerError("unsupported native capture")
    # AGY has no machine-readable account observation in this installed stream.
    # Auth/account mode is proved at protected host enrollment, not by this text.
    if events[0].get("event") != "init" or events[-1].get("event") != "result":
        raise IssuerError("missing native init or result")
    session = events[0].get("conversation_id")
    init = _object(events[0].get("init", {}), "native init is not an object")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", session) or init.get("model") != model or init.get("cwd") != capture["cwd"]:
        raise IssuerError("native session, requested model or directory mismatch")
    for event in events[1:-1]:
        step = _object(event.get("step_update", {}), "native step update is not an object")
        if event.get("event") != "step_update" or step.get("conversation_id") != session:
            raise IssuerError("duplicate, foreign or unsupported native event")
        if step.get("step_type") not in {"user_input", "agent_response"} or step.get("tool_info") or step.get("subagent_info"):
            raise IssuerError("tools or subagents are prohibited in native review")
    result = _object(events[-1].get("result", {}), "native result is not an object")
    if result.get("conversation_id") != session or result.get("status") != "SUCCESS" or result.get("error"):
        raise IssuerError("native result is incomplete or foreign")
    _verdict(result.get("response", ""), request)
    if "structured_output" in result:
        _verdict(canonical(result["structured_output"]).decode(), request)
    # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
    # Everything above is stream structure the producer controls. The requested
    # auth label and this session id are not evidence that the native account is
    # authenticated, so no receipt may be signed from this capture. Removing this
    # fail-closed step requires trusted enrollment readback, not a stream field.
    from .native_auth_enrollment import VerifiedNativeAuth
    if (not isinstance(native_auth, VerifiedNativeAuth)
        or request.get("native_auth_enrollment_digest") != native_auth.enrollment_digest):
        raise IssuerError("native AGY acceptance lane unavailable: independent native "
                          "authentication readback is not enrolled for this issuer")
    return session
    # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]


def capture_document(raw, *, provider, model, cwd, auth):
    protocol = PROTOCOLS.get(provider)
    if protocol is None:
        raise IssuerError("unsupported native capture")
    return canonical(dict(protocol=protocol,
        requested_model=model, observed_model=None, observed_account=None, cwd=str(cwd), auth=auth,
        events=[json.loads(line, object_pairs_hook=_pairs) for line in raw.splitlines() if line.strip()]))
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 23:35 UTC | END]]
