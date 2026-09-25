# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:57 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 05:01 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 01:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | BEGIN]]
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
import hmac
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
    # The public flags are resolved exactly as Settings resolves them (1/true/
    # yes/on, defaults) through the same safe resolver revision_binding uses,
    # so a spelling difference cannot split the flags from their binding.
    resolved, _ = resolve_safe_configuration(env)
    public = {"container_id": item["Id"], "image_id": item["Image"],
        "runtime_head": expected_head, "started_at": item["State"].get("StartedAt"),
        "offline_configured": resolved["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"],
        "paid_configured": resolved["ALLOW_PAID_RUNS"]}
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
        status, raw_headers, body = self.drain(url, params, token)
        headers = {key: value for key, value in raw_headers.items()
                   if key.lower().startswith("x-primary-") or key.lower() == "x-task-engine-owner"}
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

    def drain(self, url, params, token, cap=10):
        """One streamed GET, bounded in bytes and in wall clock. The read ends by the earlier of its own cap and
        the collection deadline: HTTPX gets a timeout clamped to what remains (so no connect or single read can
        wait past it), and the clock is checked after every chunk (so a slow trickle, each chunk inside the
        per-read timeout, cannot keep the read open past it either)."""
        now = time.monotonic()
        end = min(self.deadline, now + cap)
        if now >= end:
            raise ValueError("Collection deadline reached")
        with self.client.stream("GET", url, params=params, headers={"Authorization": "Bearer " + token},
                                timeout=end - now) as response:
            body = bytearray()
            # No chunk_size: iter_bytes(n) buffers until n bytes arrive, so a trickle would never reach the clock
            # check; unsized, each chunk is handed over as it arrives.
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES or time.monotonic() >= end:
                    raise ValueError("Read exceeded byte/time bound")
            return response.status_code, dict(response.headers), bytes(body)

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


def unedited(message):
    """A Slack message as originally posted: no edit metadata and no subtype.

    Slack keeps an edited message's ts but returns its new text, so every Slack
    text read as evidence (collector and completion scorecard) goes through this.
    """
    return "edited" not in message and not message.get("subtype")


BEGIN_TAG = re.compile(r"\*?\[\[[^\]\n]*\|\s*BEGIN\]\]\*?")
RECEIPT_HEADER = re.compile(r"\*?(DIR-LG-[A-F0-9]+)\s+—\s+(?:ACCEPTED|REQUEST RECEIVED)\*?")


def reply_header(text):
    """A bot reply's leading line, after the contribution BEGIN tag the gateway wraps replies in.

    A receipt or BLOCKED notice is the reply's header; the same words quoted in a body (for example
    inside an untrusted provider result) are not one. The scorecard's drill parser uses this too.
    """
    lines = [line.strip() for line in html.unescape(str(text)).strip().splitlines() if line.strip()]
    if lines and BEGIN_TAG.fullmatch(lines[0]):
        lines = lines[1:]
    return lines[0] if lines else ""


def task_from_accepted(thread, messages, complete, bot):
    if not complete:
        raise ValueError("Task lookup requires complete thread readback")
    # slack_bot.py:658 sends the current "REQUEST RECEIVED" wording; "ACCEPTED" is
    # the retained legacy wording. Either binds a task ID the same way, and any
    # duplicate or mixed-wording match within the thread stays ambiguous.
    replies = [message for message in messages if message.get("user") == bot and message.get("thread_ts") == thread]
    # The bot never edits its replies; an edited one may have gained or lost a receipt.
    if not all(unedited(message) for message in replies):
        raise ValueError("Edited or subtyped bot reply in the thread")
    # Only a reply whose leading header is the receipt counts; receipt text anywhere else does not.
    matches = [match.group(1) for message in replies
        if (match := RECEIPT_HEADER.fullmatch(reply_header(message.get("text", ""))))]
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
            "slack_deliveries", "status_receipts", "intake_auth_claims", "provider_usage")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


def _identities(items, key):
    return sorted({item[key] for item in items if isinstance(item, dict)
                   and isinstance(item.get(key), str) and item[key]})


def _typed_list(task, key):
    return task.get(key) if isinstance(task.get(key), list) else None


ATTEMPT_STATUSES = ("STARTED", "SUCCEEDED", "FAILED", "AMBIGUOUS")
DELIVERY_STATES = ("PENDING", "POSTING", "ACKNOWLEDGED", "DELIVERED", "AMBIGUOUS", "REFUSED", "SUPPRESSED", "SUPERSEDED")
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
# The exact top-level shape of the typed response-only readback; any other
# key, or a missing one, means the response is not the contract this
# collector understands and nothing is derived from it.
READBACK_KEYS = frozenset({
    "schema_version", "task_id", "engine_owner", "projection_version", "projection_digest", "source_event_id",
    "execution_mode", "terminal_classification", "terminal_reason_code", "task_created_at", "task_updated_at",
    "captured_at", "task_scope_counters", "snapshot", "bounds", "truncated", "absent", "complete",
    "assignments", "provider_attempts", "effects", "reconciliation_events", "slack_deliveries",
    "status_receipts", "intake_auth_claims", "provider_usage",
})
COUNTER_KEYS = ("thread_tasks", "assignments", "provider_attempts", "attempt_ordinals", "started_attempts",
                "status_receipts", "effect_reservations")


def _is_id(value):
    return isinstance(value, str) and bool(IDENTITY.fullmatch(value))


def _is_count(value):
    return type(value) is int and value >= 0


