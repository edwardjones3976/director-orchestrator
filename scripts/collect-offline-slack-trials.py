# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 20:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 10:24 UTC | BEGIN]]
"""Read explicit Director Slack trials; never post, approve, dispatch or score."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import html
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import time

import httpx
import jsonschema

SERVICE = "director-orchestrator-orchestrator-1"
API = "http://127.0.0.1:8080"
SLACK = "https://slack.com/api/"
LABEL = "SIMULATION — NO PROVIDER INTERACTION"
MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 5
TOTAL_SECONDS = 120
TS = re.compile(r"\d{10,}\.\d{1,6}\Z")
TASK = re.compile(r"DIR-LG-[A-F0-9]+\Z")
HEAD = re.compile(r"[a-f0-9]{40}\Z")


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False).encode()


def sensitive_bytes(raw, env):
    """Include JSON-escaped reflections without retaining the decoded payload."""
    try:
        canonical = encode(json.loads(raw))
    except (ValueError, TypeError, UnicodeError):
        canonical = raw
    for key, value in env.items():
        if not value or not (any(part in key for part in ("TOKEN", "SECRET", "PASSWORD", "DSN", "ALLOWLIST", "API_KEY"))
                or key in {"SLACK_DIRECTOR_CHANNEL_ID", "SLACK_OWNER_USER_ID"}):
            continue
        escaped = json.dumps(value, ensure_ascii=True)[1:-1].encode()
        if any(marker in body for marker in (value.encode(), escaped) for body in (raw, canonical)):
            return True
    return any(re.search(pattern, canonical) for pattern in (
        rb'\b[CUWB](?=[A-Z0-9]{8,}\b)(?=[A-Z0-9]*[0-9])[A-Z0-9]+\b', rb'postgres(?:ql)?://'))


def validate_observations(report):
    schema = Path(__file__).resolve().parents[1] / "docs/schemas/director-slack-trial-observations-v1.schema.json"
    jsonschema.Draft202012Validator(json.loads(schema.read_bytes())).validate(report)


def protect_directory(directory):
    """Create a new private directory using only platform tools, before writes."""
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    if os.name == "nt":
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                capture_output=True, timeout=10, check=True)
        sid = list(csv.reader(result.stdout.decode().splitlines()))[0][1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise ValueError("Private directory identity unavailable")
        subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                        "*S-1-5-32-544:(OI)(CI)F"],
                       capture_output=True, timeout=10, check=True)


def inspect_runtime(expected_head):
    result = subprocess.run(["docker", "inspect", SERVICE], capture_output=True, timeout=15)
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError("Canonical runtime inspection failed")
    item = json.loads(result.stdout)[0]
    labels = item.get("Config", {}).get("Labels") or {}
    ports = item.get("NetworkSettings", {}).get("Ports", {}).get("8080/tcp") or []
    if (labels.get("org.director.candidate.commit") != expected_head
            or item.get("State", {}).get("Running") is not True
            or not any(p.get("HostIp") == "127.0.0.1" and p.get("HostPort") == "8080" for p in ports)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("Image", ""))):
        raise ValueError("Canonical runtime image/head/port binding differs")
    env = dict(value.split("=", 1) for value in item["Config"].get("Env", []) if "=" in value)
    if (not re.fullmatch(r"C[A-Z0-9]+", env.get("SLACK_DIRECTOR_CHANNEL_ID", ""))
            or not re.fullmatch(r"U[A-Z0-9]+", env.get("SLACK_OWNER_USER_ID", ""))
            or not env.get("SLACK_BOT_TOKEN") or not env.get("ORCHESTRATOR_SHARED_SECRET")):
        raise ValueError("Canonical runtime channel/identity/credentials unavailable")
    public = {"container_id": item["Id"], "image_id": item["Image"],
        "runtime_head": expected_head, "started_at": item["State"].get("StartedAt"),
        "offline_configured": env.get("LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED") == "true",
        "paid_configured": env.get("ALLOW_PAID_RUNS") == "true"}
    return public, env


def parse_trials(values):
    if not 1 <= len(values) <= 10:
        raise ValueError("Supply between one and ten explicit trials")
    if any(not TS.fullmatch(value) for value in values) or len(set(values)) != len(values):
        raise ValueError("Each --trial must be a distinct THREAD_TIMESTAMP")
    return values


class Collector:
    def __init__(self, client, output, env, private_dir=None):
        self.client, self.output, self.env = client, output, env
        self.private_dir = private_dir
        self.deadline = time.monotonic() + TOTAL_SECONDS
        self.references = []

    def get(self, url, *, params=None):
        if time.monotonic() >= self.deadline:
            raise ValueError("Collection deadline reached")
        if url.startswith(SLACK):
            if url not in (SLACK + "auth.test", SLACK + "conversations.replies"):
                raise ValueError("Unsupported Slack read")
            token = self.env["SLACK_BOT_TOKEN"]
        elif url == API + "/health" or re.fullmatch(
                re.escape(API) + r"/v1/(?:tasks/DIR-LG-[A-F0-9]+|primary/tasks/DIR-LG-[A-F0-9]+/deliveries)", url):
            token = self.env["ORCHESTRATOR_SHARED_SECRET"]
            # The only permitted API query is the read-only readback opt-in on a task.
            if params is not None and (params != READBACK_QUERY or "/v1/tasks/" not in url):
                raise ValueError("Unsupported API query")
        else:
            raise ValueError("Unsupported source")
        started = utc()
        end = min(self.deadline, time.monotonic() + 10)
        with self.client.stream("GET", url, params=params,
                headers={"Authorization": "Bearer " + token}) as response:
            body = bytearray()
            for chunk in response.iter_bytes(8192):
                body.extend(chunk)
                if len(body) > MAX_BYTES or time.monotonic() >= end:
                    raise ValueError("Read exceeded byte/time bound")
            headers = {key: value for key, value in response.headers.items()
                       if key.lower().startswith("x-primary-") or key.lower() == "x-task-engine-owner"}
            status = response.status_code
        finished = utc()
        reference = {"file": None, "source": url, "http_status": status,
            "body_sha256": digest(body), "original_sha256": None,
            "started_at": started, "finished_at": finished}
        # Hash all responses; never retain credentials or Slack account/channel IDs,
        # even when the upstream reflects them. Sensitive originals are withheld.
        if self.private_dir is not None and not sensitive_bytes(body, self.env):
            original = {"source": url, "started_at": started, "finished_at": finished,
                "http_status": status, "body_base64": base64.b64encode(body).decode()}
            name = f"original-{len(self.references) + 1:03d}.json"
            stored = encode(original)
            with (self.private_dir / name).open("xb") as handle:
                handle.write(stored)
            reference.update(file=name, original_sha256=digest(stored))
        self.references.append(reference)
        if status != 200:
            raise ValueError("Source did not return HTTP 200")
        return json.loads(body), headers

    def replies(self, thread):
        messages, cursors = {}, set()
        cursor = None
        try:
            for _ in range(MAX_PAGES):
                params = {"channel": self.env["SLACK_DIRECTOR_CHANNEL_ID"], "ts": thread, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                page, _ = self.get(SLACK + "conversations.replies", params=params)
                if (page.get("ok") is not True or not isinstance(page.get("messages"), list)
                        or len(page["messages"]) > 100):
                    raise ValueError("Slack replies unavailable")
                for message in page["messages"]:
                    ts = message.get("ts") if isinstance(message, dict) else None
                    if (not isinstance(ts, str) or not TS.fullmatch(ts)
                            or (ts != thread and message.get("thread_ts") != thread)):
                        raise ValueError("Unexpected thread/message identity")
                    if ts in messages and messages[ts] != message:
                        raise ValueError("Message changed between pages")
                    messages[ts] = message
                cursor = (page.get("response_metadata") or {}).get("next_cursor", "")
                if page.get("has_more") is False and not cursor:
                    return list(messages.values()), True, None
                if not isinstance(cursor, str) or not cursor or len(cursor) > 1024 or cursor in cursors:
                    raise ValueError("Incomplete or repeated pagination cursor")
                cursors.add(cursor)
            return list(messages.values()), False, "page_bound_reached"
        except (ValueError, TypeError, AttributeError, httpx.HTTPError):
            return list(messages.values()), False, "replies_incomplete_or_unavailable"


def task_from_accepted(thread, messages, complete, bot):
    if not complete:
        raise ValueError("Task lookup requires complete thread readback")
    # slack_bot.py:658 sends the current "REQUEST RECEIVED" wording; "ACCEPTED" is
    # the retained legacy wording. Either binds a task ID the same way, and any
    # duplicate or mixed-wording match within the thread stays ambiguous.
    pattern = re.compile(r"(?<![A-Z0-9-])(DIR-LG-[A-F0-9]+)\s+—\s+(?:ACCEPTED|REQUEST RECEIVED)\b")
    matches = [match.group(1) for message in messages
        if message.get("user") == bot and message.get("thread_ts") == thread
        for match in pattern.finditer(html.unescape(str(message.get("text", ""))))]
    if len(matches) != 1:
        raise ValueError("Exactly one native task receipt required")
    return matches[0]


def owner_pause(rows, wall, input_ts, final_ts):
    """Sum non-overlapping approved waits; missing/denied evidence stays unknown."""
    if wall is None:
        return None, None
    intervals = []
    try:
        for row in rows:
            if not str(row.get("notification_key", "")).startswith("approval:"):
                continue
            if (row.get("owner_decision") != "approve" or row.get("state") != "DELIVERED"
                    or not row.get("owner_command_id") or not TS.fullmatch(row.get("slack_ts", ""))):
                return None, None
            at = datetime.fromisoformat(row["owner_decision_at"])
            if at.tzinfo is None:
                return None, None
            start, end = Decimal(row["slack_ts"]), Decimal(str(at.timestamp()))
            if not Decimal(input_ts) <= start <= end <= Decimal(final_ts):
                return None, None
            intervals.append((start, end))
        intervals.sort()
        if any(right[0] < left[1] for left, right in zip(intervals, intervals[1:])):
            return None, None
        pause = float(sum((end - start for start, end in intervals), Decimal(0)))
        return pause, wall - pause
    except (KeyError, ValueError, TypeError, OverflowError):
        return None, None


UNKNOWN = "UNKNOWN"
READBACK_QUERY = {"include_qualification_evidence": "true"}
READBACK_SCHEMA = "director-qualification-readback/v1"
STATUS_RECEIPT_DEPENDENCY = "issue-230-receipts-collected;zero-work-delta-requires-prospective-baseline"
SECTIONS = ("assignments", "provider_attempts", "effects", "reconciliation_events",
            "slack_deliveries", "status_receipts", "provider_usage")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


def _identities(items, key):
    return sorted({item[key] for item in items if isinstance(item, dict)
                   and isinstance(item.get(key), str) and item[key]})


def _typed_list(task, key):
    return task.get(key) if isinstance(task.get(key), list) else None


def _readback(task_id, task):
    """Accept the typed opt-in readback only when it is bound to this task."""
    readback = task.get("qualification_evidence")
    if readback is None:
        return None, "absent", "opt-in readback not present in the task response"
    if (not isinstance(readback, dict) or readback.get("schema_version") != READBACK_SCHEMA
            or readback.get("task_id") != task_id or type(readback.get("complete")) is not bool
            or any(not isinstance(readback.get(name), list) for name in SECTIONS)
            or any(not isinstance(row, dict) for name in SECTIONS for row in readback[name])):
        return None, "rejected", "readback schema/task binding mismatch"
    attempts = readback["provider_attempts"]
    keys = [(r.get("assignment_id"), r.get("attempt_ordinal"), r.get("event_sequence")) for r in attempts]
    assignment_ids = {r.get("assignment_id") for r in readback["assignments"]}
    if (len(set(keys)) != len(keys)
            or any(not DIGEST.fullmatch(str(r.get("attempt_digest", ""))) for r in attempts)
            or any(r.get("assignment_id") not in assignment_ids for r in attempts)
            or len({r.get("assignment_id") for r in readback["assignments"]}) != len(readback["assignments"])
            or len({r.get("reservation_key") for r in readback["effects"]}) != len(readback["effects"])
            or len({r.get("client_msg_id") for r in readback["slack_deliveries"]}) != len(readback["slack_deliveries"])
            or len({r.get("receipt_id") for r in readback["status_receipts"]}) != len(readback["status_receipts"])):
        return None, "rejected", "duplicate or unjoined identities in readback rows"
    if not readback["complete"]:
        return readback, "incomplete", "readback truncated: " + ",".join(map(str, readback.get("truncated", [])))
    return readback, "collected", None


def _provider_effect_state(attempts):
    statuses = {str(r.get("status")) for r in attempts}
    if not attempts:
        return "ZERO_PROVIDER_ATTEMPT"
    if "AMBIGUOUS" in statuses:
        return "PROVIDER_ATTEMPT_AMBIGUOUS"
    if "SUCCEEDED" in statuses or "FAILED" in statuses:
        return "PROVIDER_RESULT_RECORDED"
    return "PROVIDER_ATTEMPT_STARTED_UNSETTLED"


def _external_effect_state(effects):
    if not effects:
        return "NO_EXTERNAL_EFFECT_RESERVATION"
    if any(r.get("terminal_disposition") for r in effects):
        return "EXTERNAL_EFFECT_TERMINAL:" + ",".join(sorted({str(r.get("terminal_disposition")) for r in effects
                                                              if r.get("terminal_disposition")}))
    if any(r.get("execution_started_at") and not r.get("remote_identifier") for r in effects):
        return "EXTERNAL_EFFECT_AMBIGUOUS"
    if any(r.get("remote_identifier") for r in effects):
        return "EXTERNAL_EFFECT_BOUND_UNCONFIRMED"
    return "EXTERNAL_EFFECT_RESERVED_NO_EFFECT"


def qualification_evidence(task_id, task, rows, rows_bound, input_message, final):
    """Section 4 correlation chain derived from the typed opt-in readback.

    Identities come from real persisted keys: assignment_id/sequence, attempt
    digest/ordinal, effect reservation/effect_key digests, Slack delivery
    client_msg_id and status receipt_id are kept as distinct families. Nothing
    here manufactures a receipt, an attempt or a cost; an absent, rejected or
    truncated readback leaves the derived facts null with the stated reason.
    """
    intake = task.get("intake") if isinstance(task.get("intake"), dict) else {}
    outcomes = _typed_list(task, "assignment_outcomes") or []
    history = _typed_list(task, "provider_run_history")
    contacts = _typed_list(task, "provider_contact_attempts")
    readback, state, reason = _readback(task_id, task)
    attempts = readback["provider_attempts"] if readback else []
    effects = readback["effects"] if readback else []
    deliveries = readback["slack_deliveries"] if readback else (rows if rows_bound else [])
    receipts = readback["status_receipts"] if readback else []
    usage_rows = readback["provider_usage"] if readback else []
    terminal = [r for r in deliveries if r.get("notification_key") == "terminal"]
    delivery = terminal[0] if len(terminal) == 1 else {}
    delivery_states = {str(r.get("state")) for r in deliveries}
    settled = [r for r in attempts if r.get("status") in ("SUCCEEDED", "FAILED")]
    if state != "collected":
        effect_state = None
    elif "AMBIGUOUS" in delivery_states or _provider_effect_state(attempts) == "PROVIDER_ATTEMPT_AMBIGUOUS" \
            or _external_effect_state(effects) == "EXTERNAL_EFFECT_AMBIGUOUS":
        effect_state = "AMBIGUOUS_DELIVERY_EFFECT"
    elif not attempts and not any(r.get("execution_started_at") for r in effects):
        effect_state = "ZERO_PROVIDER_EFFECT"
    elif any(r.get("status") == "SUCCEEDED" for r in attempts):
        effect_state = "PROVIDER_EFFECT_RECORDED"
    else:
        effect_state = None
    authenticated_microusd = [r.get("actual_microusd") for r in usage_rows]
    if usage_rows and all(type(v) is int and v >= 0 for v in authenticated_microusd):
        measured_cost, cost_basis = sum(authenticated_microusd) / 1_000_000, "authenticated_usage_table"
        usage = {"authenticated_microusd": sum(authenticated_microusd),
                 "observed_tokens": sum(int(r.get("observed_tokens", 0)) for r in usage_rows),
                 "reserved_budget_microusd": sum(int(r.get("reserved_microusd", 0)) for r in usage_rows),
                 "attempt_digests": _identities(usage_rows, "attempt_digest")}
    else:
        cost = task.get("actual_cost_usd")
        measured_cost = cost if type(cost) in (int, float) and cost == cost and cost not in (float("inf"),) \
            and cost >= 0 else None
        cost_basis = "provider_reported" if measured_cost is not None else UNKNOWN
        usage = task.get("usage") if isinstance(task.get("usage"), dict) else None
    ordinals = {(r.get("assignment_id"), r.get("attempt_ordinal")) for r in attempts}
    providers_per_assignment = {}
    for r in attempts:
        providers_per_assignment.setdefault(r.get("assignment_id"), set()).add(r.get("provider_id"))
    fallback = None if not attempts else (any(len(v) > 1 for v in providers_per_assignment.values())
                                          or len(ordinals) > len(providers_per_assignment))
    completion = max((str(r.get("recorded_at")) for r in settled), default=None)
    return {
        "readback": {"state": state, "reason": reason,
                     "source": (API + "/v1/tasks/" + task_id + "?include_qualification_evidence=true")
                     if readback is not None else None,
                     "complete": readback.get("complete") if readback else None,
                     "truncated": list(readback.get("truncated", [])) if readback else [],
                     "absent": list(readback.get("absent", [])) if readback else [],
                     "snapshot": readback.get("snapshot") if readback else None,
                     "projection_digest": readback.get("projection_digest") if readback else None},
        "chain": {
            "source_event_id_sha256": digest(encode(intake["source_event_id"]))
                if isinstance(intake.get("source_event_id"), str) and intake["source_event_id"] else None,
            "task_id": task_id,
            "assignment_ids": _identities(readback["assignments"], "assignment_id") if readback else [],
            "assignment_sequences": sorted({int(r["sequence"]) for r in readback["assignments"]
                                            if type(r.get("sequence")) is int}) if readback else
                                     sorted({int(o["assignment_sequence"]) for o in outcomes
                                             if isinstance(o, dict) and type(o.get("assignment_sequence")) is int}),
            "attempt_ids": _identities(attempts, "attempt_digest"),
            "effect_ids": _identities(effects, "effect_key_digest"),
            "delivery_ids": _identities(deliveries, "client_msg_id"),
            "status_receipt_ids": _identities(receipts, "receipt_id"),
            "provider_result_digests": _identities(settled, "result_digest") or _identities(outcomes, "provider_result_digest"),
            "slack_delivery_ts": delivery.get("slack_ts") if TS.fullmatch(str(delivery.get("slack_ts", ""))) else None,
            "slack_readback_state": delivery.get("state") if isinstance(delivery.get("state"), str) else None,
        },
        "timestamps": {
            "request_ts": input_message.get("ts") if input_message else None,
            "first_byte_at": None,
            "completion_at": completion,
            "validation_at": None,
            "delivery_at": str(delivery["acknowledged_at"]) if delivery.get("acknowledged_at") else None,
            "readback_at": str(delivery["delivered_at"]) if delivery.get("delivered_at") else None,
            "unknown_reasons": {"first_byte_at": "no producer records provider first-byte time",
                                "validation_at": "no producer records a separate validation time",
                                **({"completion_at": "no settled provider attempt row"} if completion is None else {})},
        },
        "lineage": {
            "provider_routes": _identities(attempts, "provider_id") or _identities(outcomes, "provider"),
            "transport": ",".join(_identities(attempts, "transport_id")) or None,
            "recorded_attempt_count": len(ordinals) if readback else None,
            "attempt_history_count": len(history) if history is not None else None,
            "contact_attempt_count": len(contacts) if contacts is not None else None,
            "fallback_observed": fallback,
        },
        "effect_state": effect_state,
        "provider_effect_state": _provider_effect_state(attempts) if readback else None,
        "external_effect_state": _external_effect_state(effects) if readback else None,
        "usage": {"actual_usage": usage, "actual_cost_usd": measured_cost, "cost_basis": cost_basis},
        "status_receipts": {"tasks_created": None, "assignments_created": None,
                            "provider_attempts_created": None,
                            "deduplicated_receipts": len(receipts) if readback else None,
                            "dependency": STATUS_RECEIPT_DEPENDENCY},
        "allocation": {"rolling_twenty": UNKNOWN, "deviation_from_target_points": None},
        "revision_binding": None,
        "terminal_message_ts": final.get("ts") if final else None,
    }


def revision_binding(runtime, env):
    """Read-time revision identity; configuration names only, never values.

    The names digest is not an effective-configuration digest: two runtimes
    with identical variable names and different values collide, so no value
    drift can be detected from it. Execution-time binding stays UNKNOWN.
    """
    public = {key: runtime.get(key) for key in ("runtime_head", "image_id", "offline_configured", "paid_configured")}
    return {**public, "configuration_digest": digest(encode({**public, "environment_names": sorted(env)})),
            "configuration_scope": "read-time-environment-names-and-public-flags",
            "execution_time_binding": UNKNOWN}


def summarize(thread, task_id, task, deliveries, messages, complete, owner, bot, headers, channel):
    """Summarize observed source data; this cannot create scorecard observations."""
    task = task if isinstance(task, dict) else {}
    intake = task.get("intake") if isinstance(task.get("intake"), dict) else {}
    rows_ok = isinstance(deliveries, list) and all(isinstance(row, dict) for row in deliveries)
    rows = deliveries if rows_ok else []
    rows_bound = rows_ok and all(row.get("task_id") == task_id for row in rows)
    raw = lambda message: html.unescape(str(message.get("text", "")))
    clean = lambda text: re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    messages = [m for m in messages if isinstance(m, dict) and isinstance(m.get("ts"), str)
        and TS.fullmatch(m["ts"]) and (m["ts"] == thread or m.get("thread_ts") == thread)]
    inputs = [m for m in messages if isinstance(intake.get("source_event_id"), str)
        and intake["source_event_id"] and (intake["source_event_id"] in
        (m.get("client_msg_id"), m.get("event_ts"), m.get("ts")))
        and m.get("user") == owner and not m.get("bot_id")
        and not m.get("subtype") and clean(str(m.get("text", ""))) == intake.get("text")]
    bound = (task.get("task_id") == task_id and intake.get("channel_id") == channel
        and intake.get("thread_ts") == thread and intake.get("user_id") == owner
        and headers.get("x-task-engine-owner") == "langgraph")
    input_message = inputs[0] if bound and len(inputs) == 1 else None
    bound = bool(bound and input_message)
    terminal_rows = [r for r in rows if r.get("notification_key") == "terminal"] if rows_bound else []
    ids = {r.get("client_msg_id") for r in terminal_rows if r.get("client_msg_id")}
    # Count additional bot final messages too, not just the first ledger match.
    final_pattern = re.compile(re.escape(task_id) + r"\s+—\s+(?:SUCCEEDED|FAILED|CANCELLED|POLICY_STOPPED|COMPLETED)\b")
    finals = [m for m in messages if bot and m.get("user") == bot and
              (m.get("client_msg_id") in ids or final_pattern.search(raw(m)))]
    final = finals[0] if complete and len(finals) == 1 else None
    correlated = bool(bound and final and len(terminal_rows) == 1 and terminal_rows[0].get("state") == "DELIVERED"
        and terminal_rows[0].get("client_msg_id") == final.get("client_msg_id")
        and terminal_rows[0].get("slack_ts") == final.get("ts"))
    wall = None
    if input_message and final:
        elapsed = Decimal(final["ts"]) - Decimal(input_message["ts"])
        if elapsed >= 0:
            wall = float(elapsed)
    empty_fields = ("provider_runs", "provider_run_history", "provider_contact_attempts",
                    "provider_contact_attempt_history", "provider_interaction_ledger")
    outcomes = task.get("assignment_outcomes")
    doubles = bool(isinstance(outcomes, list) and outcomes and all(isinstance(item, dict)
        and str(item.get("run_id", "")).startswith("offline-double:") and item.get("result") == LABEL for item in outcomes))
    zero_recorded = (bound and all(task.get(key) == [] for key in empty_fields)
        if all(isinstance(task.get(key), list) for key in empty_fields) else None)
    approval_rows = [r for r in rows if str(r.get("notification_key", "")).startswith("approval:")]
    pause, machine = owner_pause(rows, wall, input_message["ts"], final["ts"]) if (
        correlated and input_message and rows_bound and len(rows) < 100) else (None, None)
    safe_receipts = [{key: row.get(key) for key in ("notification_key", "projection_digest", "client_msg_id",
        "state", "claimed_at", "acknowledged_at", "delivered_at", "slack_ts", "readback_attempts",
        "owner_decision", "owner_decision_at", "owner_command_id")} for row in rows] if rows_bound else []
    return {"task_id": task_id, "thread_ts": thread, "task_record_bound": bound,
        "task_status": task.get("status") if task.get("status") in
            {"QUEUED", "PLANNING", "OWNER_DECISION_REQUIRED", "RUNNING", "REVIEWING", "DONE", "FAILED", "CANCELLED", "BLOCKED"} else None,
        "input_message_ts": input_message.get("ts") if input_message else None,
        "input_client_msg_id": input_message.get("client_msg_id") if input_message else None,
        "source_event_id_sha256": digest(encode(intake["source_event_id"]))
            if isinstance(intake.get("source_event_id"), str) and intake["source_event_id"] else None,
        "owner_account_input_matched": bool(input_message), "human_typing_attested": None,
        "task_api_gateway_attested": task.get("intake_authentication") == "slack_gateway_attested",
        "projection_digest": headers.get("x-primary-projection-digest"),
        "slack_thread_complete_at_read": complete, "observed_final_message_count": len(finals),
        "terminal_reply_count": len(finals) if complete else None,
        "terminal_messages": [{"ts": m["ts"], "client_msg_id": m.get("client_msg_id"),
            "body_sha256": digest(encode(m)), "simulation_label_present": LABEL in raw(m),
            "actual_cost_not_recorded_present": "Actual USD cost: not recorded" in raw(m)} for m in finals],
        "final_receipt_matches_native_message": correlated,
        "delivery_receipt_list_complete": rows_bound and len(rows) < 100,
        "delivery_receipts": safe_receipts, "owner_pause_seconds": pause, "machine_seconds": machine,
        "owner_decision_record_count": len([r for r in approval_rows if r.get("owner_decision_at")]),
        "authenticated_scoped_approval_verified": None,
        "wall_seconds": wall, "wall_under_60_seconds": wall < 60 if wall is not None else None,
        "all_recorded_outcomes_are_offline_doubles": doubles,
        "all_provider_result_digests_present": bool(isinstance(outcomes, list) and outcomes and all(
            isinstance(item, dict) and isinstance(item.get("provider_result_digest"), str)
            and re.fullmatch(r"[a-fA-F0-9]{64}", item["provider_result_digest"]) for item in outcomes)),
        "zero_calls_in_exposed_task_projection": zero_recorded,
        "simulation_charge_supported_usd": 0 if doubles and zero_recorded else None,
        "simulation_charge_basis": "offline double outcomes and empty exposed attempt/interaction lists; not billing or complete runtime-attempt authority",
        "actual_cost_usd": None, "operational_provider_attempts_verified": None,
        "independent_correctness_verified": None, "global_task_deduplication_verified": None,
        "execution_image_binding_verified": None, "verdict": "EVIDENCE_ONLY_NOT_QUALIFIED",
        "qualification_evidence": qualification_evidence(task_id, task, rows, rows_bound, input_message, final)}


RUBRIC_REQUIREMENTS = {
    "terminal_reply_count": 1,
    "task_status": "DONE",
    "all_recorded_outcomes_are_offline_doubles": True,
    "all_provider_result_digests_present": True,
    "terminal_simulation_label": LABEL,
    "terminal_cost_text": "Actual USD cost: not recorded",
}


def load_rubric(path):
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Rubric exceeds byte bound")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"schema", "rubric_id", "declared_at", "requirements"}
            or value["schema"] != "director-offline-structural-rubric/v1"
            or not re.fullmatch(r"[a-z0-9-]{1,80}", value["rubric_id"])
            or encode(value["requirements"]) != encode(RUBRIC_REQUIREMENTS)):
        raise ValueError("Unsupported structural rubric")
    declared = datetime.fromisoformat(value["declared_at"])
    if declared.tzinfo is None:
        raise ValueError("Rubric declaration must have a timezone")
    return value, digest(raw)


def evaluate_rubric(row, rubric, rubric_digest):
    """Recomputable structural checks; a file timestamp is not dispatch proof."""
    messages = row.get("terminal_messages") or []
    declared_before = False
    if row.get("input_message_ts"):
        declared_before = (datetime.fromisoformat(rubric["declared_at"]).timestamp()
                           <= float(row["input_message_ts"]))
    checks = {
        "predeclared": declared_before and row.get("input_rubric_sha256") == rubric_digest,
        "task_bound": row.get("task_record_bound") is True,
        "one_terminal": type(row.get("terminal_reply_count")) is int and row["terminal_reply_count"] == 1,
        "task_done": row.get("task_status") == "DONE",
        "offline_outcomes": row.get("all_recorded_outcomes_are_offline_doubles") is True,
        "result_digests": row.get("all_provider_result_digests_present") is True,
        "terminal_label": len(messages) == 1 and messages[0].get("simulation_label_present") is True,
        "terminal_cost_text": len(messages) == 1 and messages[0].get("actual_cost_not_recorded_present") is True,
        "native_delivery": row.get("final_receipt_matches_native_message") is True,
    }
    return {"rubric_sha256": rubric_digest, "checks": checks, "passed": all(checks.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--trial", action="append", required=True, help="THREAD_TIMESTAMP; repeat up to ten")
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, help="Optional new private directory; sensitive originals are withheld")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; existing reports are never overwritten")
    args = parser.parse_args(argv)
    trials, records, collector, created = [], [], None, False
    try:
        if not HEAD.fullmatch(args.expected_head):
            raise ValueError("Expected head must be a full lowercase SHA")
        trials = parse_trials(args.trial)
        rubric, rubric_digest = load_rubric(args.rubric)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        created = True
        if args.private_dir is not None:
            protect_directory(args.private_dir)
        before, env = inspect_runtime(args.expected_head)
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            collector = Collector(client, args.output_dir, env, args.private_dir)
            auth, _ = collector.get(SLACK + "auth.test")
            if (auth.get("ok") is not True or not re.fullmatch(r"[UW][A-Z0-9]+", str(auth.get("user_id", "")))
                    or not re.fullmatch(r"B[A-Z0-9]+", str(auth.get("bot_id", "")))):
                raise ValueError("Native Slack identity unavailable")
            bot = auth["user_id"]
            health, _ = collector.get(API + "/health")
            if health.get("environment") != "local":
                raise ValueError("Wrong Director environment")
            for thread in trials:
                errors, task, deliveries, headers = [], None, None, {}
                messages, complete, error = collector.replies(thread)
                try:
                    task_id = task_from_accepted(thread, messages, complete, bot)
                    if any(row.get("task_id") == task_id for row in records):
                        raise ValueError("Duplicate task binding")
                except (ValueError, TypeError, AttributeError):
                    records.append({"thread_ts": thread, "task_id": None,
                        "source_errors": ["accepted_task_lookup_unavailable"],
                        "verdict": "COLLECTION_UNAVAILABLE"})
                    continue
                try:
                    # The opt-in readback rides the same authenticated GET; the
                    # ordinary record is unchanged and the projection is read-only.
                    task, headers = collector.get(API + "/v1/tasks/" + task_id, params=dict(READBACK_QUERY))
                except (ValueError, httpx.HTTPError):
                    errors.append("task_api_unavailable")
                try:
                    deliveries, _ = collector.get(API + "/v1/primary/tasks/" + task_id + "/deliveries")
                except (ValueError, httpx.HTTPError):
                    errors.append("delivery_api_unavailable")
                row = summarize(thread, task_id, task, deliveries, messages, complete,
                    env["SLACK_OWNER_USER_ID"], bot, headers, env["SLACK_DIRECTOR_CHANNEL_ID"])
                # The owner's matched source event commits the rubric bytes before
                # dispatch. A backdated or subsequently edited rubric cannot pass.
                markers = re.findall(r"\brubric-sha256:([a-f0-9]{64})\b", (task or {}).get("intake", {}).get("text", ""))
                row["input_rubric_sha256"] = markers[0] if row["task_record_bound"] and len(markers) == 1 else None
                row["rubric_evaluation"] = evaluate_rubric(row, rubric, rubric_digest)
                row["source_errors"] = errors + ([error] if error else [])
                # Read-time revision/configuration identity, not proof of the
                # image that executed the task (see limitations).
                row["qualification_evidence"]["revision_binding"] = revision_binding(before, env)
                records.append(row)
        stable = None
        try:
            after, _ = inspect_runtime(args.expected_head)
            stable = before == after
        except (ValueError, subprocess.SubprocessError):
            after = None
        for row in records:
            row["runtime_collection_binding_stable"] = stable
        report = {"schema": "director-slack-trial-observations/v1", "observed_at": utc(),
            "expires_at": datetime.fromtimestamp(time.time() + 300, timezone.utc).isoformat(),
            "requested_trials": len(trials), "observed_trials": len(records),
            "rubric_sha256": rubric_digest,
            "collection_runtime_before": before, "collection_runtime_after": after,
            "runtime_collection_binding_stable": stable,
            "runtime_health_at_collection": {"paid_runs_enabled": health.get("paid_runs_enabled"),
                **{key: health.get("langgraph_primary", {}).get(key) for key in
                   ("ambiguous_effect_count", "reconciliation_hold_reason", "primary_runtime_active")}},
            "collector_sha256": digest(Path(__file__).read_bytes()), "read_only": True,
            "slack_writes": 0, "provider_dispatches": 0, "scorecard_mutations": 0,
            "qualified_run_count": None, "ten_consecutive_runs_verified": False,
            "native_source_reads": collector.references, "trials": records,
            "limitations": ["Account-authored input does not attest who physically typed it.",
                "Approval pause uses only delivered, approved, non-overlapping rows; legacy decisions remain unknown.",
                "Read-time runtime identity does not prove the image that originally executed the task.",
                "Complete bounded replies are a snapshot, not assurance against a later duplicate or edit.",
                "Simulation zero-call support is separate from actual billing; unknown actual cost remains null.",
                "Structural correctness is not provider answer quality or complete operational-attempt evidence.",
                "Private capture is optional, uses platform access controls and withholds sensitive originals; copied receipts are not self-authenticating.",
                "Qualification evidence links only exposed identities; provider first-byte/completion/validation times, transport, fallback, status receipts and allocation remain UNKNOWN until their collectors exist."]}
        path = args.output_dir / "receipt.json"
        validate_observations(report)
        serialized = encode(report) + b"\n"
        # Fail closed if an upstream puts a configured credential/identity in a
        # nominally safe field. Neither originals nor summaries may retain it.
        if sensitive_bytes(serialized, env):
            raise ValueError("Sensitive source field withheld")
        with path.open("xb") as handle:
            handle.write(serialized)
        print(json.dumps({"receipt": str(path.resolve()), "observed_trials": len(records),
                          "qualified_run_count": None, "runtime_collection_binding_stable": stable}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError,
            httpx.HTTPError, jsonschema.ValidationError) as error:
        # Never echo response text, environment, request headers or exception details.
        failure = {"schema": "director-slack-trial-observations/v1", "collection_failed": True,
            "observed_at": utc(), "error_type": type(error).__name__, "details_withheld": True,
            "read_only": True, "qualified_run_count": None, "trials": [
                {"thread_ts": thread, "task_id": None, "verdict": "COLLECTION_UNAVAILABLE"}
                for thread in trials],
            "native_source_reads": collector.references if collector else []}
        path = args.output_dir / "receipt.json"
        # Do not overwrite an earlier run even when the destination was supplied again.
        if created and trials and not path.exists():
            with path.open("xb") as handle:
                handle.write(encode(failure) + b"\n")
        print(json.dumps({"collection_failed": True, "error_type": type(error).__name__,
                          "receipt": str(path.resolve()) if created and path.exists() else None, "details_withheld": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 10:24 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 20:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | END]]
