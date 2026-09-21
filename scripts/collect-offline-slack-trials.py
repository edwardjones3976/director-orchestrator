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
STATUS_RECEIPT_DEPENDENCY = "issue-230-status-receipt-interface-not-accepted"


def _identities(items, key):
    return sorted({item[key] for item in items if isinstance(item, dict)
                   and isinstance(item.get(key), str) and item[key]})


def _typed_list(task, key):
    return task.get(key) if isinstance(task.get(key), list) else None


def qualification_evidence(task_id, task, rows, rows_bound, input_message, final):
    """Section 4 correlation chain derived only from observed sources.

    Every link the exposed task/delivery projections do not carry is null and
    reported as UNKNOWN; nothing here manufactures a receipt, an attempt or a
    cost. Status-receipt counts stay an explicit Issue #230 dependency.
    """
    intake = task.get("intake") if isinstance(task.get("intake"), dict) else {}
    outcomes = _typed_list(task, "assignment_outcomes") or []
    runs, history = _typed_list(task, "provider_runs"), _typed_list(task, "provider_run_history")
    contacts = _typed_list(task, "provider_contact_attempts")
    contact_history = _typed_list(task, "provider_contact_attempt_history")
    ledger = _typed_list(task, "provider_interaction_ledger")
    terminal = [r for r in rows if r.get("notification_key") == "terminal"] if rows_bound else []
    delivery = terminal[0] if len(terminal) == 1 else {}
    states = {r.get("state") for r in rows} if rows_bound else set()
    lists_known = all(value is not None for value in (runs, history, contacts, contact_history, ledger))
    if not lists_known:
        effect_state = None
    elif states & {"AMBIGUOUS"}:
        effect_state = "AMBIGUOUS_DELIVERY_EFFECT"
    elif not any((runs, history, contacts, contact_history, ledger)):
        effect_state = "ZERO_PROVIDER_EFFECT"
    else:
        effect_state = "PROVIDER_EFFECT_RECORDED"
    cost = task.get("actual_cost_usd")
    usage = task.get("usage") if isinstance(task.get("usage"), dict) else None
    measured_cost = cost if type(cost) in (int, float) and cost >= 0 else None
    return {
        "chain": {
            "source_event_id_sha256": digest(encode(intake["source_event_id"]))
                if isinstance(intake.get("source_event_id"), str) and intake["source_event_id"] else None,
            "task_id": task_id,
            "assignment_ids": _identities(outcomes, "assignment_id"),
            "attempt_ids": _identities((runs or []) + (history or []), "run_id"),
            "effect_ids": _identities(rows, "client_msg_id") if rows_bound else [],
            "provider_result_digests": _identities(outcomes, "provider_result_digest"),
            "slack_delivery_ts": delivery.get("slack_ts") if TS.fullmatch(str(delivery.get("slack_ts", ""))) else None,
            "slack_readback_state": delivery.get("state") if isinstance(delivery.get("state"), str) else None,
        },
        # Distinct timestamps; the exposed projections carry only the Slack
        # request and delivery/readback edges. Provider-side edges are UNKNOWN.
        "timestamps": {
            "request_ts": input_message.get("ts") if input_message else None,
            "first_byte_at": None, "completion_at": None, "validation_at": None,
            "delivery_at": delivery.get("acknowledged_at") if isinstance(delivery.get("acknowledged_at"), str) else None,
            "readback_at": delivery.get("delivered_at") if isinstance(delivery.get("delivered_at"), str) else None,
        },
        "lineage": {
            "provider_routes": _identities(outcomes, "provider"),
            "transport": None,
            "recorded_attempt_count": len(runs) if runs is not None else None,
            "attempt_history_count": len(history) if history is not None else None,
            "contact_attempt_count": len(contacts) if contacts is not None else None,
            "fallback_observed": None,
        },
        "effect_state": effect_state,
        "usage": {"actual_usage": usage, "actual_cost_usd": measured_cost,
                  "cost_basis": "provider_reported" if measured_cost is not None else UNKNOWN},
        "status_receipts": {"tasks_created": None, "assignments_created": None,
                            "provider_attempts_created": None, "deduplicated_receipts": None,
                            "dependency": STATUS_RECEIPT_DEPENDENCY},
        "allocation": {"rolling_twenty": UNKNOWN, "deviation_from_target_points": None},
        "revision_binding": None,
        "terminal_message_ts": final.get("ts") if final else None,
    }


def revision_binding(runtime, env):
    """Exact revision plus a configuration digest over names only, never values."""
    public = {key: runtime.get(key) for key in ("runtime_head", "image_id", "offline_configured", "paid_configured")}
    return {**public, "configuration_digest": digest(encode({**public, "environment_names": sorted(env)}))}


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
                    task, headers = collector.get(API + "/v1/tasks/" + task_id)
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