def _when(value):
    """Timezone-aware datetime from an ISO string, or None when unusable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# One attempt-history rule for the collector (selected tasks) and the in-image consistency command (every task).
from director_orchestrator.primary_scorecard_evidence import validate_attempt_history as _validate_attempts  # noqa: E402


def _validate_effects(effects):
    keys = set()
    for row in effects:
        if (not DIGEST.fullmatch(str(row.get("reservation_key", ""))) or not DIGEST.fullmatch(str(row.get("claim_key", "")))
                or not DIGEST.fullmatch(str(row.get("effect_key_digest", "")))
                or not DIGEST.fullmatch(str(row.get("effect_attempt_digest", "")))
                or not _is_id(row.get("writer_owner_id")) or not _is_count(row.get("fencing_generation"))
                or not _is_count(row.get("reserved_units")) or _when(row.get("reserved_at")) is None):
            return "malformed external-effect reservation identity"
        if row["reservation_key"] in keys:
            return "duplicate external-effect reservation"
        keys.add(row["reservation_key"])
        if (row.get("remote_identifier") is None) != (row.get("remote_system") is None):
            return "remote binding lacks its system or identifier"
        if row.get("remote_identifier") is not None and (not _is_id(row["remote_system"])
                                                          or not isinstance(row["remote_identifier"], str)
                                                          or not row["remote_identifier"].strip()
                                                          or _when(row.get("bound_at")) is None):
            return "malformed remote binding"
        if row.get("terminal_disposition") is not None and (
                not _is_id(row["terminal_disposition"]) or not DIGEST.fullmatch(str(row.get("terminal_evidence_digest", "")))
                or _when(row.get("terminal_recorded_at")) is None):
            return "terminal evidence lacks its digest or time"
        for field in ("execution_started_at", "bound_at", "terminal_recorded_at"):
            if row.get(field) is not None and _when(row[field]) is None:
                return "malformed external-effect timestamp"
    return None


# Notification-key families exactly as the delivery producer builds them
# (primary_slack_delivery.enqueue): the fixed terminal key, hold:<state digest>
# and approval:<request digest>:<projection digest>, each digest being one
# sha256-prefixed value. Generic identity bounds are not widened for these.
NOTIFICATION_KEY_FAMILIES = (
    ("terminal", re.compile(r"terminal\Z")),
    ("hold", re.compile(r"hold:sha256:[a-f0-9]{64}\Z")),
    ("approval", re.compile(r"approval:sha256:[a-f0-9]{64}:sha256:[a-f0-9]{64}\Z")),
)


def notification_key_family(value):
    """Return the producer family of a notification key, or None when malformed."""
    if not isinstance(value, str):
        return None
    for family, pattern in NOTIFICATION_KEY_FAMILIES:
        if pattern.fullmatch(value):
            return family
    return None


def _validate_deliveries_and_receipts(deliveries, receipts):
    if len({r.get("client_msg_id") for r in deliveries}) != len(deliveries):
        return "duplicate Slack delivery identity"
    if len({r.get("notification_key") for r in deliveries}) != len(deliveries):
        return "duplicate Slack notification key"
    for row in deliveries:
        if (not _is_id(row.get("client_msg_id")) or notification_key_family(row.get("notification_key")) is None
                or row.get("state") not in DELIVERY_STATES or not _is_count(row.get("readback_attempts"))
                or _when(row.get("created_at")) is None):
            return "malformed Slack delivery row"
        if row.get("owner_decision") not in (None, "approve", "reject"):
            return "malformed Slack delivery row"
        if (row.get("owner_decision") is not None) and notification_key_family(row["notification_key"]) != "approval":
            return "owner decision recorded on a non-approval notification"
        for field in ("claimed_at", "acknowledged_at", "delivered_at", "owner_decision_at"):
            if row.get(field) is not None and _when(row[field]) is None:
                return "malformed Slack delivery timestamp"
        if row.get("slack_ts") is not None and not TS.fullmatch(str(row["slack_ts"])):
            return "malformed Slack delivery timestamp"
    if len({r.get("receipt_id") for r in receipts}) != len(receipts):
        return "duplicate status receipt identity"
    for row in receipts:
        if not _is_id(row.get("receipt_id")) or not isinstance(row.get("source_event_id"), str) \
                or not row["source_event_id"] or _when(row.get("created_at")) is None:
            return "malformed status receipt row"
    return None


def _validate_usage(usage_rows, latest):
    """Usage joins exactly one valid attempt of the same provider, never twice."""
    seen = set()
    for row in usage_rows:
        digest_value = row.get("attempt_digest")
        owner = next((e for e in latest.values() if e["attempt_digest"] == digest_value), None)
        if (owner is None or digest_value in seen or row.get("provider_id") != owner["provider_id"]
                or not all(_is_count(row.get(k)) for k in ("actual_microusd", "observed_tokens",
                                                            "reserved_microusd", "token_limit"))):
            return "usage is orphaned, duplicated, provider-mismatched or non-integer"
        seen.add(digest_value)
    return None


def _has_nonfinite(value):
    """JSON payloads may carry NaN/Infinity tokens; no numeric field may be non-finite."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return value != value or value in (float("inf"), float("-inf"))
    if isinstance(value, dict):
        return any(_has_nonfinite(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_nonfinite(v) for v in value)
    return False


def _typed_readback_error(readback, task_id):
    """Strict JSON-shape validation through the existing typed readback model.

    ``QualificationEvidenceReadback`` (models.py) is validated in strict JSON
    mode so nested shapes, unknown keys, bool/string count coercion and naive
    or malformed timestamps are refused by the product model itself; the
    checks below add only what the model cannot express: this task's binding,
    the collector's schema/counter contract, non-finite numbers, ordered
    timezone-aware times, complete/truncated consistency, known section names
    and counter/row relationships. Diagnostics never echo the payload.
    """
    from pydantic import ValidationError
    from director_orchestrator.models import QualificationEvidenceReadback
    if not isinstance(readback, dict) or set(readback) != READBACK_KEYS:
        return "readback shape/task binding mismatch"
    if _has_nonfinite(readback):
        return "readback carries a non-finite number"
    try:
        typed = QualificationEvidenceReadback.model_validate_json(json.dumps(readback), strict=True)
    except (ValidationError, ValueError, TypeError) as error:
        if not isinstance(error, ValidationError):
            return "readback is not serializable JSON"
        # Locations only; values never surface in the observation.
        places = sorted({".".join(str(p) for p in item.get("loc", ())) for item in error.errors()})[:5]
        return "readback rejected by the typed model at " + (",".join(places) or "document")
    if typed.task_id != task_id or typed.schema_version != READBACK_SCHEMA:
        return "readback shape/task binding mismatch"
    if set(readback["task_scope_counters"]) != set(COUNTER_KEYS) \
            or not all(_is_count(readback["task_scope_counters"][k]) for k in COUNTER_KEYS):
        return "readback counter contract mismatch"
    for field in ("captured_at", "task_updated_at", "task_created_at"):
        if _when(readback.get(field)) is None:
            return "readback timestamp is missing, naive or malformed: " + field
    if not (_when(readback["task_created_at"]) <= _when(readback["task_updated_at"]) <= _when(readback["captured_at"])):
        return "readback times are not ordered created <= updated <= captured"
    for name in ("truncated", "absent"):
        values = readback[name]
        if len(set(values)) != len(values) or any(v not in SECTIONS and v != "response_bytes" for v in values):
            return "readback names an unknown or duplicate section under " + name
    if readback["complete"] != (not readback["truncated"]):
        return "readback completeness disagrees with its truncated sections"
    if any(readback[name] for name in readback["absent"] if name in SECTIONS):
        return "readback reports rows for an absent section"
    bounds = readback["bounds"]
    if not (0 <= float(bounds["elapsed_seconds"]) <= float(bounds["wall_seconds"])):
        return "readback elapsed time exceeds its declared bound"
    if any(len(readback[name]) > int(bounds["row_limit"]) for name in SECTIONS):
        return "readback section exceeds its declared row limit"
    return None


def _counter_consistency_error(readback):
    """A complete readback's task-scope counters must agree with its own rows."""
    if not readback["complete"]:
        return None
    counters = readback["task_scope_counters"]
    events = readback["provider_attempts"]
    expected = {"assignments": len(readback["assignments"]), "provider_attempts": len(events),
                "attempt_ordinals": len({(e.get("assignment_id"), e.get("attempt_ordinal")) for e in events}),
                "started_attempts": sum(1 for e in events if e.get("status") == "STARTED"),
                "status_receipts": len(readback["status_receipts"]),
                "effect_reservations": len(readback["effects"])}
    if any(counters[key] != value for key, value in expected.items()) or counters["thread_tasks"] < 1:
        return "readback counters disagree with its complete rows"
    return None


def _readback(task_id, task):
    """Accept the typed opt-in readback only when it is bound and internally consistent."""
    readback = task.get("qualification_evidence")
    if readback is None:
        return None, None, "absent", "opt-in readback not present in the task response"
    error = _typed_readback_error(readback, task_id)
    if error is not None:
        return None, None, "rejected", error
    latest, error = _validate_attempts(readback["assignments"], readback["provider_attempts"])
    if error is None:
        error = _validate_usage(readback["provider_usage"], latest)
    if error is None:
        error = _validate_effects(readback["effects"])
    if error is None:
        error = _validate_deliveries_and_receipts(readback["slack_deliveries"], readback["status_receipts"])
    if error is None:
        error = _counter_consistency_error(readback)
    if error is not None:
        return None, None, "rejected", error
    if not readback["complete"]:
        return readback, latest, "incomplete", "readback truncated: " + ",".join(map(str, readback.get("truncated", [])))
    return readback, latest, "collected", None


def intake_claim_matches(task, readback, input_ts):
    """The gateway attestation joined to its append-only claim, never the mutable projection label alone.

    True only when the task says slack_gateway_attested and the complete readback carries exactly one
    director_primary_intake_auth_claim row of that class for this task whose payload digest is main.py's
    digest of this very intake (approvals.verify_slack_intake_attestation) and whose idempotency digest is
    primary_default._request's over its source event, channel and thread. The claim's own time is a durable
    moment too: timezone-aware, no earlier than the correlated Slack input (input_ts; the gateway forwards a
    post it has seen), and no later than the task's creation (create_intake claims before the graph exists)
    or the readback's capture. Anything else is not attested.
    """
    from director_orchestrator.approvals import _canonical_digest
    intake = task.get("intake")
    claims = (readback or {}).get("intake_auth_claims")
    if (not isinstance(intake, dict) or not isinstance(claims, list) or len(claims) != 1
            or not isinstance(claims[0], dict) or task.get("intake_authentication") != "slack_gateway_attested"
            or claims[0].get("authentication_class") != "slack_gateway_attested"):
        return False
    expected = ("sha256:" + _canonical_digest(intake),
                "sha256:" + _canonical_digest({key: intake.get(key) for key in ("source_event_id", "channel_id",
                                                                                "thread_ts")}))
    observed = (claims[0].get("payload_digest"), claims[0].get("idempotency_key_digest"))
    claimed, created, captured = (_when(claims[0].get("created_at")), _when(readback.get("task_created_at")),
                                  _when(readback.get("captured_at")))
    try:
        posted = datetime.fromtimestamp(float(Decimal(str(input_ts))), timezone.utc) if input_ts else None
    except (ArithmeticError, ValueError, OverflowError):
        posted = None
    return (None not in (claimed, created, captured, posted) and posted <= claimed <= created <= captured
            and all(isinstance(value, str) and hmac.compare_digest(value.encode(), wanted.encode())
                    for value, wanted in zip(observed, expected)))


def _provider_effect_state(latest):
    """Aggregate the latest valid event of every attempt; any open uncertainty wins."""
    if not latest:
        return "ZERO_PROVIDER_ATTEMPT"
    statuses = {e["status"] for e in latest.values()}
    if "AMBIGUOUS" in statuses:
        return "PROVIDER_ATTEMPT_AMBIGUOUS"
    if "STARTED" in statuses:
        return "PROVIDER_ATTEMPT_STARTED_UNSETTLED"
    return "PROVIDER_RESULT_RECORDED"


def _effect_row_state(row):
    if row.get("terminal_disposition"):
        return "TERMINAL:" + str(row["terminal_disposition"])
    if row.get("execution_started_at") and not row.get("remote_identifier"):
        return "AMBIGUOUS"
    if row.get("remote_identifier"):
        return "BOUND_UNCONFIRMED"
    return "RESERVED_NO_EFFECT"


def _external_effect_state(effects):
    """Aggregate every reservation; one terminal sibling never hides another."""
    if not effects:
        return "NO_EXTERNAL_EFFECT_RESERVATION"
    states = {_effect_row_state(r) for r in effects}
    if "AMBIGUOUS" in states:
        return "EXTERNAL_EFFECT_AMBIGUOUS"
    if "BOUND_UNCONFIRMED" in states:
        return "EXTERNAL_EFFECT_BOUND_UNCONFIRMED"
    terminal = sorted(s.removeprefix("TERMINAL:") for s in states if s.startswith("TERMINAL:"))
    if "RESERVED_NO_EFFECT" in states and terminal:
        return "EXTERNAL_EFFECT_MIXED_RESERVED_AND_TERMINAL:" + ",".join(terminal)
    if terminal:
        return "EXTERNAL_EFFECT_TERMINAL:" + ",".join(terminal)
    return "EXTERNAL_EFFECT_RESERVED_NO_EFFECT"


def qualification_evidence(task_id, task, rows, rows_bound, input_message, final, *, baseline=None, receipt_id=None,
                           baseline_authority="caller-supplied-file", before_binding=None, after_binding=None):
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
    readback, latest, state, reason = _readback(task_id, task)
    collected = state == "collected"
    # Only a complete, validated readback yields derived identities, states or
    # amounts: the chain below never falls back to the ordinary TaskRecord
    # projection or the separate deliveries endpoint, because those are not
    # the validated single-snapshot join. An incomplete readback keeps its raw
    # diagnostics under the readback block, explicitly marked partial.
    latest = latest if collected else {}
    effects = readback["effects"] if collected else []
    deliveries = readback["slack_deliveries"] if collected else []
    receipts = readback["status_receipts"] if collected else []
    usage_rows = readback["provider_usage"] if collected else []
    # Ordinary, unjoined context from the task record and deliveries endpoint,
    # reported separately and never as qualification evidence.
    unjoined = {
        "assignment_sequences": sorted({int(o["assignment_sequence"]) for o in outcomes
                                        if isinstance(o, dict) and type(o.get("assignment_sequence")) is int}),
        "provider_result_digests": _identities(outcomes, "provider_result_digest"),
        "provider_routes": _identities(outcomes, "provider"),
        "delivery_ids": _identities(rows, "client_msg_id") if rows_bound else [],
        "note": "ordinary task-record/deliveries context; not the validated snapshot join",
    }
    terminal = [r for r in deliveries if r.get("notification_key") == "terminal"]
    delivery = terminal[0] if len(terminal) == 1 else {}
    delivery_states = {str(r.get("state")) for r in deliveries}
    settled = [e for e in latest.values() if e["status"] in ("SUCCEEDED", "FAILED")]
    provider_state = _provider_effect_state(latest) if collected else None
    external_state = _external_effect_state(effects) if collected else None
    if not collected:
        effect_state = None
    elif ("AMBIGUOUS" in delivery_states or provider_state == "PROVIDER_ATTEMPT_AMBIGUOUS"
            or external_state in ("EXTERNAL_EFFECT_AMBIGUOUS", "EXTERNAL_EFFECT_BOUND_UNCONFIRMED")):
        effect_state = "AMBIGUOUS_DELIVERY_EFFECT"
    elif not latest and not any(r.get("execution_started_at") for r in effects):
        effect_state = "ZERO_PROVIDER_EFFECT"
    elif provider_state == "PROVIDER_RESULT_RECORDED" and any(e["status"] == "SUCCEEDED" for e in latest.values()):
        effect_state = "PROVIDER_EFFECT_RECORDED"
    else:
        effect_state = None
    if usage_rows:
        # Validated in _readback: unique, joined to a valid attempt of the same
        # provider, exact non-negative integers. Amount families stay separate.
        measured_cost, cost_basis = sum(r["actual_microusd"] for r in usage_rows) / 1_000_000, "authenticated_usage_table"
        usage = {"authenticated_microusd": sum(r["actual_microusd"] for r in usage_rows),
                 "observed_tokens": sum(r["observed_tokens"] for r in usage_rows),
                 "reserved_budget_microusd": sum(r["reserved_microusd"] for r in usage_rows),
                 "attempt_digests": _identities(usage_rows, "attempt_digest"),
                 "invoice_charge": UNKNOWN, "local_estimate_usd": None}
    elif collected:
        # A top-level task cost is an unbound provider-reported figure: only a
        # complete, validated readback may carry it, as a finite non-negative
        # non-boolean number; it is never an authenticated accounting amount.
        cost = task.get("actual_cost_usd")
        measured_cost = cost if type(cost) in (int, float) and cost == cost and cost not in (float("inf"),) \
            and cost >= 0 else None
        cost_basis = "provider_reported" if measured_cost is not None else UNKNOWN
        usage = task.get("usage") if isinstance(task.get("usage"), dict) else None
    else:
        measured_cost, cost_basis, usage = None, UNKNOWN, None
    ordinals = sorted(latest)
    per_assignment = {}
    for assignment_id, ordinal in ordinals:
        per_assignment.setdefault(assignment_id, []).append(ordinal)
    retried = any(len(v) > 1 for v in per_assignment.values())
    # No producer records fallback linkage (every attempt is bound to its
    # assignment's immutable provider), so fallback is UNKNOWN, not inferred.
    fallback = None
    completion = max((str(e["recorded_at"]) for e in settled), default=None)
    counters = readback.get("task_scope_counters") if collected else None
    if collected and baseline is not None and receipt_id is not None:
        delta = status_receipt_delta(baseline, readback, receipt_id, baseline_authority=baseline_authority,
                                     before_binding=before_binding, after_binding=after_binding)
    elif baseline is not None:
        delta = {"proof": None, "observed_delta": None,
                 "reason": "after readback not collected or receipt identity missing"}
    else:
        delta = None
    return {
        "readback": {"state": state, "reason": reason,
                     "source": (API + "/v1/tasks/" + task_id + "?include_qualification_evidence=true")
                     if readback is not None else None,
                     "complete": readback.get("complete") if readback else None,
                     "truncated": list(readback.get("truncated", [])) if readback else [],
                     "absent": list(readback.get("absent", [])) if readback else [],
                     "snapshot": readback.get("snapshot") if readback else None,
                     "projection_digest": readback.get("projection_digest") if readback else None,
                     "engine_owner": readback.get("engine_owner") if readback else None,
                     "captured_at": str(readback["captured_at"]) if readback and readback.get("captured_at") else None,
                     "task_updated_at": str(readback["task_updated_at"]) if readback and readback.get("task_updated_at") else None,
                     "task_scope_counters": counters,
                     "status_receipt_rows": [{"receipt_id": r.get("receipt_id"), "created_at": str(r.get("created_at"))}
                                             for r in receipts],
                     "partial_diagnostics": ({"row_counts": {name: len(readback[name]) for name in SECTIONS},
                                              "not_proof": True} if state == "incomplete" else None)},
        "unjoined_context": unjoined,
        "chain": {
            "source_event_id_sha256": digest(encode(intake["source_event_id"]))
                if isinstance(intake.get("source_event_id"), str) and intake["source_event_id"] else None,
            "task_id": task_id,
            "assignment_ids": _identities(readback["assignments"], "assignment_id") if collected else [],
            "assignment_sequences": sorted({int(r["sequence"]) for r in readback["assignments"]}) if collected else [],
            "attempt_ids": sorted({e["attempt_digest"] for e in latest.values()}),
            "effect_ids": _identities(effects, "effect_key_digest"),
            "delivery_ids": _identities(deliveries, "client_msg_id"),
            "approval_delivery_keys": sorted(r["notification_key"] for r in deliveries
                                             if notification_key_family(r.get("notification_key")) == "approval"),
            "status_receipt_ids": _identities(receipts, "receipt_id"),
            "provider_result_digests": _identities(settled, "result_digest"),
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
            "provider_routes": sorted({e["provider_id"] for e in latest.values()}),
            "transport": ",".join(sorted({e["transport_id"] for e in latest.values()})) or None,
            "recorded_attempt_count": len(ordinals) if collected else None,
            "retry_observed": retried if latest else None,
            "attempt_history_count": len(history) if history is not None else None,
            "contact_attempt_count": len(contacts) if contacts is not None else None,
            "fallback_observed": fallback,
        },
        "effect_state": effect_state,
        "provider_effect_state": provider_state,
        "external_effect_state": external_state,
        "usage": {"actual_usage": usage, "actual_cost_usd": measured_cost, "cost_basis": cost_basis},
        "status_receipts": {"tasks_created": delta.get("tasks_created") if delta else None,
                            "assignments_created": delta.get("assignments_created") if delta else None,
                            "provider_attempts_created": delta.get("provider_attempts_created") if delta else None,
                            # Only an observed replay (proof) is a deduplication result; a
                            # durable row count alone is reported under durable_receipt_rows.
                            "deduplicated_receipts": delta.get("deduplicated_receipts") if delta and delta.get("proof")
                            else None,
                            "durable_receipt_rows": len(receipts) if collected else None,
                            "counter_scope": "task-and-thread-local; not a global zero-work claim",
                            "task_scope_counters": counters,
                            "baseline": delta,
                            "baseline_provenance": None,
                            "dependency": STATUS_RECEIPT_DEPENDENCY},
        "allocation": {"rolling_twenty": UNKNOWN, "deviation_from_target_points": None},
        "revision_binding": None,
        "terminal_message_ts": final.get("ts") if final else None,
    }


# Safe effective-configuration values: an explicit allowlist of switches and
# route/profile selectors whose values are never secrets. Anything outside it
# (tokens, secrets, DSNs, allowlists, identities, unknown keys) is excluded by
# construction, so the digest can bind value drift of these keys without ever
# hashing or emitting confidential material. It is a read-time binding of this
# bounded scope, not the native account/credential pin.
# The public configuration fields this collector resolves, with the exact
# semantics config.Settings applies to them (declared here so the resolution is
# reproducible without instantiating host-ambient Settings or reading .env):
#   bool  -> value.strip().lower() in {"1","true","yes","on"}, default False
#   str   -> value.strip(), declared default
#   profile -> value.strip().lower(), default DEFAULT_ALLOCATION_PROFILE, must
#            name an allocation profile (Settings rejects anything else)
# Secret-bearing or unknown keys are excluded by construction and never read.
CONFIGURATION_SCOPE = "supplied-public-configuration-resolved-with-declared-defaults/v1"
SAFE_BOOL_KEYS = (
    "ALLOW_PAID_RUNS",
    "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
    "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
    "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
)
SAFE_STRING_DEFAULTS = {"LANGGRAPH_PRIMARY_POSTGRES_SCHEMA": "director_primary_v1"}
SAFE_PROFILE_KEY = "INTAKE_ALLOCATION_PROFILE"
SAFE_CONFIGURATION_KEYS = SAFE_BOOL_KEYS + tuple(SAFE_STRING_DEFAULTS) + (SAFE_PROFILE_KEY,)
SCHEMA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


def resolve_safe_configuration(env):
    """Resolve the public fields exactly as Settings would, from a supplied mapping."""
    from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES, DEFAULT_ALLOCATION_PROFILE
    resolved, supplied = {}, []
    for key in SAFE_CONFIGURATION_KEYS:
        raw = env.get(key)
        if raw is not None:
            if not isinstance(raw, str) or len(raw) > 256 or any(ord(c) < 32 for c in raw):
                raise ValueError("Malformed configuration value for " + key)
            supplied.append(key)
        if key in SAFE_BOOL_KEYS:
            resolved[key] = False if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}
        elif key == SAFE_PROFILE_KEY:
            profile = DEFAULT_ALLOCATION_PROFILE if raw is None else raw.strip().lower()
            if profile not in ALLOCATION_PROFILES:
                raise ValueError("Unknown allocation profile")
            resolved[key] = profile
        else:
            value = SAFE_STRING_DEFAULTS[key] if raw is None else raw.strip()
            if not SCHEMA_NAME.fullmatch(value):
                raise ValueError("Malformed configuration value for " + key)
            resolved[key] = value
    return resolved, supplied


