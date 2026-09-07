# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]]
"""Strict native Codex and AGY receipt captures; never an enrollment authority.

Stream shapes follow the installed native transport evidence. Neither stream
observes an account identity or a served backend model. Those remain unknown.
The AGY lane therefore fails closed: its only authentication field is a
producer-controlled request label, so it cannot authorize an acceptance receipt
until protected host enrollment supplies trusted native identity readback.
"""
import json
import re

from .native_acceptance_issuer import IssuerError, _pairs, canonical


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
    return canonical(dict(protocol="codex-exec/v1" if provider == "openai" else "agy-stream/v1",
        requested_model=model, observed_model=None, observed_account=None, cwd=str(cwd), auth=auth,
        events=[json.loads(line, object_pairs_hook=_pairs) for line in raw.splitlines() if line.strip()]))
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | END]]