def safe_configuration_digest(env):
    """Digest of the resolved public fields; malformed or unknown profiles refuse."""
    resolved, supplied = resolve_safe_configuration(env)
    return digest(encode({"scope": CONFIGURATION_SCOPE, "resolved": resolved})), sorted(resolved), sorted(supplied)


def revision_binding(runtime, env):
    """Read-time revision identity plus the bounded public configuration binding.

    The digest covers the resolved public fields (declared defaults applied,
    Settings types and normalization), so a changed profile or switch changes
    it while an omitted field equal to its default does not. It is not the
    native account/credential pin and not an execution-time binding: no
    producer records the configuration in force when the task executed.
    """
    resolved, _ = resolve_safe_configuration(env)
    # The emitted public flags and the digest come from the same resolver, so
    # equivalent spellings (1/true/yes/on) and declared defaults agree.
    public = {"runtime_head": runtime.get("runtime_head"), "image_id": runtime.get("image_id"),
              "offline_configured": resolved["LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED"],
              "paid_configured": resolved["ALLOW_PAID_RUNS"]}
    configuration_digest, keys, supplied = safe_configuration_digest(env)
    return {**public, "configuration_digest": digest(encode({**public, "safe_configuration_digest": configuration_digest})),
            "configuration_scope": CONFIGURATION_SCOPE,
            "configuration_keys": keys,
            "supplied_keys": supplied,
            "execution_time_binding": UNKNOWN,
            "execution_time_reason": "no producer records the configuration in force at execution time"}


COUNTER_SCOPE_VERSION = "task-and-thread-local/v1"
SAME_PROCESS_AUTHORITY = "same-process-authenticated-reads"


def baseline_from_receipt(receipt, task_id):
    """Rebuild a readback-like baseline from a prior collector receipt's trial.

    The prior receipt must first be a schema-valid observation with a
    collected readback for this task; only then are the raw snapshot facts the
    collector recorded (counters, capture time, receipt rows, revision and
    configuration binding) reused. Validity is a consistency check, not
    authentication: a file-supplied baseline can never yield authoritative
    proof, only an explicitly limited observed delta.
    """
    try:
        validate_observations(receipt)
    except (jsonschema.ValidationError, TypeError, AttributeError):
        return None
    for trial in receipt.get("trials", []):
        evidence = trial.get("qualification_evidence") if isinstance(trial, dict) else None
        block = (evidence or {}).get("readback")
        if not isinstance(block, dict) or trial.get("task_id") != task_id or block.get("state") != "collected":
            continue
        return {"schema_version": READBACK_SCHEMA, "task_id": task_id, "engine_owner": block.get("engine_owner"),
                "complete": block.get("complete"), "task_scope_counters": block.get("task_scope_counters"),
                "task_updated_at": block.get("task_updated_at"), "captured_at": block.get("captured_at"),
                "source_event_id_sha256": (evidence.get("chain") or {}).get("source_event_id_sha256"),
                "status_receipts": block.get("status_receipt_rows") or [],
                "revision_binding": evidence.get("revision_binding")}
    return None


def status_receipt_delta(before, after, receipt_id, *, baseline_authority="caller-supplied-file",
                         before_binding=None, after_binding=None):
    """Prospective status-only evidence from two readbacks around one receipt.

    Both documents must be complete typed readbacks of the same task, source
    event and engine with the same counter scope version, the same runtime
    head/image and the same declared configuration scope/key set/digest, in
    order: before capture < receipt creation <= after capture, all
    timezone-aware. The receipt must join exactly once in the after readback
    and be absent from the baseline; no counter may decrease and exactly one
    durable receipt row is added. Anything missing, malformed, duplicate,
    foreign, stale, negative or truncated is refused as evidence.

    Consistency is not authentication. Only readbacks obtained by this same
    process through the authenticated GET (``baseline_authority`` ==
    SAME_PROCESS_AUTHORITY, as the signed synthetic test sequence does) yield
    ``proof``; a caller-supplied baseline file, however consistent, yields an
    explicitly limited ``observed_delta`` with ``proof`` UNKNOWN, because no
    existing mechanism authenticates a saved file and no signing framework is
    added here.
    """
    def refused(reason):
        return {"proof": None, "observed_delta": None, "reason": reason}

    def readback(document):
        if (not isinstance(document, dict) or document.get("schema_version") != READBACK_SCHEMA
                or document.get("complete") is not True or not isinstance(document.get("task_scope_counters"), dict)
                or set(document["task_scope_counters"]) != set(COUNTER_KEYS)
                or not all(_is_count(document["task_scope_counters"][k]) for k in COUNTER_KEYS)
                or not isinstance(document.get("status_receipts"), list)
                or any(not isinstance(r, dict) for r in document["status_receipts"])):
            return None
        return document
    first, second = readback(before), readback(after)
    if first is None or second is None:
        return refused("baseline or after readback missing, truncated, or without complete counters")
    if first.get("task_id") != second.get("task_id") or first.get("engine_owner") != second.get("engine_owner"):
        return refused("baseline and after readbacks describe different tasks or engines")
    before_source = first.get("source_event_id_sha256") or (
        digest(encode(first["source_event_id"])) if isinstance(first.get("source_event_id"), str) else None)
    after_source = digest(encode(second["source_event_id"])) if isinstance(second.get("source_event_id"), str) else None
    if before_source is None or after_source is None or before_source != after_source:
        return refused("baseline and after readbacks are bound to different or unknown source events")
    binding_fields = ("runtime_head", "image_id", "configuration_scope", "configuration_keys", "configuration_digest")
    if not isinstance(before_binding, dict) or not isinstance(after_binding, dict) or any(
            before_binding.get(f) is None or before_binding.get(f) != after_binding.get(f) for f in binding_fields):
        return refused("runtime head/image or configuration scope/key set/digest differ or are missing")
    updated_before, updated_after = _when(first.get("task_updated_at")), _when(second.get("task_updated_at"))
    if updated_before is None or updated_after is None or updated_before > updated_after:
        return refused("baseline is newer than the after readback or timestamps are unusable")
    matches = [r for r in second["status_receipts"] if r.get("receipt_id") == receipt_id]
    if len(matches) != 1 or any(r.get("receipt_id") == receipt_id for r in first["status_receipts"]):
        return refused("receipt does not join exactly once after, or was already present in the baseline")
    receipt = matches[0]
    captured_before, created, captured_after = (_when(first.get("captured_at")), _when(receipt.get("created_at")),
                                                _when(second.get("captured_at")))
    if captured_before is None or created is None or captured_after is None \
            or not (captured_before < created <= captured_after):
        return refused("required order before-capture < receipt-creation <= after-capture does not hold")
    a, b = first["task_scope_counters"], second["task_scope_counters"]
    delta = {k: b[k] - a[k] for k in COUNTER_KEYS}
    if any(v < 0 for v in delta.values()):
        return refused("negative counter delta")
    if delta["status_receipts"] != 1:
        return refused("durable receipt delta is not exactly one")
    observed = {"tasks_created": delta["thread_tasks"], "assignments_created": delta["assignments"],
                "provider_attempts_created": delta["attempt_ordinals"],
                "provider_attempt_events_added": delta["provider_attempts"],
                "started_attempts_created": delta["started_attempts"], "durable_receipts_added": delta["status_receipts"],
                "counter_scope": COUNTER_SCOPE_VERSION, "baseline_captured_at": str(first["captured_at"]),
                "receipt_created_at": str(receipt["created_at"]), "after_captured_at": str(second["captured_at"])}
    if baseline_authority != SAME_PROCESS_AUTHORITY:
        return {"proof": None, "observed_delta": observed, "authority": str(baseline_authority),
                "reason": "baseline is caller-supplied; no existing mechanism authenticates a saved file, "
                          "so the consistent observed delta is not authoritative proof"}
    return {"proof": True, "observed_delta": observed, "authority": SAME_PROCESS_AUTHORITY, "reason": None,
            "tasks_created": observed["tasks_created"], "assignments_created": observed["assignments_created"],
            "provider_attempts_created": observed["provider_attempts_created"],
            "started_attempts_created": observed["started_attempts_created"], "deduplicated_receipts": 1,
            "counter_scope": COUNTER_SCOPE_VERSION, "baseline_captured_at": observed["baseline_captured_at"],
            "receipt_created_at": observed["receipt_created_at"]}


def summarize(thread, task_id, task, deliveries, messages, complete, owner, bot, headers, channel,
              *, baseline=None, receipt_id=None, baseline_authority="caller-supplied-file",
              before_binding=None, after_binding=None):
    """Summarize observed source data; this cannot create scorecard observations."""
    task = task if isinstance(task, dict) else {}
    intake = task.get("intake") if isinstance(task.get("intake"), dict) else {}
    rows_ok = isinstance(deliveries, list) and all(isinstance(row, dict) for row in deliveries)
    rows = deliveries if rows_ok else []
    rows_bound = rows_ok and all(row.get("task_id") == task_id for row in rows)
    raw = lambda message: html.unescape(str(message.get("text", "")))
    clean = lambda text: re.sub(r"<@[A-Z0-9]+>", "", text).strip()  # slack_bot._clean_text
    messages = [m for m in messages if isinstance(m, dict) and isinstance(m.get("ts"), str)
        and TS.fullmatch(m["ts"]) and (m["ts"] == thread or m.get("thread_ts") == thread)]
    from director_orchestrator.models import canonical_slack_source_event_id
    # The gateway's own identity for the owner's message (slack_bot._forward and _forward_thread_reply:
    # the canonical `<channel>:<ts>`, else client_msg_id, event_ts or ts), computed by the same function.
    source_of = lambda m: (canonical_slack_source_event_id({"channel": channel, "ts": m.get("ts")})
        or m.get("client_msg_id") or m.get("event_ts") or m.get("ts"))
    inputs = [m for m in messages if isinstance(intake.get("source_event_id"), str)
        and intake["source_event_id"] and intake["source_event_id"] == source_of(m)
        and m.get("user") == owner and not m.get("bot_id")
        and unedited(m) and clean(str(m.get("text", ""))) == intake.get("text")]
    bound = (task.get("task_id") == task_id and intake.get("channel_id") == channel
        and intake.get("thread_ts") == thread and intake.get("user_id") == owner
        and headers.get("x-task-engine-owner") == "langgraph")
    input_message = inputs[0] if bound and len(inputs) == 1 else None
    bound = bool(bound and input_message)
    terminal_rows = [r for r in rows if r.get("notification_key") == "terminal"] if rows_bound else []
    ids = {r.get("client_msg_id") for r in terminal_rows if r.get("client_msg_id")}
    # Count additional bot final messages too, not just the first ledger match: any of the Director's own
    # terminal classifications (primary_slack_delivery.render_notification renders `<task> — <classification>`).
    # The producer (PrimarySlackDelivery.scan) keys a notification by family: 'approval:' for a pending prompt,
    # 'hold:' for an OWNER_DECISION_REQUIRED or RECONCILIATION_REQUIRED classification, and 'terminal' only for
    # every other one. So only those settled classifications can head a final reply, and a message that is a
    # known approval or hold delivery is never one, whatever its header says.
    from director_orchestrator.langgraph_primary import TerminalClassification
    held = {TerminalClassification.OWNER_DECISION_REQUIRED, TerminalClassification.RECONCILIATION_REQUIRED}
    final_pattern = re.compile(re.escape(task_id) + r"\s+—\s+(?:"
        + "|".join(re.escape(str(item)) for item in TerminalClassification if item not in held) + r")\b")
    not_final = {r.get("client_msg_id") for r in rows
                 if notification_key_family(r.get("notification_key")) in ("approval", "hold")} if rows_bound else set()
    finals = [m for m in messages if bot and m.get("user") == bot and m.get("client_msg_id") not in not_final and
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
    claim_readback, _, claim_state, _ = _readback(task_id, task)
    claim_attested = (claim_state == "collected" and input_message is not None
                      and intake_claim_matches(task, claim_readback, input_message.get("ts")))
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
        # Joined to the append-only intake claim in a complete readback, not read from the projection label.
        "task_api_gateway_attested": claim_attested,
        "projection_digest": headers.get("x-primary-projection-digest"),
        "slack_thread_complete_at_read": complete, "observed_final_message_count": len(finals),
        "terminal_reply_count": len(finals) if complete else None,
        # Slack keeps an edited message's ts but returns its new text, so an edited or
        # subtyped terminal reply cannot show the label or cost text it was posted with.
        "terminal_messages": [{"ts": m["ts"], "client_msg_id": m.get("client_msg_id"),
            "body_sha256": digest(encode(m)),
            "simulation_label_present": unedited(m) and LABEL in raw(m),
            "actual_cost_not_recorded_present": unedited(m) and "Actual USD cost: not recorded" in raw(m)}
            for m in finals],
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
        "qualification_evidence": qualification_evidence(task_id, task, rows, rows_bound, input_message, final,
                                                         baseline=baseline, receipt_id=receipt_id,
                                                         baseline_authority=baseline_authority,
                                                         before_binding=before_binding, after_binding=after_binding)}


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
        "predeclared": declared_before and isinstance(row.get("input_rubric_sha256"), str)
            and hmac.compare_digest(row["input_rubric_sha256"].encode(), str(rubric_digest).encode()),
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
    parser.add_argument("--status-baseline", type=Path, help="Prior collector receipt captured before a status mention")
    parser.add_argument("--status-receipt-id", help="Durable receipt_id of that status mention")
    args = parser.parse_args(argv)
    trials, records, collector, created = [], [], None, False
    baseline_receipt, baseline_provenance = None, None
    try:
        if not HEAD.fullmatch(args.expected_head):
            raise ValueError("Expected head must be a full lowercase SHA")
        trials = parse_trials(args.trial)
        rubric, rubric_digest = load_rubric(args.rubric)
        if (args.status_baseline is None) != (args.status_receipt_id is None):
            raise ValueError("A status baseline and its receipt identity are supplied together")
        if args.status_baseline is not None:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", args.status_receipt_id):
                raise ValueError("Malformed status receipt identity")
            with args.status_baseline.open("rb") as handle:
                raw_baseline = handle.read(MAX_BYTES + 1)
            if len(raw_baseline) > MAX_BYTES:
                raise ValueError("Status baseline exceeds byte bound")
            baseline_receipt = json.loads(raw_baseline)
            baseline_provenance = {"file_sha256": digest(raw_baseline),
                                   "authentication": "caller-supplied prior collector receipt; digest recorded, not self-authenticating"}
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
                baseline = baseline_from_receipt(baseline_receipt, task_id) if baseline_receipt is not None else None
                binding = revision_binding(before, env)
                row = summarize(thread, task_id, task, deliveries, messages, complete,
                    env["SLACK_OWNER_USER_ID"], bot, headers, env["SLACK_DIRECTOR_CHANNEL_ID"],
                    baseline=baseline if baseline is not None else ({} if baseline_receipt is not None else None),
                    receipt_id=args.status_receipt_id, baseline_authority="caller-supplied-file",
                    before_binding=(baseline or {}).get("revision_binding"), after_binding=binding)
                if baseline_receipt is not None:
                    row["qualification_evidence"]["status_receipts"]["baseline_provenance"] = baseline_provenance
                # The owner's matched source event commits the rubric bytes before
                # dispatch. A backdated or subsequently edited rubric cannot pass.
                markers = re.findall(r"\brubric-sha256:([a-f0-9]{64})\b", (task or {}).get("intake", {}).get("text", ""))
                row["input_rubric_sha256"] = markers[0] if row["task_record_bound"] and len(markers) == 1 else None
                row["rubric_evaluation"] = evaluate_rubric(row, rubric, rubric_digest)
                row["source_errors"] = errors + ([error] if error else [])
                # Read-time revision/configuration identity, not proof of the
                # image that executed the task (see limitations).
                row["qualification_evidence"]["revision_binding"] = binding
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
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-22 | 01:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:14 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 05:01 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 07:57 UTC | END]]
