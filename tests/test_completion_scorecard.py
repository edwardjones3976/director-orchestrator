# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:50 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:47 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:09 UTC | BEGIN]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]]
import dataclasses
import json
import hashlib
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from director_orchestrator import completion_scorecard as sc


def _load_trial_fixtures():
    # Loaded by path so this module never re-exports the collector's test classes.
    path = Path(__file__).resolve().parent / "test_collect_offline_slack_trials.py"
    spec = importlib.util.spec_from_file_location("scorecard_trial_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tf = _load_trial_fixtures()
SHA = "a" * 40
OTHER = "b" * 40
IMAGE = "sha256:" + "c" * 64
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
GATEWAY = "director-orchestrator-slackbot-release-879c4eb4a375-pre193-02"
OLD_GATEWAY = "director-orchestrator-slackbot-explicit-alias"
DEPLOYMENT = "edge-engine-director"
HEALTH = "http://127.0.0.1:18082/health"
API = "http://127.0.0.1:18082"
CHANNEL_FIXTURE = "CFIXTURE12"
OWNER, BOT = tf.OWNER, tf.BOT
PIN = "e" * 64
BINDINGS_JSON = '{"fixture":1}'
DIRECTOR_ID, GATEWAY_ID, N8N_ID, N8N_DB_ID = "1" * 64, "2" * 64, "3" * 64, "5" * 64
SECRETS = ("fixture-secret-bot", "fixture-director-secret", "fixture-app-token")
REAL_SLACK_THREADS = sc._SlackThreads
PAID_TASK, APPROVAL_TASK = "DIR-LG-FA1D", "DIR-LG-AA01"
STATUS_TS = f"{int(datetime(2026, 9, 12, 9, 59, 1, tzinfo=timezone.utc).timestamp())}.000100"


def compose_labels(service, project=DEPLOYMENT):
    return {"com.docker.compose.project": project, "com.docker.compose.service": service}


def containers():
    return [{"Id": DIRECTOR_ID, "Name": "/edge-engine-director-orchestrator-1", "Image": IMAGE,
             "Created": "2026-09-12T09:00:00.123456789Z",
             "State": {"Running": True, "StartedAt": "2026-09-12T09:30:00.000000001Z"},
             "Config": {"Labels": {"org.director.candidate.commit": SHA, "org.director.candidate.tree": "d" * 40,
                                   **compose_labels("orchestrator")},
                        "Env": [f"SLACK_DIRECTOR_CHANNEL_ID={CHANNEL_FIXTURE}", "SLACK_BOT_TOKEN=fixture-secret-bot",
                                "ORCHESTRATOR_SHARED_SECRET=fixture-director-secret", f"SLACK_OWNER_USER_ID={OWNER}",
                                f"LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256={PIN}",
                                f"LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON={BINDINGS_JSON}"]},
             "NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18082"}]},
                                 "Networks": {"edge-engine-director_default": {
                                     "NetworkID": "a" * 64, "Aliases": ["orchestrator"],
                                     "DNSNames": ["edge-engine-director-orchestrator-1", "orchestrator"]}}}}]


def health():
    return {"environment": "local", "paid_runs_enabled": True, "langgraph_primary": {
        "new_intake_owner": "langgraph", "legacy_new_intake_disabled": True,
        "intake_fencing_generation": 83, "ambiguous_effect_count": 0,
        "historical_parallel_plan_hold_count": 0, "reconciliation_worker_ready": True,
        "reconciliation_hold_reason": None,
        "primary_runtime_active": True, "effect_executor_ready": True,
        "provider_activation": {"combined_live_ready": True}},
        "usage_monitor": {"daily_accounting": {"grok": {"reported_cost_usd": 0.1, "reserved_cost_usd": 0.2,
                                                         "daily_budget_usd": 5.0}}}}


def runtime_fixture(**changes):
    values = dict(project=DEPLOYMENT, container_id=DIRECTOR_ID, image_id=IMAGE, runtime_sha=SHA,
                  created_at=datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc),
                  started_at=datetime(2026, 9, 12, 9, 30, tzinfo=timezone.utc), environment="local",
                  health_url=HEALTH, api_base=API, health=health(),
                  env=dict(entry.split("=", 1) for entry in containers()[0]["Config"]["Env"]))
    values.update(changes)
    return sc._Runtime(**values)


def row(report, check):
    return report["checks"][int(check) - 1]


def ts(offset):
    return f"{1789210000 + offset}.100000"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def flatted(value):
    """Minimal n8n/flatted encoder: containers reference strings and children by table index."""
    table = []

    def add(item):
        index = len(table)
        table.append(None)
        if isinstance(item, dict):
            table[index] = {key: str(add(child)) if isinstance(child, (dict, list, str)) else child
                            for key, child in item.items()}
        elif isinstance(item, list):
            table[index] = [str(add(child)) if isinstance(child, (dict, list, str)) else child for child in item]
        else:
            table[index] = item
        return index

    add(value)
    return json.dumps(table)


def status_fixture(ts_value=STATUS_TS, channel=CHANNEL_FIXTURE, ok=True):
    run = {"resultData": {"runData": {"Post Status": [{"data": {"main": [[{"json": {
        "ok": ok, "channel": channel, "ts": ts_value, "message": {"text": "*STATUS — readiness changed*"}}}]]}}]}}}
    return {"workflows": 1, "execution": {"id": "4411", "status": "success", "finished": True, "mode": "trigger",
                                          "workflow_active": True,
                                          "startedAt": "2026-09-12T09:59:00.000+00:00",
                                          "stoppedAt": "2026-09-12T09:59:02.000+00:00", "data": flatted(run)}}


def adoption_fixture(**changes):
    value = {"repository": sc.REPOSITORY, "pull_request_number": 260, "pull_request_author": "author-login",
             "independent_reviewer": "reviewer-login", "security_reviewer": "security-login",
             "reviewed_source_commit": SHA, "reviewed_source_tree": "d" * 40, "adoption_digest": "f" * 64}
    value.update(changes)
    return value


def consistency_fixture():
    counts = {key: 0 for key in sc._FAIL_COUNTS + sc._INFLIGHT_COUNTS}
    counts["graph_task_count"] = 12
    spend = {"task_id": PAID_TASK, "attempt_digest": "sha256:" + "1" * 64, "provider_id": "claude",
             "project_id": "fixture-project", "budget_day": "2026-09-12", "reserved_microusd": 500000,
             "actual_microusd": 300000, "request_correlated": True}
    budget = {"task_id": PAID_TASK, "project_id": "fixture-project", "day": "2026-09-12",
              "daily_limit_microusd": 5000000, "day_held_microusd": 500000}
    return {"version": "director-primary-consistency/v1",
            "generation": {"manifest_sha256": PIN, "acceptance_mode": "owner-local-evidence-v1", "candidate_commit": SHA,
                           "candidate_tree": "d" * 40, "adoption": adoption_fixture()},
            "captured_at": "2026-09-12T09:59:59+00:00", "intake_fencing_generation": 83, "counts": counts,
            "tasks": {PAID_TASK: {"usage_tables_present": True, "spend": [spend], "budgets": [budget],
                                  "approval_receipts": []},
                      APPROVAL_TASK: {"usage_tables_present": True, "spend": [], "budgets": [],
                                      "approval_receipts": [approval_receipt()]}}}


def approval_receipt(recorded_at=None):
    return {"command_id_sha256": hashlib.sha256(b"owner-command-1").hexdigest(), "command_digest": "sha256:" + "6" * 64,
            "resulting_projection_digest": "sha256:" + "7" * 64, "recorded_at": recorded_at or iso(1789210000 + 30010)}


def captures_fixture():
    surface = {"stage": "LIVE_OK", "evidence_digest": "a" * 64, "observed_at": "2026-09-12T09:30:00+00:00",
               "expires_at": "2026-09-12T12:00:00+00:00", "capture_verified": True}
    return {"version": "director-activation-captures/v1", "generation_manifest_sha256": PIN,
            "provider_bindings_sha256": hashlib.sha256(BINDINGS_JSON.encode()).hexdigest(),
            "observed_at": "2026-09-12T09:59:59+00:00",
            "lanes": {lane: {"binding_digest": "b" * 64, "product": product,
                             "surfaces": {"app": dict(surface), "runtime_adapter": dict(surface)}}
                      for lane, product in (("openai", "openai-responses"), ("claude", "anthropic-claude"),
                                            ("grok", "xai-grok-responses"))}}


def task_entry(task_id, offset, *, offline=True, text="audit trial", answer=None, final_offset=30):
    """One task's API record, deliveries and Slack thread, re-keyed from the collector's fixtures."""
    task, deliveries, messages, _ = tf.sample()
    readback = tf.readback_fixture()
    if offline:
        readback.update(assignments=[], provider_attempts=[], provider_usage=[])
        tf.recount(readback)
    else:
        task["assignment_outcomes"] = [{"run_id": "provider-run-1", "result": "provider answer", "provider": "claude"}]
    task["assignment_outcomes"][0]["provider_result_digest"] = "a" * 64
    task.update(status="DONE", qualification_evidence=readback)
    task["intake"]["text"] = text
    messages[0]["text"] = f"<@{BOT}> {text}"
    if answer is None:
        messages[-1]["text"] += "\nActual USD cost: not recorded"
    else:
        messages[-1]["text"] = tf.TASK_ID + " — SUCCEEDED\n" + answer
    messages.insert(1, {"ts": "1789210001.100000", "thread_ts": tf.THREAD, "user": BOT,
                        "text": f"*{tf.TASK_ID} — REQUEST RECEIVED*"})
    blob = json.dumps({"task": task, "deliveries": deliveries, "messages": messages})
    pairs = ((tf.TASK_ID, task_id), (tf.THREAD, ts(offset)), ("1789210001.100000", ts(offset + 1)),
             (tf.FINAL_TS, ts(offset + final_offset)), ('"CFIXTURE"', f'"{CHANNEL_FIXTURE}"'))
    for index, (old, _) in enumerate(pairs):
        blob = blob.replace(old, f"@@{index}@@")
    for index, (_, new) in enumerate(pairs):
        blob = blob.replace(f"@@{index}@@", new)
    return json.loads(blob)


def approval_entry(decided=None):
    entry = task_entry(APPROVAL_TASK, 30000, offline=False, text="approve then run")
    readback = entry["task"]["qualification_evidence"]
    readback["task_created_at"] = "2026-09-12T09:00:30+00:00"
    prompt = ts(30005)
    projection = "sha256:" + "9" * 64
    delivery = {"notification_key": "approval:sha256:" + "8" * 64 + ":" + projection, "projection_digest": projection,
                "client_msg_id": "approval-client-1", "state": "DELIVERED", "created_at": "2026-09-12T09:20:00+00:00",
                "claimed_at": "2026-09-12T10:00:03+00:00", "acknowledged_at": "2026-09-12T10:00:04+00:00",
                "delivered_at": "2026-09-12T10:00:05+00:00", "slack_ts": prompt, "readback_attempts": 1,
                "last_error": None, "owner_decision": "approve",
                "owner_decision_at": decided or iso(1789210000 + 30010), "owner_command_id": "owner-command-1"}
    readback["slack_deliveries"].append(delivery)
    entry["deliveries"].append({"task_id": APPROVAL_TASK, **{key: value for key, value in delivery.items()
                                                              if key not in ("created_at", "last_error")}})
    entry["messages"].insert(2, {"ts": prompt, "thread_ts": ts(30000), "user": BOT,
                                 "client_msg_id": "approval-client-1", "text": "*Approval required*"})
    return entry


def workflow_fixture():
    return {"active": True, "active_version": "active-version", "stored_version": "active-version", "http_node_count": 1,
            "supported_request": True, "static_request": True, "supported_webhook": True, "direct_connection": True}


class World:
    """One deployment's Docker, Director, n8n and Slack sources; unrouted reads are unavailable."""

    def __init__(self, test=None):
        self._folder = tempfile.TemporaryDirectory(prefix="scorecard-world-")
        if test is not None:
            test.addCleanup(self._folder.cleanup)
        self.root = Path(self._folder.name)
        self.rows = containers() + [item for item in topology() if item["Id"] in (N8N_ID, N8N_DB_ID)]
        self.rows.append({**topology()[1], "Created": "2026-09-12T09:00:00Z"})  # the gateway, created with the Director
        self.commit_tree = "d" * 40
        self.payload = health()
        self.consistency = consistency_fixture()
        self.captures = captures_fixture()
        self.status = status_fixture()
        self.ps_override = None
        self.calls, self.exec_timeouts, self.consistency_tasks = [], [], None
        self.threads, self.tasks, self.deliveries, self.bindings = {}, {}, {}, {}
        self.failing, self.slack_down = set(), False
        self.threads[STATUS_TS] = [{"ts": STATUS_TS, "user": BOT, "bot_id": "BBOT123",
                                    "text": "*STATUS — readiness changed*\n• `reachable`: unset → true"}]
        self.history_extra, self.history_hidden = [], set()
        for offset in (100, 150, 200):
            self.threads[ts(offset)] = [
                {"ts": ts(offset), "user": OWNER, "client_msg_id": f"drill-{offset}", "text": f"<@{BOT}> drill"},
                {"ts": ts(offset + 1), "thread_ts": ts(offset), "user": BOT,
                 "text": "*DIRECTOR — BLOCKED*\nThe Director did not accept the request."}]
        self.bindings[ts(100)] = (404, None)
        self.bindings[ts(150)] = (404, None)
        self.add(task_entry("DIR-LG-5E", 200), thread=False)
        self.bindings[ts(200)] = (200, "DIR-LG-5E")
        duplicate = self.add(task_entry("DIR-LG-D0", 300))
        duplicate["messages"].append({"ts": ts(302), "thread_ts": ts(300), "user": BOT, "text": "*DIR-LG-D0 — REQUEST RECEIVED*"})
        self.bindings[ts(300)] = (200, "DIR-LG-D0")
        ambiguous = self.add(task_entry("DIR-LG-A0", 400))
        for delivery in ambiguous["deliveries"] + ambiguous["task"]["qualification_evidence"]["slack_deliveries"]:
            delivery["acknowledged_at"] = None
        self.bindings[ts(400)] = (200, "DIR-LG-A0")
        rubric, digest = tf.rubric_fixture()
        self.rubric_path = self.root / "rubric.json"
        self.rubric_path.write_bytes(tf.collector_module.encode(rubric))
        self.trials = []
        for number in range(1, 11):
            self.add(task_entry(f"DIR-LG-7A{number:02X}", 1000 * number, text="audit trial rubric-sha256:" + digest))
            self.trials.append(ts(1000 * number))
        paid_rubric = {"schema": "director-paid-answer-rubric/v1", "rubric_id": "fixture-paid",
                       "declared_at": "2020-01-01T00:00:00+00:00", "expected_answer_substrings": ["answer is 42"]}
        raw = json.dumps(paid_rubric).encode()
        self.paid_rubric_path = self.root / "paid-rubric.json"
        self.paid_rubric_path.write_bytes(raw)
        self.paid_text = "What is the answer? rubric-sha256:" + hashlib.sha256(raw).hexdigest()
        self.add(task_entry(PAID_TASK, 20000, offline=False, text=self.paid_text, answer="The audit answer is 42"))
        self.add(approval_entry())

    def add(self, entry, thread=True):
        task_id = entry["task"]["task_id"]
        self.tasks[task_id], self.deliveries[task_id] = entry["task"], entry["deliveries"]
        if thread:
            self.threads[entry["task"]["intake"]["thread_ts"]] = entry["messages"]
        return entry

    def reply(self, value):
        if value is None:
            raise ValueError("fixture source unavailable")
        if isinstance(value, Exception):
            raise value
        return json.dumps(value).encode()

    def run(self, args, timeout=15):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["docker", "ps"]:
            wanted = dict(item.split("=", 1)[1].split("=", 1) for item in args if item.startswith("label="))
            if wanted and wanted.get("com.docker.compose.project") != DEPLOYMENT:
                raise AssertionError("docker ps was not filtered to the selected deployment")
            if self.ps_override is not None:
                return "\n".join(self.ps_override).encode()
            rows = [item for item in self.rows if item.get("State", {}).get("Running")
                    and all(sc._labels(item).get(key) == value for key, value in wanted.items())]
            return "\n".join(item["Id"] for item in rows).encode()
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([item for item in self.rows
                               if item["Id"] in args[2:] or item.get("Name", "").lstrip("/") in args[2:]]).encode()
        if args[:2] == ["docker", "exec"] and args[2] == DIRECTOR_ID and args[3:6] == ["python", "-m", "director_orchestrator.primary_generation"]:
            self.exec_timeouts.append(timeout)
            if args[6] == "consistency":
                self.consistency_tasks = [args[index + 1] for index in range(7, len(args), 2)]
                return self.reply(self.consistency)
            if args[6] == "activation-captures":
                return self.reply(self.captures)
        if args[:2] == ["docker", "exec"] and args[2] == N8N_DB_ID and args[-1] == sc._STATUS_EXECUTION:
            return self.reply(self.status)
        if args[:2] == ["gh", "api"] and args[2] == f"repos/{sc.REPOSITORY}/git/commits/{SHA}":
            return self.reply({"tree": self.commit_tree})
        raise ValueError("unrouted fixture command")

    def handle(self, request):
        if request.method != "GET":
            raise AssertionError("scorecard sources are GET-only")
        if self.slack_down:
            raise httpx.ConnectError("fixture transport down", request=request)
        url = request.url
        if url.host == "slack.com":
            if request.headers["Authorization"] != "Bearer fixture-secret-bot":
                raise AssertionError("Slack read used the wrong credential")
            if url.path == "/api/auth.test":
                return httpx.Response(200, json={"ok": True, "user_id": BOT, "bot_id": "BBOT123"})
            if url.params["channel"] != CHANNEL_FIXTURE:
                raise AssertionError("another channel was read")
            if url.path == "/api/conversations.history":
                if "history" in self.failing:
                    raise httpx.ConnectError("fixture transport failure", request=request)
                low, high = Decimal(url.params["oldest"]), Decimal(url.params["latest"])
                top = [message for key, messages in self.threads.items() for message in messages
                       if message["ts"] == key and key not in self.history_hidden]
                return httpx.Response(200, json={"ok": True, "has_more": False, "messages": [
                    message for message in top + self.history_extra if low <= Decimal(message["ts"]) <= high]})
            thread = url.params["ts"]
            if thread in self.failing:
                raise httpx.ConnectError("fixture transport failure", request=request)
            if thread not in self.threads:
                return httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
            return httpx.Response(200, json={"ok": True, "messages": self.threads[thread], "has_more": False})
        if not str(url).startswith(API + "/") or request.headers["Authorization"] != "Bearer fixture-director-secret":
            raise AssertionError("Director read left the selected deployment or used the wrong credential")
        parts = url.path.split("/")
        if url.path == "/v1/slack-thread-bindings":
            thread = url.params["thread_ts"]
            if thread in self.failing:
                raise httpx.ConnectError("fixture transport failure", request=request)
            status, task_id = self.bindings.get(thread, (404, None))
            body = ({"channel_id": url.params["channel_id"], "thread_ts": thread, "task_id": task_id}
                    if status == 200 else {"detail": "fixture"})
            return httpx.Response(status, json=body)
        if url.path.startswith("/v1/tasks/") and parts[3] in self.tasks:
            return httpx.Response(200, json=self.tasks[parts[3]], headers={
                "x-task-engine-owner": "langgraph", "x-primary-projection-digest": "fixture-digest"})
        if url.path.endswith("/deliveries") and parts[4] in self.deliveries:
            return httpx.Response(200, json=self.deliveries[parts[4]])
        return httpx.Response(404, json={"detail": "fixture"})

    def slack(self, runtime, channel):
        return REAL_SLACK_THREADS(runtime, channel, client=httpx.Client(
            transport=httpx.MockTransport(self.handle), follow_redirects=False, trust_env=False))

    def observe(self, only_deployment=False, **options):
        selected = dict(deployment=DEPLOYMENT)
        if not only_deployment:
            selected.update(channel=CHANNEL_FIXTURE, approval_task=APPROVAL_TASK, trials=list(self.trials),
                            gateway=GATEWAY, topology=False,
                            rubric_path=self.rubric_path, paid_thread=ts(20000), paid_rubric_path=self.paid_rubric_path,
                            drill_threads={"refusal-4xx": ts(100), "reserved-5xx": ts(200), "transport-failure": ts(150),
                                           "duplicate-input": ts(300), "ambiguous-post": ts(400)})
        selected.update(options)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = HEALTH
        response.read.return_value = json.dumps(self.payload).encode()
        with patch.object(sc, "_run_bytes", side_effect=self.run), patch.object(sc, "build_opener") as opener, \
                patch.object(sc, "_SlackThreads", side_effect=self.slack):
            opener.return_value.open.return_value = response
            observations = sc.collect(SHA, now=NOW, **selected)
            self.opened = [call.args[0] for call in opener.return_value.open.call_args_list]
        return observations

    def report(self, **options):
        return sc.score(SHA, self.observe(**options), now=NOW, runtime_sha=SHA, image_id=IMAGE)

    def state(self, check, **options):
        return row(self.report(**options), check)["status"]


def runtime_observations(payload=None, rows=None):
    # Substitute external I/O, not the score's trust check or predicates.
    world = World()
    world.payload = payload if payload is not None else health()
    world.consistency = world.captures = None
    if rows is not None:
        world.rows = rows
    try:
        return world.observe(only_deployment=True)
    finally:
        world._folder.cleanup()


def run(mode="offline", number=1):
    return {"run_id": str(number), "mode": mode, "actual_owner_event": True,
            "correctness_verified": True, "task_count": 1, "terminal_reply_count": 1,
            "wall_seconds": 40.0, "owner_pause_seconds": 10.0, "machine_seconds": 30.0,
            "actual_cost_usd": 0 if mode == "offline" else 0.4,
            "ambiguous_effects": 0, "delivery_ambiguous": False,
            "operational_provider_attempts": 0 if mode == "offline" else 1,
            "simulation_label": "SIMULATION — NO PROVIDER INTERACTION",
            "runtime_sha": SHA, "image_id": IMAGE,
            "cost_basis": "simulation_zero_calls" if mode == "offline" else "correlated_actual_charges"}


def qualify(records, mode="offline"):
    return sc.evaluate_run_records(records, mode=mode, runtime_sha=SHA, image_id=IMAGE)[0]


NETWORK = "edge-engine-director_default"


def topology():
    api = containers()[0]
    api["Config"].update(Cmd=["uvicorn", "director_orchestrator.main:app"], Env=["SLACK_APP_TOKEN=fixture-app-token"])
    gateway = {"Id": GATEWAY_ID, "Image": IMAGE, "Name": "/" + GATEWAY, "State": {"Running": True},
               "Config": {"Cmd": ["python", "-m", "director_orchestrator.slack_bot"], "Labels": compose_labels("slackbot"),
                          "Env": ["SLACK_APP_TOKEN=fixture-app-token", "SLACK_BOT_TOKEN=fixture-secret-bot",
                                  f"SLACK_DIRECTOR_CHANNEL_ID={CHANNEL_FIXTURE}", "N8N_DIRECTOR_WEBHOOK_URL=http://n8n:5678/webhook/director-intake"]},
               "NetworkSettings": {"Networks": {NETWORK: {"NetworkID": "a" * 64, "Aliases": ["slackbot"],
                                                          "DNSNames": ["edge-engine-director-slackbot-1", "slackbot"]}}}}
    n8n = {"Id": N8N_ID, "Name": "/edge-engine-director-n8n-1", "State": {"Running": True},
           "NetworkSettings": {"Ports": {"5678/tcp": [{"HostIp": "127.0.0.1", "HostPort": "45678"}]},
                               "Networks": {NETWORK: {"NetworkID": "a" * 64, "Aliases": ["n8n"],
                                                      "DNSNames": ["edge-engine-director-n8n-1", "n8n"]}}},
           "Config": {"Labels": compose_labels("n8n")}}
    n8n_db = {"Id": N8N_DB_ID, "Name": "/edge-engine-director-n8n-db-1", "State": {"Running": True},
              "Config": {"Labels": compose_labels("n8n-db")}}
    return [api, gateway, n8n, n8n_db]


def collect_topology(rows, host=None, upstream="local", workflow=None, gateway=GATEWAY, later_rows=None,
                     channel=CHANNEL_FIXTURE):
    host = {"inspected": 5, "unreadable": 0, "gateway_process_count": 0} if host is None else host
    workflow = workflow_fixture() if workflow is None else workflow
    listings = [rows, rows if later_rows is None else later_rows]
    def process(args, timeout=15):
        if args[:2] == ["docker", "ps"]:
            return "\n".join(row["Id"] for row in listings.pop(0)).encode()
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(rows).encode()
        if args[:2] == ["docker", "exec"]:
            if args[2] == N8N_DB_ID:
                return json.dumps(workflow).encode()
            if args[2] == N8N_ID:
                return json.dumps({"environment": upstream}).encode()
            raise ValueError("unrouted container")
        if isinstance(host, Exception):
            raise host
        return json.dumps(host).encode()
    with patch.object(sc, "_run_bytes", side_effect=process):
        observations = sc._topology_observations(NOW, runtime_fixture(), gateway, channel)
    return sc.score(SHA, observations, now=NOW)


def native_security_fixture(folder):
    scan_id = "12345678-1234-1234-1234-123456789abc"
    root = Path(folder) / "native-scan"
    root.mkdir()
    documents = {"coverage.json": {"scanId": scan_id, "completeness": "complete", "deferred": [],
                                   "excludePaths": [], "explicitExclusions": [],
                                   "surfaces": [{"label": "src/thing.py", "disposition": "no_issue_found"}]},
                 "findings.json": {"scanId": scan_id, "findings": []}}
    artifacts = []
    for name, value in documents.items():
        raw = json.dumps(value).encode()
        (root / name).write_bytes(raw)
        artifacts.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    sealed = (NOW - timedelta(minutes=30)).isoformat()
    manifest = {"documentType": "codex-security.scan-manifest", "scan": {
        "id": scan_id, "status": "completed", "sealedAt": sealed, "completedAt": sealed,
        "target": {"kind": "git_diff", "headRevision": SHA, "baseRevision": OTHER},
        "artifacts": artifacts, "preservedSources": {}, "scope": {"limitations": ["Source review only"]}}}
    checkpoint = {"scanId": scan_id, "findings": [], "coverage": {"completeness": "complete"}}
    (root / "checkpoint.json").write_text(json.dumps(checkpoint, indent=2))
    manifest["scan"]["preservedSources"]["checkpoint.json"] = hashlib.sha256(
        json.dumps(checkpoint, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    raw = json.dumps(manifest).encode()
    (root / "scan-manifest.json").write_bytes(raw)
    index = Path(folder) / "native-workbench.sqlite3"
    with closing(sqlite3.connect(index)) as db, db:
        db.execute("CREATE TABLE scans(id TEXT,status TEXT,scan_dir TEXT,target_path TEXT,target_revision TEXT,mode TEXT,diff_base_revision TEXT,diff_head_revision TEXT,seal_manifest_digest TEXT,completed_at TEXT)")
        db.execute("INSERT INTO scans VALUES(?,?,?,?,?,?,?,?,?,?)", (scan_id, "complete", str(root), str(root), SHA,
                   "diff", OTHER, SHA, "sha256:" + hashlib.sha256(raw).hexdigest(), sealed))
    return scan_id, index, root


def ledger_fixture(path, first_bounds=None):
    """Twenty real prospective admissions with known, in-bound reported usage."""
    from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
    reference = {"reference": "fixture-only", "sha256": "d" * 64}
    route = {"route_id": "openai-codex:fixture-route", "product_id": "openai-codex", "actor_id": "fixture-actor",
             "provider_identity": "fixture-provider", "model_family": "fixture-family", "eligible": True,
             "roles": ["implementation"], "enrollment_evidence": reference}
    manifest = {"authorization": reference, "policy_authorization": reference, "operator": "fixture operator",
                "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                "policy": {"kind": "product", "targets": {"openai-codex": 100}},
                "history_mode": "prospective", "history_evidence": reference,
                "prior_history_unknown": True, "quiescent_cutover_verified": True,
                "all_future_assignments_required": True, "routes": [route]}
    ledger = EngineeringWorkloadLedger(path)
    ledger.bootstrap(manifest)
    for number in range(20):
        bounds = {"timeout_seconds": 60, "call_limit": 1, "turn_limit": 1, "output_bytes_limit": 1000, "usd_limit": 1,
                  "hard_usd_cap_verified": True, "usd_cap_evidence": reference}
        if number == 0 and first_bounds is not None:
            bounds = first_bounds(reference)
        assignment_id = f"fixture-{number}"
        ledger.reserve({"assignment_id": assignment_id, "task_id": f"task-{number}", "scope": "test only",
                        "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                        "route_id": "openai-codex:fixture-route",
                        "packet_evidence": {"reference": "fixture-only", "sha256": f"{number:064x}"}, "bounds": bounds})
        ledger.begin_attempt(assignment_id, transport="local_cli")
        ledger.record_outcome(assignment_id, 1, {"outcome": "succeeded", "evidence": reference,
                                                  "usage": {"usd": 0.5, "turns": 1, "output_bytes": 500}})
    return ledger


def gh_runs(jobs_by_run, paths=None, compare=None, size=100, total=None):
    """Runs come newest first; every run is ci.yml unless ``paths`` names another workflow."""
    def query(args, timeout=15):
        if args[0] != "gh":
            raise ValueError("offline")
        if "/compare/" in args[2]:
            return {"status": (compare or {}).get(args[2].split("/compare/")[1], "ahead")}
        if "/jobs?" in args[-1]:
            jobs = jobs_by_run[int(args[-1].split("/runs/")[1].split("/")[0])]
            return {"total_count": len(jobs), "jobs": jobs}
        if "head_sha=" + SHA not in args[-1]:
            raise AssertionError("CI query is not bound to the exact head")
        runs = [{"id": run_id, "path": (paths or {}).get(run_id, sc.CI_WORKFLOW)} for run_id in jobs_by_run]
        page = int(args[-1].split("&page=")[1]) if "&page=" in args[-1] else 1  # GitHub's default page
        return {"total_count": len(runs) if total is None else total, "workflow_runs": runs[(page - 1) * size:page * size]}
    return query


def ci_jobs(steps=True, failed=()):
    return [{"name": name, "conclusion": "failure" if name in failed else "success",
             "steps": [{"name": "run"}] if steps else []} for name in sc.CI_JOBS]


def replica_receipt(key, candidate=SHA, head_failures=("tests.test_fixture.T.test_known",), head_tests_run=3000):
    document = {"schema": "director-ci-local-replica/v1", "repository": sc.REPOSITORY, "candidate_sha": candidate,
                "base_sha": OTHER, "produced_at": (NOW - timedelta(hours=1)).isoformat(),
                "lanes": [{"lane": lane, "head_tests_run": head_tests_run, "base_tests_run": 2990,
                           "head_failures": list(head_failures), "base_failures": ["tests.test_fixture.T.test_known"],
                           "command_sha256": "f" * 64} for lane in ("windows-postgres-lane", "ubuntu-replica")]}
    signed = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    return {"document": document, "signature": key.sign(signed).hex()}


def resign(key, envelope):
    signed = json.dumps(envelope["document"], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                        allow_nan=False).encode()
    return {"document": envelope["document"], "signature": key.sign(signed).hex()}


class CompletionScorecardTests(unittest.TestCase):
    def test_zero_hold_counters_are_not_reconciliation_proof(self):
        # Release 879 reported these exact zeros while authoritative plan digests conflicted.
        payload = health()
        payload["langgraph_primary"]["reconciliation_worker_ready"] = False
        report = sc.score(SHA, runtime_observations(payload), now=NOW)
        self.assertEqual(report["checks"][6]["status"], "UNKNOWN")
        self.assertIn("consistency command", report["checks"][6]["explanation"])
        self.assertEqual(report["checks"][18]["status"], "FAIL")
        self.assertIn("health counters alone can only fail 07 and 12", json.dumps(report["limitations"]))

    def test_reported_reconciliation_blocker_fails(self):
        for change in ({"ambiguous_effect_count": 2}, {"reconciliation_hold_reason": "plan digest conflict"},
                       {"historical_parallel_plan_hold_count": 1}):
            payload = health()
            payload["langgraph_primary"].update(change)
            with self.subTest(change=change):
                self.assertEqual(sc.score(SHA, runtime_observations(payload), now=NOW)["checks"][6]["status"], "FAIL")
        payload = health()
        del payload["langgraph_primary"]["reconciliation_hold_reason"]
        self.assertEqual(sc.score(SHA, runtime_observations(payload), now=NOW)["checks"][6]["status"], "UNKNOWN")

    def test_reported_blocker_still_fails_with_other_health_fields_missing(self):
        for fields in ({"ambiguous_effect_count": 1}, {"historical_parallel_plan_hold_count": 1},
                       {"reconciliation_hold_reason": "preserved plan mismatch"}):
            payload = health()
            payload["langgraph_primary"] = fields
            with self.subTest(fields=fields):
                report = sc.score(SHA, runtime_observations(payload), now=NOW)
                self.assertEqual(report["checks"][6]["status"], "FAIL")

    def test_topology_and_single_gateway_ignore_api_copy_of_token_without_exposure(self):
        report = collect_topology(topology())
        self.assertEqual([row["status"] for row in report["checks"][:2]], ["PASS", "PASS"])
        for row in report["checks"][:2]:
            self.assertEqual(row["evidence"][0]["facts"]["expected_gateway"], GATEWAY)
            self.assertEqual(row["evidence"][0]["facts"]["gateway_container_id"], "2" * 64)
            self.assertEqual(row["evidence"][0]["facts"]["gateway_image_id"], IMAGE)
        self.assertNotIn("fixture-app-token", json.dumps(report))
        self.assertNotIn("fixture-secret-bot", json.dumps(report))
        self.assertNotIn("SLACK_APP_TOKEN", json.dumps(report))

    def test_tokenless_gateway_preserves_identity_on_both_observations(self):
        for empty_token in (False, True):
            with self.subTest(empty_token=empty_token):
                rows = topology()
                gateway_env = rows[1]["Config"]["Env"]
                gateway_env[:] = [entry for entry in gateway_env if not entry.startswith("SLACK_APP_TOKEN=")]
                if empty_token:
                    gateway_env.append("SLACK_APP_TOKEN=")
                report = collect_topology(rows)
                self.assertEqual([row["status"] for row in report["checks"][:2]], ["PASS", "UNKNOWN"])
                self.assertEqual(report["checks"][1]["explanation"], "Canonical gateway app identity unavailable.")
                for row in report["checks"][:2]:
                    facts = row["evidence"][0]["facts"]
                    self.assertEqual(facts["expected_gateway"], GATEWAY)
                    self.assertEqual(facts["gateway_container_id"], rows[1]["Id"])
                    self.assertEqual(facts["gateway_image_id"], rows[1]["Image"])

    def test_stopped_missing_or_non_gateway_identity_cannot_pass(self):
        rows = topology()
        for name in (OLD_GATEWAY, "edge-engine-director-orchestrator-1", "edge-engine-director-n8n-1"):
            with self.subTest(name=name):
                report = collect_topology(rows, gateway=name)
                self.assertEqual([row["status"] for row in report["checks"][:2]], ["UNKNOWN", "UNKNOWN"])
                self.assertEqual(report["checks"][1]["evidence"][0]["facts"]["expected_gateway"], name)
        renamed = json.loads(json.dumps(rows))
        renamed[1]["Name"] = "/" + OLD_GATEWAY
        self.assertEqual(collect_topology(renamed)["checks"][1]["status"], "UNKNOWN")
        rows[1]["State"]["Running"] = False
        report = collect_topology(rows)
        self.assertEqual([row["status"] for row in report["checks"][:2]], ["UNKNOWN", "UNKNOWN"])
        for bad in ("/" + GATEWAY, "", "name with space", "x" * 130):
            with self.assertRaises(ValueError):
                sc.collect(SHA, now=NOW, gateway=bad)

    def test_live_without_gateway_leaves_topology_unknown_and_named(self):
        report = sc.score(SHA, runtime_observations(), now=NOW)
        self.assertEqual([row["status"] for row in report["checks"][:2]], ["UNKNOWN", "UNKNOWN"])
        self.assertIn("--gateway", report["checks"][0]["explanation"])

    def test_inventory_race_or_image_change_stops_collection(self):
        rows = topology()
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            collect_topology(rows, later_rows=rows[:2])
        rows[0]["Image"] = "sha256:" + "d" * 64
        with self.assertRaisesRegex(ValueError, "image changed"):
            collect_topology(rows)
        rows = topology()
        rows[1]["Image"] = "unverified-tag"
        with self.assertRaisesRegex(ValueError, "gateway image"):
            collect_topology(rows)

    def test_unlabelled_duplicate_gateway_is_counted(self):
        for name in ("/another-session-deliberate-build", "/" + OLD_GATEWAY):
            rows = topology()
            duplicate = json.loads(json.dumps(rows[1]))
            duplicate.update(Id="4" * 64, Name=name)
            rows.append(duplicate)
            with self.subTest(name=name):
                self.assertEqual(collect_topology(rows)["checks"][1]["status"], "FAIL")

    def test_host_visibility_or_unknown_same_app_process_prevents_gateway_pass(self):
        for host in ({"unreadable": 1, "gateway_process_count": 0}, {"unreadable": 0, "gateway_process_count": 1}, OSError("hidden host")):
            self.assertEqual(collect_topology(topology(), host=host)["checks"][1]["status"], "UNKNOWN")
        rows = topology()
        unknown = json.loads(json.dumps(rows[1]))
        unknown.update(Id="4" * 64, Name="/unknown-launcher")
        unknown["Config"]["Cmd"] = ["unknown-wrapper"]
        rows.append(unknown)
        self.assertEqual(collect_topology(rows)["checks"][1]["status"], "UNKNOWN")

    def test_wrong_channel_upstream_or_port_fails_project_route(self):
        self.assertEqual(collect_topology(topology(), upstream="edge-engine")["checks"][0]["status"], "FAIL")
        for wrong in ("channel", "port"):
            rows = topology()
            if wrong == "channel":
                rows[1]["Config"]["Env"][2] = "SLACK_DIRECTOR_CHANNEL_ID=other-project"
            else:
                rows[2]["NetworkSettings"]["Ports"]["5678/tcp"][0]["HostIp"] = "0.0.0.0"
            self.assertEqual(collect_topology(rows)["checks"][0]["status"], "FAIL")

    def test_wrong_active_workflow_url_does_not_pass_because_alias_is_healthy(self):
        workflow = {"active": True, "active_version": "published", "stored_version": "published", "http_node_count": 1,
                    "supported_request": False, "static_request": True, "supported_webhook": True, "direct_connection": True,
                    "editor_supported_request": True}
        self.assertEqual(collect_topology(topology(), workflow=workflow)["checks"][0]["status"], "FAIL")
        self.assertIn('h.nodes::json', sc._ACTIVE_INTAKE)
        self.assertNotIn('w.nodes', sc._ACTIVE_INTAKE)
        workflow["static_request"] = False
        self.assertEqual(collect_topology(topology(), workflow=workflow)["checks"][0]["status"], "UNKNOWN")
        workflow["static_request"] = True
        workflow["supported_request"] = True
        workflow["stored_version"] = "other-version"
        self.assertEqual(collect_topology(topology(), workflow=workflow)["checks"][0]["status"], "FAIL")
        self.assertEqual(collect_topology(topology(), workflow={})["checks"][0]["status"], "UNKNOWN")

    def test_native_security_recomputes_indexed_hashes_and_exact_file_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            scan_id, index, root = native_security_fixture(folder)
            with patch.object(sc, "_native_security_index", return_value=index), patch.object(sc, "_run_bytes", return_value=b"src/thing.py\0"), \
                    patch.object(sc, "_merge_base", return_value=OTHER):
                observation = sc._security_observation(scan_id, SHA, NOW)
                self.assertEqual(sc.score(SHA, [observation], now=NOW)["checks"][17]["status"], "PASS")
                # A base other than the candidate's merge base with main can omit its changes.
                with patch.object(sc, "_merge_base", return_value="c" * 40):
                    moved = sc._security_observation(scan_id, SHA, NOW)
                self.assertEqual(sc.score(SHA, [moved], now=NOW)["checks"][17]["status"], "UNKNOWN")
                with patch.object(sc, "_merge_base", return_value=None):
                    unreadable = sc._security_observation(scan_id, SHA, NOW)
                self.assertEqual(sc.score(SHA, [unreadable], now=NOW)["checks"][17]["status"], "UNKNOWN")
                self.assertEqual(sc.score(OTHER, [observation], now=NOW)["checks"][17]["status"], "UNKNOWN")
                (root / "coverage.json").write_text('{"completeness":"complete","verified":true}')
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    sc._security_observation(scan_id, SHA, NOW)

    def test_unindexed_copy_or_altered_native_seal_does_not_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            scan_id, index, root = native_security_fixture(folder)
            with patch.object(sc, "_native_security_index", return_value=index):
                with self.assertRaises(ValueError):
                    sc._security_observation("99999999-1234-1234-1234-123456789abc", SHA, NOW)
                manifest = json.loads((root / "scan-manifest.json").read_text())
                manifest["scan"]["target"]["headRevision"] = OTHER
                (root / "scan-manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "indexed seal"):
                    sc._security_observation(scan_id, SHA, NOW)

    def test_native_security_missing_changed_file_coverage_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            scan_id, index, _ = native_security_fixture(folder)
            with patch.object(sc, "_native_security_index", return_value=index), \
                    patch.object(sc, "_run_bytes", return_value=b"src/thing.py\0src/unreviewed.py\0"), \
                    patch.object(sc, "_merge_base", return_value=OTHER):
                observation = sc._security_observation(scan_id, SHA, NOW)
                self.assertEqual(sc.score(SHA, [observation], now=NOW)["checks"][17]["status"], "FAIL")

    def test_exactly_twenty_unique_fixed_checks_and_missing_is_unknown(self):
        self.assertEqual([f"{number:02}" for number in range(1, 21)], [item.id for item in sc.CHECKS])
        report = sc.score(SHA, now=NOW)
        self.assertEqual((report["passed"], report["percent"]), (0, 0))
        self.assertEqual({row["status"] for row in report["checks"]}, {"UNKNOWN"})
        self.assertFalse(report["activation_authorized"])

    def test_fake_self_attested_pass_and_saved_receipt_cannot_earn_points(self):
        fake = sc.Observation("19", True, "trust me", "signed-by-me", NOW, NOW + timedelta(minutes=1),
                              runtime_sha=SHA, image_id=IMAGE)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "accepted.json"
            path.write_text(json.dumps({"check": "19", "verified": True, "signature": "fake", "PASS": True}))
            report = sc.score(SHA, [fake, {"check_id": "19", "passed": True}], now=NOW, evidence_dir=Path(folder))
            self.assertEqual(report["passed"], 0)
            self.assertEqual(report["reference_files"][0]["status"], "UNVERIFIED_REFERENCE")
            self.assertEqual(len(report["reference_files"][0]["sha256"]), 64)

    def test_runtime_collection_grades_observed_fields_and_keeps_capture_unknown(self):
        report = sc.score(SHA, runtime_observations(), now=NOW, runtime_sha=SHA, image_id=IMAGE)
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["percent"], 10)
        self.assertEqual(report["checks"][11]["status"], "UNKNOWN")
        self.assertFalse(report["activation_authorized"])
        self.assertEqual(report["checks"][4]["evidence"][0]["runtime_sha"], SHA)

    def test_failure_is_not_unknown_and_true_strings_are_not_booleans(self):
        payload = health()
        payload["langgraph_primary"].update(historical_parallel_plan_hold_count=1, primary_runtime_active="true")
        payload["langgraph_primary"]["provider_activation"]["combined_live_ready"] = False
        report = sc.score(SHA, runtime_observations(payload), now=NOW)
        self.assertEqual([report["checks"][index]["status"] for index in (6, 11, 18)], ["FAIL"] * 3)

    def test_missing_runtime_field_is_unknown(self):
        payload = health()
        del payload["langgraph_primary"]["effect_executor_ready"]
        report = sc.score(SHA, runtime_observations(payload), now=NOW)
        self.assertEqual(report["checks"][18]["status"], "UNKNOWN")

    def test_stale_future_and_excessive_expiry_never_pass(self):
        observation = runtime_observations()[0]
        cases = [dataclasses.replace(observation, expires_at=NOW),
                 dataclasses.replace(observation, observed_at=NOW + timedelta(seconds=1)),
                 dataclasses.replace(observation, expires_at=NOW + timedelta(hours=1)),
                 dataclasses.replace(observation, observed_at=NOW.replace(tzinfo=None))]
        for item in cases:
            with self.subTest(item=item):
                self.assertEqual(sc.score(SHA, [item], now=NOW)["passed"], 0)

    def test_runtime_head_or_image_mismatch_does_not_transfer(self):
        observed = runtime_observations()
        for binding in ({"runtime_sha": OTHER}, {"image_id": "sha256:" + "d" * 64}):
            self.assertEqual(sc.score(SHA, observed, now=NOW, **binding)["passed"], 0)
        # A build candidate and runtime may legitimately differ; they stay separate.
        self.assertEqual(sc.score(OTHER, observed, now=NOW, runtime_sha=SHA)["passed"], 2)

    def test_wrong_project_endpoint_or_container_port_is_not_observed(self):
        wrong = health()
        wrong["environment"] = "edge-engine"
        self.assertEqual(sc.score(SHA, runtime_observations(wrong), now=NOW)["passed"], 0)
        data = containers()
        data[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = "0.0.0.0"
        report = sc.score(SHA, runtime_observations(rows=data), now=NOW)
        self.assertEqual(report["passed"], 0)

    def test_command_failure_does_not_disclose_secret_error_or_retry(self):
        with patch.object(sc, "_run_bytes", return_value=DIRECTOR_ID.encode()), \
                patch.object(sc, "_run_json", side_effect=ValueError("secret-token-should-not-display")) as query:
            report = sc.score(SHA, sc.collect(SHA, now=NOW, deployment=DEPLOYMENT), now=NOW)
        self.assertEqual(query.call_count, 1)
        self.assertNotIn("secret-token", json.dumps(report))

    def test_workload_probe_does_not_create_missing_journal_or_accept_corrupt_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")):
            ledger = Path(folder) / "admissions.sqlite3"
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=ledger), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "UNKNOWN")
            self.assertFalse(ledger.exists())
            ledger.write_text('{"verified":true,"last20":"all passed"}')
            original = ledger.read_bytes()
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=ledger), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "UNKNOWN")
            self.assertEqual(ledger.read_bytes(), original)

    def test_empty_prospective_journal_is_not_twenty_verified_assignments(self):
        from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
        reference = {"reference": "test-authorized-fixture", "sha256": "d" * 64}
        manifest = {"authorization": reference, "operator": "fixture operator",
                    "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                    "policy": {"kind": "canonical", "profile": "engineering"},
                    "history_mode": "prospective", "history_evidence": reference,
                    "prior_history_unknown": True, "quiescent_cutover_verified": True,
                    "all_future_assignments_required": True, "history": [], "routes": []}
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")):
            path = Path(folder) / "admissions.sqlite3"
            with patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
                EngineeringWorkloadLedger(path).bootstrap(manifest)
            before = path.read_bytes()
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "UNKNOWN")
            self.assertEqual(before, path.read_bytes())

    def test_twenty_prospective_admissions_recompute_allocation_from_actual_database(self):
        from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
        reference = {"reference": "fixture-only", "sha256": "d" * 64}
        route = {"route_id": "openai-codex:fixture-route", "product_id": "openai-codex", "actor_id": "fixture-actor", "provider_identity": "fixture-provider",
                 "model_family": "fixture-family", "eligible": True, "roles": ["implementation"],
                 "enrollment_evidence": reference}
        manifest = {"authorization": reference, "policy_authorization": reference, "operator": "fixture operator",
                    "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                    "policy": {"kind": "product", "targets": {"openai-codex": 100}},
                    "history_mode": "prospective", "history_evidence": reference,
                    "prior_history_unknown": True, "quiescent_cutover_verified": True,
                    "all_future_assignments_required": True, "routes": [route]}
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")), \
                patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
            path = Path(folder) / "admissions.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            ledger.bootstrap(manifest)
            for number in range(20):
                assignment_id = f"fixture-{number}"
                ledger.reserve({"assignment_id": assignment_id, "task_id": f"task-{number}", "scope": "test only",
                                "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                                "route_id": "openai-codex:fixture-route",
                                "packet_evidence": {"reference": "fixture-only", "sha256": f"{number:064x}"},
                                "bounds": {"timeout_seconds": 60, "call_limit": 1, "turn_limit": 1,
                                           "output_bytes_limit": 1000, "usd_limit": 1,
                                           "hard_usd_cap_verified": True, "usd_cap_evidence": reference}})
                # Real reported usage, known and within the declared bounds, so
                # Check 13's tri-state resource-bound result is genuinely True.
                ledger.begin_attempt(assignment_id, transport="local_cli")
                ledger.record_outcome(assignment_id, 1, {"outcome": "succeeded", "evidence": reference,
                                                          "usage": {"usd": 0.5, "turns": 1, "output_bytes": 500}})
            before = path.read_bytes()
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "PASS")
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(report["activation_authorized"])

    def test_twenty_prospective_admissions_pass_despite_allocation_over_target(self):
        from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
        reference = {"reference": "fixture-only", "sha256": "d" * 64}
        route = {"route_id": "openai-codex:fixture-route", "product_id": "openai-codex", "actor_id": "fixture-actor", "provider_identity": "fixture-provider",
                 "model_family": "fixture-family", "eligible": True, "roles": ["implementation"],
                 "enrollment_evidence": reference}
        # Canonical engineering profile: openai target is 40% with a now-retired
        # 45% hard ceiling. Twenty real reserve() admissions to the same lane push
        # its share to 100%, well above the retired ceiling, through the actual
        # admission path rather than a direct SQL insertion bypass.
        manifest = {"authorization": reference, "policy_authorization": reference, "operator": "fixture operator",
                    "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                    "policy": {"kind": "canonical", "profile": "engineering"},
                    "history_mode": "prospective", "history_evidence": reference,
                    "prior_history_unknown": True, "quiescent_cutover_verified": True,
                    "all_future_assignments_required": True, "routes": [route]}
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")), \
                patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
            path = Path(folder) / "admissions.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            ledger.bootstrap(manifest)
            for number in range(20):
                assignment_id = f"fixture-{number}"
                ledger.reserve({"assignment_id": assignment_id, "task_id": f"task-{number}", "scope": "test only",
                                "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                                "route_id": "openai-codex:fixture-route",
                                "packet_evidence": {"reference": "fixture-only", "sha256": f"{number:064x}"},
                                "bounds": {"timeout_seconds": 60, "call_limit": 1, "turn_limit": 1,
                                           "output_bytes_limit": 1000, "usd_limit": 1,
                                           "hard_usd_cap_verified": True, "usd_cap_evidence": reference}})
                # Real reported usage, known and within the declared bounds, so
                # Check 13's tri-state resource-bound result is genuinely True
                # even though this window is far above the retired allocation ceiling.
                ledger.begin_attempt(assignment_id, transport="local_cli")
                ledger.record_outcome(assignment_id, 1, {"outcome": "succeeded", "evidence": reference,
                                                          "usage": {"usd": 0.5, "turns": 1, "output_bytes": 500}})
            status = ledger.status()
            openai_row = next(r for r in status["utilization"] if r["bucket"] == "openai")
            self.assertEqual(openai_row["admitted"], 20)
            self.assertGreater(openai_row["share_percent"], openai_row["target_percent"] + 5)

            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "PASS")

    def test_twenty_reservations_alone_without_reported_usage_are_unknown(self):
        """Twenty real admissions with no reported outcome usage must be
        UNKNOWN, never PASS: unknown usage is not treated as compliant."""
        from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
        reference = {"reference": "fixture-only", "sha256": "d" * 64}
        route = {"route_id": "openai-codex:fixture-route", "product_id": "openai-codex", "actor_id": "fixture-actor", "provider_identity": "fixture-provider",
                 "model_family": "fixture-family", "eligible": True, "roles": ["implementation"],
                 "enrollment_evidence": reference}
        manifest = {"authorization": reference, "policy_authorization": reference, "operator": "fixture operator",
                    "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                    "policy": {"kind": "product", "targets": {"openai-codex": 100}},
                    "history_mode": "prospective", "history_evidence": reference,
                    "prior_history_unknown": True, "quiescent_cutover_verified": True,
                    "all_future_assignments_required": True, "routes": [route]}
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")), \
                patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
            path = Path(folder) / "admissions.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            ledger.bootstrap(manifest)
            for number in range(20):
                ledger.reserve({"assignment_id": f"fixture-{number}", "task_id": f"task-{number}", "scope": "test only",
                                "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                                "route_id": "openai-codex:fixture-route",
                                "packet_evidence": {"reference": "fixture-only", "sha256": f"{number:064x}"},
                                "bounds": {"timeout_seconds": 60, "call_limit": 1, "turn_limit": 1,
                                           "output_bytes_limit": 1000, "usd_limit": 1,
                                           "hard_usd_cap_verified": True, "usd_cap_evidence": reference}})
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "UNKNOWN")

    def test_one_known_resource_violation_fails_even_if_others_unknown(self):
        """A single known reported bound violation must FAIL Check 13, even
        though the other nineteen admissions have no reported usage yet."""
        from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
        reference = {"reference": "fixture-only", "sha256": "d" * 64}
        route = {"route_id": "openai-codex:fixture-route", "product_id": "openai-codex", "actor_id": "fixture-actor", "provider_identity": "fixture-provider",
                 "model_family": "fixture-family", "eligible": True, "roles": ["implementation"],
                 "enrollment_evidence": reference}
        manifest = {"authorization": reference, "policy_authorization": reference, "operator": "fixture operator",
                    "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                    "policy": {"kind": "product", "targets": {"openai-codex": 100}},
                    "history_mode": "prospective", "history_evidence": reference,
                    "prior_history_unknown": True, "quiescent_cutover_verified": True,
                    "all_future_assignments_required": True, "routes": [route]}
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")), \
                patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
            path = Path(folder) / "admissions.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            ledger.bootstrap(manifest)
            for number in range(20):
                assignment_id = f"fixture-{number}"
                ledger.reserve({"assignment_id": assignment_id, "task_id": f"task-{number}", "scope": "test only",
                                "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                                "route_id": "openai-codex:fixture-route",
                                "packet_evidence": {"reference": "fixture-only", "sha256": f"{number:064x}"},
                                "bounds": {"timeout_seconds": 60, "call_limit": 1, "turn_limit": 1,
                                           "output_bytes_limit": 1000, "usd_limit": 1,
                                           "hard_usd_cap_verified": True, "usd_cap_evidence": reference}})
                if number == 0:
                    # One real, known, over-bound report; the other nineteen
                    # remain unsettled with unknown usage.
                    ledger.begin_attempt(assignment_id, transport="local_cli")
                    ledger.record_outcome(assignment_id, 1, {"outcome": "succeeded", "evidence": reference,
                                                              "usage": {"usd": 0.5, "turns": 1, "output_bytes": 2000}})
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "FAIL")

    def test_pending_recovery_attempt_is_unknown_between_two_known_passes(self):
        """A permitted transient-recovery attempt must not be silently ignored.

        Twenty fully-reported single-attempt admissions are a genuine PASS.
        Once one of them begins a second, permitted attempt, that admission's
        attempt count no longer matches its settled-outcome count, so the
        aggregate result must fall to UNKNOWN even though its prior, still
        fully-reported usage remains within bounds. Recording the second
        outcome (also in bounds) restores PASS. No completion/settlement is
        ever recorded, proving this is not a settlement gate.
        """
        from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
        reference = {"reference": "fixture-only", "sha256": "d" * 64}
        route = {"route_id": "openai-codex:fixture-route", "product_id": "openai-codex", "actor_id": "fixture-actor", "provider_identity": "fixture-provider",
                 "model_family": "fixture-family", "eligible": True, "roles": ["implementation"],
                 "enrollment_evidence": reference}
        manifest = {"authorization": reference, "policy_authorization": reference, "operator": "fixture operator",
                    "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                    "policy": {"kind": "product", "targets": {"openai-codex": 100}},
                    "history_mode": "prospective", "history_evidence": reference,
                    "prior_history_unknown": True, "quiescent_cutover_verified": True,
                    "all_future_assignments_required": True, "routes": [route]}
        retried_id = "fixture-retried"
        with tempfile.TemporaryDirectory() as folder, patch.object(sc, "_run_json", side_effect=ValueError("offline")), \
                patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
            path = Path(folder) / "admissions.sqlite3"
            ledger = EngineeringWorkloadLedger(path)
            ledger.bootstrap(manifest)
            for number in range(19):
                assignment_id = f"fixture-{number}"
                ledger.reserve({"assignment_id": assignment_id, "task_id": f"task-{number}", "scope": "test only",
                                "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                                "route_id": "openai-codex:fixture-route",
                                "packet_evidence": {"reference": "fixture-only", "sha256": f"{number:064x}"},
                                "bounds": {"timeout_seconds": 60, "call_limit": 1, "turn_limit": 1,
                                           "output_bytes_limit": 1000, "usd_limit": 1,
                                           "hard_usd_cap_verified": True, "usd_cap_evidence": reference}})
                ledger.begin_attempt(assignment_id, transport="local_cli")
                ledger.record_outcome(assignment_id, 1, {"outcome": "succeeded", "evidence": reference,
                                                          "usage": {"usd": 0.5, "turns": 1, "output_bytes": 500}})
            ledger.reserve({"assignment_id": retried_id, "task_id": "task-retried", "scope": "test only",
                            "kind": "implementation", "product_id": "openai-codex", "transport": "local_cli",
                            "route_id": "openai-codex:fixture-route",
                            "packet_evidence": {"reference": "fixture-only", "sha256": "f" * 64},
                            "bounds": {"timeout_seconds": 60, "call_limit": 2, "turn_limit": 6,
                                       "output_bytes_limit": 1000, "usd_limit": 1,
                                       "hard_usd_cap_verified": True, "usd_cap_evidence": reference}})
            ledger.begin_attempt(retried_id, transport="local_cli")
            ledger.record_outcome(retried_id, 1, {"outcome": "transient", "evidence": reference,
                                                   "usage": {"usd": 0.5, "turns": 1, "output_bytes": 500}})

            # Phase 1: 20/20 attempts settled, all known and in bounds -> PASS.
            status = ledger.status()
            self.assertEqual(status["total_admitted_assignments"], 20)
            self.assertEqual(sum(r["attempt_count"] for r in status["records"]), 20)
            self.assertEqual(sum(r["settled_attempts"] for r in status["records"]), 20)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM completions").fetchone()[0], 0)
            self.assertTrue(status["current_window_checks"]["reported_resource_bounds_ok"])
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "PASS")
            self.assertFalse(report["activation_authorized"])

            # Phase 2: a permitted recovery attempt is admitted but not yet
            # settled -> the pending attempt makes the result UNKNOWN.
            ledger.begin_attempt(retried_id, transport="local_cli")
            status = ledger.status()
            self.assertEqual(sum(r["attempt_count"] for r in status["records"]), 21)
            self.assertEqual(sum(r["settled_attempts"] for r in status["records"]), 20)
            self.assertEqual(status["total_admitted_assignments"], 20)
            self.assertIsNone(status["current_window_checks"]["reported_resource_bounds_ok"])
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "UNKNOWN")

            # Phase 3: recording the recovered outcome (also in bounds) restores
            # PASS, with no completion/settlement ever recorded.
            ledger.record_outcome(retried_id, 2, {"outcome": "succeeded", "evidence": reference,
                                                   "usage": {"usd": 0.25, "turns": 1, "output_bytes": 100}})
            status = ledger.status()
            self.assertEqual(sum(r["attempt_count"] for r in status["records"]), 21)
            self.assertEqual(sum(r["settled_attempts"] for r in status["records"]), 21)
            self.assertEqual(status["total_admitted_assignments"], 20)
            self.assertTrue(status["current_window_checks"]["reported_resource_bounds_ok"])
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM completions").fetchone()[0], 0)
            report = sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)
            self.assertEqual(report["checks"][12]["status"], "PASS")
            self.assertFalse(report["activation_authorized"])

    def test_ci_uses_existing_oracle_actual_steps_and_exact_sha(self):
        jobs = [{"name": name, "conclusion": "success", "steps": [{"name": "actual test"}]} for name in sc.CI_JOBS]
        def external_query(args):
            if args[0] == "docker":
                raise ValueError("offline")
            if "/jobs?" in args[-1]:
                return {"total_count": len(jobs), "jobs": jobs}
            self.assertIn("head_sha=" + SHA, args[-1])
            return {"total_count": 1, "workflow_runs": [{"id": 123, "path": sc.CI_WORKFLOW}]}
        with patch.object(sc, "_run_json", side_effect=external_query):
            observations = sc.collect(SHA, now=NOW, github=True)
            self.assertEqual(sc.score(SHA, observations, now=NOW)["checks"][15]["status"], "PASS")
            self.assertEqual(sc.score(OTHER, observations, now=NOW)["checks"][15]["status"], "UNKNOWN")
            jobs[0]["steps"] = []
            self.assertEqual(sc.score(SHA, sc.collect(SHA, now=NOW, github=True), now=NOW)["checks"][15]["status"], "FAIL")

    def test_ci_missing_job_cannot_pass(self):
        def external_query(args):
            if args[0] == "docker":
                raise ValueError("offline")
            return ({"total_count": 0, "jobs": []} if "/jobs?" in args[-1]
                    else {"total_count": 1, "workflow_runs": [{"id": 1, "path": sc.CI_WORKFLOW}]})
        with patch.object(sc, "_run_json", side_effect=external_query):
            self.assertEqual(sc.score(SHA, sc.collect(SHA, now=NOW, github=True), now=NOW)["checks"][15]["status"], "FAIL")

    def test_ten_distinct_actual_offline_runs_can_meet_semantic_standard(self):
        rows = [run(number=number) for number in range(10)]
        self.assertTrue(qualify(rows))
        rows[-1]["run_id"] = rows[0]["run_id"]
        self.assertFalse(qualify(rows))
        self.assertIsNone(qualify(rows[:9]))

    def test_bot_canary_not_actual_owner_and_simulation_not_real_paid(self):
        row = run("paid")
        row["actual_owner_event"] = False
        self.assertFalse(qualify([row], "paid"))
        self.assertFalse(qualify([run()], "paid"))

    def test_unknown_estimated_or_reserved_cost_never_actual_cost_pass(self):
        for change in ({"actual_cost_usd": None}, {"cost_basis": "estimate"}, {"cost_basis": "reservation"}):
            row = {**run("paid"), **change}
            self.assertIsNot(qualify([row], "paid"), True)
        self.assertTrue(qualify([run("paid")], "paid"))

    @staticmethod
    def qualification_row(**changes):
        row = {"run_id": "canary-1", "source_event_id_sha256": "a" * 64, "task_id": "DIR-LG-AB12",
               "assignment_ids": ["ASSIGN-1"], "attempt_ids": ["attempt-1"], "effect_ids": ["effect-1"],
               "provider_route": "claude", "runtime_sha": SHA, "image_id": IMAGE, "configuration_digest": "c" * 64,
               "duplicate_external_effects": 0, "effect_state": "PROVIDER_EFFECT_RECORDED",
               "delivery_state": "DELIVERED", "readback_verified": True,
               "status_receipt_counts": {"tasks_created": 0, "assignments_created": 0,
                                         "provider_attempts_created": 0, "deduplicated_receipts": 1},
               "actual_cost_usd": None, "policy_violations": 0}
        row.update(changes)
        return row

    def classify(self, row):
        return sc.evaluate_qualification_record(row, runtime_sha=SHA, image_id=IMAGE, configuration_digest="c" * 64)[0]

    def test_unknown_cost_qualification_is_distinct_from_measured_cost_pass(self):
        self.assertEqual("QUALIFIED_COST_UNKNOWN", self.classify(self.qualification_row()))
        self.assertEqual("QUALIFIED_COST_MEASURED", self.classify(self.qualification_row(actual_cost_usd=0.12)))
        # The older measured-cost criterion is preserved: unknown cost is still not a PASS there.
        self.assertIsNone(qualify([{**run("paid"), "actual_cost_usd": None}], "paid"))
        self.assertIsNone(sc.evaluate_run_record({**run("paid"), "actual_cost_usd": None}, mode="paid",
                                                 runtime_sha=SHA, image_id=IMAGE)[0])
        self.assertNotIn("PASS", sc.QUALIFICATION_VERDICTS)

    def test_qualification_never_manufactures_missing_links_or_status_receipts(self):
        for changes in ({"attempt_ids": []}, {"effect_ids": [""]}, {"source_event_id_sha256": None},
                        {"effect_state": None}, {"status_receipt_counts": {"tasks_created": None}},
                        {"actual_cost_usd": float("nan")}, {"actual_cost_usd": "0.10"}, {"run_id": ""}):
            with self.subTest(changes=changes):
                self.assertEqual("UNKNOWN", self.classify(self.qualification_row(**changes)))
        incomplete = self.qualification_row()
        incomplete.pop("status_receipt_counts")
        self.assertEqual("UNKNOWN", self.classify(incomplete))

    def test_host_probe_defects_are_closed(self):
        # The Director's host-boundary probe cases against the original candidate.
        for case, changes in (("infinite_cost", {"actual_cost_usd": float("inf")}),
                              ("negative_infinite_cost", {"actual_cost_usd": float("-inf")}),
                              ("empty_route", {"provider_route": ""}),
                              ("unknown_route", {"provider_route": "not-a-lane"}),
                              ("alias_route_is_not_canonical", {"provider_route": "external_openai"}),
                              ("non_string_task", {"task_id": 42}),
                              ("non_string_route", {"provider_route": ["claude"]}),
                              ("boolean_evidence_counts", {"duplicate_external_effects": False, "policy_violations": False}),
                              ("boolean_status_counts", {"status_receipt_counts": {
                                  "tasks_created": False, "assignments_created": False,
                                  "provider_attempts_created": False, "deduplicated_receipts": True}}),
                              ("float_counter", {"duplicate_external_effects": 0.0}),
                              ("duplicate_lineage", {"attempt_ids": ["attempt-1", "attempt-1"]}),
                              ("duplicate_effects", {"effect_ids": ["effect-1", "effect-1"]}),
                              ("malformed_identity", {"assignment_ids": ["assign one"]}),
                              ("malformed_run_id", {"run_id": "run id"})):
            with self.subTest(case=case):
                self.assertEqual("UNKNOWN", self.classify(self.qualification_row(**changes)))
        self.assertEqual("NOT_QUALIFIED", self.classify(self.qualification_row(effect_state="ZERO_PROVIDER_EFFECT")))
        self.assertEqual("NOT_QUALIFIED", self.classify(self.qualification_row(effect_state="ZERO_PROVIDER_EFFECT",
                                                                               actual_cost_usd=0)))
        self.assertEqual("QUALIFIED_COST_UNKNOWN", self.classify(self.qualification_row(provider_route="openai")))
        for verdict in ("QUALIFIED_COST_UNKNOWN", "QUALIFIED_COST_MEASURED"):
            row = self.qualification_row(actual_cost_usd=None if verdict.endswith("UNKNOWN") else 0.5)
            got, reason = sc.evaluate_qualification_record(row, runtime_sha=SHA, image_id=IMAGE, configuration_digest="c" * 64)
            self.assertEqual(verdict, got)
            self.assertIn("asserted links only", reason)
            self.assertNotIn("verified", reason.replace("authenticated collector correlation", ""))

    def test_qualification_fails_closed_on_ambiguity_duplicates_readback_and_binding(self):
        for changes in ({"effect_state": "AMBIGUOUS_DELIVERY_EFFECT"}, {"duplicate_external_effects": 1},
                        {"policy_violations": 1}, {"delivery_state": "ACKNOWLEDGED"}, {"readback_verified": None},
                        {"runtime_sha": OTHER}, {"configuration_digest": "d" * 64},
                        {"status_receipt_counts": {"tasks_created": 1, "assignments_created": 0,
                                                   "provider_attempts_created": 0, "deduplicated_receipts": 1}}):
            with self.subTest(changes=changes):
                self.assertEqual("NOT_QUALIFIED", self.classify(self.qualification_row(**changes)))
        with self.assertRaises(ValueError):
            sc.evaluate_qualification_record(self.qualification_row(), runtime_sha=SHA, image_id=IMAGE,
                                             configuration_digest="not-a-digest")

    def test_wall_time_not_machine_time_and_strict_cost_ceiling(self):
        for changes in ({"wall_seconds": 65, "owner_pause_seconds": 35}, {"actual_cost_usd": 1},
                        {"machine_seconds": 0}, {"actual_cost_usd": float("nan")},
                        {"wall_seconds": float("inf")}, {"actual_cost_usd": True},
                        {"runtime_sha": OTHER}, {"terminal_reply_count": 2}, {"delivery_ambiguous": True}):
            with self.subTest(changes=changes):
                self.assertIsNot(qualify([{**run("paid"), **changes}], "paid"), True)

    def test_offline_calls_or_missing_simulation_label_fail(self):
        for change in ({"operational_provider_attempts": 1}, {"simulation_label": "done"}, {"actual_cost_usd": 0.01}):
            rows = [run(number=number) for number in range(10)]
            rows[0].update(change)
            self.assertFalse(qualify(rows))

    def test_markdown_keeps_pending_actions_and_reference_limitations(self):
        report = sc.score(SHA, now=NOW)
        text = sc.render_markdown(report)
        self.assertIn("0/20 verified (0%)", text)
        self.assertIn("unknown cost is not zero", text.lower())
        self.assertIn("Next:", text)
        self.assertIn("do not contribute points", text)
        self.assertIn("Next: Run the in-image read-only consistency command; it must report zero unresolved, "
                      "ambiguous or conflicting records for the running generation.", text)

    def test_bad_candidate_and_naive_score_time_rejected(self):
        with self.assertRaises(ValueError):
            sc.score("main")
        with self.assertRaises(ValueError):
            sc.score(SHA, now=NOW.replace(tzinfo=None))

    def test_cli_offline_creates_both_reports_and_does_not_read_live(self):
        script = Path(__file__).resolve().parents[1] / "scripts/director-scorecard.py"
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(script), "--candidate-sha", SHA, "--output-dir", folder],
                                    capture_output=True, timeout=15, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((Path(folder) / "completion-scorecard.json").read_text())
            self.assertEqual(report["passed"], 0)
            self.assertTrue((Path(folder) / "completion-scorecard.md").is_file())
            self.assertFalse(json.loads(result.stdout)["activation_authorized"])
            rejected = subprocess.run([sys.executable, str(script), "--candidate-sha", SHA, "--output-dir", folder, "--gateway", GATEWAY],
                                      capture_output=True, timeout=15, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("--gateway require --live", rejected.stderr)


class ScorecardCollectorTests(unittest.TestCase):
    """Every collector: a positive case, a negative control and UNKNOWN on a missing source."""

    def world(self):
        return World(self)

    def test_full_selection_reaches_every_live_collector_without_leaking(self):
        world = self.world()
        report = world.report()
        passing = {"03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "15", "17", "19", "20"}
        self.assertEqual({item["id"] for item in report["checks"] if item["status"] == "PASS"}, passing,
                         [(item["id"], item["status"], item["explanation"]) for item in report["checks"]])
        self.assertEqual(world.opened, [HEALTH])
        self.assertEqual(world.consistency_tasks, [PAID_TASK, APPROVAL_TASK])
        self.assertEqual(world.exec_timeouts, [45, 45])
        text = json.dumps(report)
        for secret in (*SECRETS, CHANNEL_FIXTURE, OWNER, BOT):
            self.assertNotIn(secret, text)
        self.assertFalse(report["activation_authorized"])

    def test_live_requires_explicit_deployment_and_never_probes_legacy(self):
        forbidden = Mock(side_effect=AssertionError("no probe may run without --deployment"))
        with patch.object(sc, "_run_bytes", forbidden), patch.object(sc, "_run_json", forbidden), \
                patch.object(sc, "build_opener", forbidden), patch.object(sc, "_SlackThreads", forbidden):
            observations = sc.collect(SHA, now=NOW, channel=CHANNEL_FIXTURE, approval_task=APPROVAL_TASK)
        self.assertEqual([(item.check_id, item.passed) for item in observations], [("19", None)])
        self.assertIn("--deployment", observations[0].explanation)
        self.assertEqual(row(sc.score(SHA, observations, now=NOW), "19")["status"], "UNKNOWN")
        forbidden.assert_not_called()
        for bad in ({"deployment": "Edge Engine"}, {"deployment": "../x"}, {"channel": "cfixture12"},
                    {"channel": "C0SHORT"}, {"approval_task": "DIR-LG-ab"}, {"paid_thread": "12.3"},
                    {"trials": ["1789210000.1"] * 2}, {"trials": [ts(n) for n in range(11)]},
                    {"drill_threads": {"refusal-5xx": ts(1)}},
                    {"drill_threads": {"refusal-4xx": ts(1), "reserved-5xx": ts(1)}},
                    {"rubric_path": Path("rubric.json")}, {"paid_rubric_path": Path("paid.json")},
                    {"ci_replica_receipt": Path("receipt.json")}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sc.collect(SHA, now=NOW, **bad)

    def test_deployment_discovery_uses_compose_labels_and_loopback_port(self):
        world = self.world()
        report = world.report(only_deployment=True)
        self.assertEqual([row(report, key)["status"] for key in ("05", "19")], ["PASS", "PASS"])
        first = world.calls[0]
        self.assertEqual(first[:4], ["docker", "ps", "-q", "--no-trunc"])
        self.assertIn("label=com.docker.compose.project=" + DEPLOYMENT, first)
        self.assertIn("label=com.docker.compose.service=orchestrator", first)
        self.assertEqual(world.opened, [HEALTH])
        self.assertEqual(row(report, "05")["evidence"][0]["source"], HEALTH)
        cases = []
        foreign = self.world()
        foreign.rows[0]["Config"]["Labels"]["com.docker.compose.project"] = "director-orchestrator"
        foreign.ps_override = [DIRECTOR_ID]
        cases.append(foreign)
        twice = self.world()
        twice.rows.append({**json.loads(json.dumps(twice.rows[0])), "Id": "6" * 64})
        cases.append(twice)
        exposed = self.world()
        exposed.rows[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = "0.0.0.0"
        cases.append(exposed)
        stopped = self.world()
        stopped.rows[0]["State"]["Running"] = False
        cases.append(stopped)
        wrong_env = self.world()
        wrong_env.payload["environment"] = "edge-engine"
        cases.append(wrong_env)
        for case in cases:
            observed = case.report(only_deployment=True)
            self.assertEqual(observed["passed"], 0)
            self.assertEqual(row(observed, "19")["status"], "UNKNOWN")
        self.assertEqual(stopped.opened, [])

    def test_no_committed_channel_or_legacy_endpoint(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "src/director_orchestrator/completion_scorecard.py", root / "scripts/director-scorecard.py"):
            text = path.read_text(encoding="utf-8")
            for legacy in ("C0BQ6GSCNLR", "director-orchestrator-orchestrator-1", "director-orchestrator-n8n",
                           "127.0.0.1:8080", "localhost:8080"):
                with self.subTest(path=path.name, legacy=legacy):
                    self.assertNotIn(legacy, text)

    def test_private_g_channel_ids_are_accepted(self):
        report = self.world().report(channel="GPRIVATE12")
        self.assertEqual(row(report, "03")["status"], "UNKNOWN")
        self.assertIn("--channel", row(report, "03")["explanation"])

    def test_app_token_fingerprints_compare_in_constant_time_02(self):
        import hmac
        with patch.object(sc.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
            self.assertEqual(row(collect_topology(topology()), "02")["status"], "PASS")
        self.assertTrue(any(len(call.args[0]) == 32 and isinstance(call.args[0], bytes) for call in spy.call_args_list))

    def test_route_requires_cli_channel_and_gateway_in_deployment(self):
        self.assertEqual(row(collect_topology(topology()), "01")["status"], "PASS")
        report = collect_topology(topology(), channel=None)
        self.assertEqual(row(report, "01")["status"], "UNKNOWN")
        self.assertIn("--channel", row(report, "01")["explanation"])
        self.assertEqual(row(collect_topology(topology(), channel="COTHERCHAN1"), "01")["status"], "FAIL")
        foreign = topology()
        foreign[1]["Config"]["Labels"] = compose_labels("slackbot", project="director-orchestrator")
        self.assertEqual(row(collect_topology(foreign), "01")["status"], "FAIL")
        self.assertEqual(row(collect_topology(foreign, channel=None), "01")["status"], "FAIL")
        unlabelled = topology()
        del unlabelled[1]["Config"]["Labels"]
        self.assertEqual(row(collect_topology(unlabelled), "01")["status"], "UNKNOWN")
        no_n8n = [item for item in topology() if item["Id"] != N8N_DB_ID]
        self.assertEqual(row(collect_topology(no_n8n), "01")["status"], "UNKNOWN")
        # The gateway must share a network with n8n on which the relay host resolves.
        detached = topology()
        detached[1]["NetworkSettings"]["Networks"] = {"other_default": {"NetworkID": "9" * 64, "Aliases": ["slackbot"]}}
        self.assertEqual(row(collect_topology(detached), "01")["status"], "FAIL")
        unaliased = topology()
        unaliased[2]["NetworkSettings"]["Networks"][NETWORK].update(Aliases=[], DNSNames=[])
        self.assertEqual(row(collect_topology(unaliased), "01")["status"], "FAIL")
        uninspected = topology()
        del uninspected[1]["NetworkSettings"]
        self.assertEqual(row(collect_topology(uninspected), "01")["status"], "UNKNOWN")
        # n8n must reach the selected Director as orchestrator, and nothing else may answer to that name.
        cut_off = topology()
        cut_off[0]["NetworkSettings"]["Networks"] = {"other_default": {"NetworkID": "9" * 64, "Aliases": ["orchestrator"]}}
        self.assertEqual(row(collect_topology(cut_off), "01")["status"], "FAIL")
        shadowed = topology()
        shadowed.append({"Id": "6" * 64, "Name": "/another-orchestrator", "State": {"Running": True}, "Config": {},
                         "NetworkSettings": {"Networks": {NETWORK: {"NetworkID": "a" * 64, "Aliases": ["orchestrator"]}}}})
        self.assertEqual(row(collect_topology(shadowed), "01")["status"], "FAIL")
        relay = topology()
        relay[1]["Config"]["Env"][3] = "N8N_DIRECTOR_WEBHOOK_URL=http://edge-engine-director-n8n-1:5678/webhook/director-intake"
        self.assertEqual(row(collect_topology(relay), "01")["status"], "PASS")
        relay[1]["Config"]["Env"][3] = "N8N_DIRECTOR_WEBHOOK_URL=http://director-orchestrator-n8n-1:5678/webhook/director-intake"
        self.assertEqual(row(collect_topology(relay), "01")["status"], "FAIL")
        self.assertNotIn(CHANNEL_FIXTURE, json.dumps(collect_topology(topology())))

    def test_channel_other_than_the_deployments_reads_no_slack_source(self):
        world = self.world()
        world.handle = Mock(side_effect=world.handle)
        report = world.report(channel="COTHERCHAN1")
        world.handle.assert_not_called()
        for key in ("03", "04", "06", "08", "09", "10", "11", "20"):
            self.assertEqual(row(report, key)["status"], "UNKNOWN")
            self.assertIn("--channel", row(report, key)["explanation"])
        self.assertEqual(row(report, "07")["status"], "PASS")

    def test_intake_drills_03(self):
        self.assertEqual(self.world().state("03"), "PASS")
        refused_with_task = self.world()
        refused_with_task.bindings[ts(100)] = (200, "DIR-LG-5E")
        self.assertEqual(refused_with_task.state("03"), "FAIL")
        refused_with_receipt = self.world()
        refused_with_receipt.threads[ts(100)].append({"ts": ts(102), "thread_ts": ts(100), "user": BOT,
                                                      "text": "*DIR-LG-5E — REQUEST RECEIVED*"})
        self.assertEqual(refused_with_receipt.state("03"), "FAIL")
        ambiguous_reservation = self.world()
        ambiguous_reservation.bindings[ts(200)] = (409, None)
        self.assertEqual(ambiguous_reservation.state("03"), "FAIL")
        duplicated = self.world()
        duplicated.tasks["DIR-LG-5E"]["qualification_evidence"]["task_scope_counters"]["thread_tasks"] = 2
        self.assertEqual(duplicated.state("03"), "FAIL")
        receipted_5xx = self.world()
        receipted_5xx.threads[ts(200)].append({"ts": ts(202), "thread_ts": ts(200), "user": BOT,
                                               "text": "*DIR-LG-5E — REQUEST RECEIVED*"})
        self.assertEqual(receipted_5xx.state("03"), "UNKNOWN")
        # Only a reply's header is a BLOCKED notice: a quoted one is not, and two notices are not one refusal.
        quoted = self.world()
        quoted.threads[ts(100)][1]["text"] = "Result below.\nThe docs mention *DIRECTOR — BLOCKED* replies."
        self.assertEqual(quoted.state("03"), "UNKNOWN")
        doubled = self.world()
        doubled.threads[ts(100)].append({**doubled.threads[ts(100)][1], "ts": ts(102)})
        self.assertEqual(doubled.state("03"), "UNKNOWN")
        tagged = self.world()
        tagged.threads[ts(100)][1]["text"] = ("*[[Director Gateway | fixture | 2026-09-12 | 10:00 UTC | BEGIN]]*\n\n"
                                              + tagged.threads[ts(100)][1]["text"])
        self.assertEqual(tagged.state("03"), "PASS")
        # A receipt quoted inside another reply is not a second receipt.
        quoted_receipt = self.world()
        thread = quoted_receipt.threads[ts(300)]
        thread.remove(next(m for m in thread if m.get("ts") == ts(302)))
        thread.append({"ts": ts(303), "thread_ts": ts(300), "user": BOT,
                       "text": "Note: earlier reply read *DIR-LG-D0 — REQUEST RECEIVED*"})
        self.assertEqual(quoted_receipt.state("09"), "UNKNOWN")
        # Slack intake evidence is bound to the selected gateway: recreated after the drills, or absent.
        new_gateway = self.world()
        new_gateway.rows[-1]["Created"] = "2026-09-12T11:00:00Z"
        self.assertEqual(new_gateway.state("03"), "UNKNOWN")
        report = self.world().report(gateway=None)
        self.assertEqual(row(report, "03")["status"], "UNKNOWN")
        self.assertIn("--gateway", row(report, "03")["explanation"])
        # One reply repeating its receipt text is one receipt: duplicate input was not exercised.
        repeated = self.world()
        thread = repeated.threads[ts(300)]
        thread.remove(next(m for m in thread if m.get("ts") == ts(302)))
        receipt = next(m for m in thread if m.get("ts") == ts(301))
        receipt["text"] = receipt["text"] + "\n" + receipt["text"]
        self.assertEqual(repeated.state("09"), "UNKNOWN")
        edited_refusal = self.world()
        edited_refusal.threads[ts(100)][1]["edited"] = {"user": BOT, "ts": ts(190)}
        self.assertEqual(edited_refusal.state("03"), "UNKNOWN")
        # Receipt text added by an edit is never parsed, so it cannot turn the drill into a FAIL either.
        forged = self.world()
        forged.threads[ts(100)][1].update(text="*DIR-LG-5E — REQUEST RECEIVED*", edited={"user": BOT, "ts": ts(190)})
        report = forged.report()
        self.assertEqual(row(report, "03")["status"], "UNKNOWN")
        self.assertIn("edited or subtyped", row(report, "03")["explanation"])
        unreserved = self.world()
        unreserved.bindings[ts(200)] = (404, None)
        self.assertEqual(unreserved.state("03"), "UNKNOWN")
        # The transport failure is its own path: a reserved task (200, one task) passes, a conflict fails.
        reserved_transport = self.world()
        reserved_transport.bindings[ts(150)] = (200, "DIR-LG-5E")
        self.assertEqual(reserved_transport.state("03"), "PASS")
        conflicting_transport = self.world()
        conflicting_transport.bindings[ts(150)] = (409, None)
        self.assertEqual(conflicting_transport.state("03"), "FAIL")
        answered = self.world()
        answered.threads[ts(150)].append({"ts": ts(152), "thread_ts": ts(150), "user": BOT,
                                          "text": "*DIR-LG-5E — REQUEST RECEIVED*"})
        self.assertEqual(answered.state("03"), "UNKNOWN")
        report = self.world().report(drill_threads={"refusal-4xx": ts(100), "reserved-5xx": ts(200)})
        self.assertEqual(row(report, "03")["status"], "UNKNOWN")
        self.assertIn("transport-failure", row(report, "03")["explanation"])
        report = self.world().report(drill_threads={"refusal-4xx": ts(100)})
        self.assertEqual(row(report, "03")["status"], "UNKNOWN")
        self.assertIn("reserved-5xx", row(report, "03")["explanation"])
        unavailable = self.world()
        unavailable.failing.add(ts(100))
        self.assertEqual(unavailable.state("03"), "UNKNOWN")
        report = self.world().report(channel=None)
        self.assertEqual(row(report, "03")["status"], "UNKNOWN")
        self.assertIn("--channel", row(report, "03")["explanation"])
        # A drill posted before the running container was created cannot attest it (the 404 drill has no task).
        recreated = self.world()
        recreated.rows[0]["Created"] = recreated.rows[0]["State"]["StartedAt"] = "2026-09-12T10:48:30Z"
        report = recreated.report()
        self.assertEqual([row(report, key)["status"] for key in ("03", "09")], ["UNKNOWN", "UNKNOWN"])
        self.assertIn("refusal-4xx", row(report, "03")["explanation"])
        # Nor can a drill whose bound task was created before it.
        early_task = self.world()
        early_task.rows[0]["Created"] = early_task.rows[0]["State"]["StartedAt"] = "2026-09-12T10:00:10Z"
        explanation = row(early_task.report(), "03")["explanation"]
        self.assertIn("reserved-5xx", explanation)
        self.assertNotIn("refusal-4xx", explanation)

    def test_status_execution_04(self):
        self.assertEqual(self.world().state("04"), "PASS")
        wrong_message = self.world()
        wrong_message.threads[STATUS_TS][0]["text"] = "an unrelated post"
        self.assertEqual(wrong_message.state("04"), "FAIL")
        human = self.world()
        del human.threads[STATUS_TS][0]["bot_id"]
        self.assertEqual(human.state("04"), "FAIL")
        for status in ({"workflows": 1, "execution": None}, {"workflows": 2, "execution": status_fixture()["execution"]},
                       status_fixture(channel="COTHERCHAN1"), status_fixture(ok=False),
                       status_fixture(ts_value="1789207999.000100"), None, {"workflows": 1, "execution": {"data": "[{}"}}):
            case = self.world()
            case.status = status
            with self.subTest(status=status):
                self.assertEqual(case.state("04"), "UNKNOWN")
        incomplete = self.world()
        incomplete.failing.add(STATUS_TS)
        self.assertEqual(incomplete.state("04"), "UNKNOWN")
        edited = self.world()
        edited.threads[STATUS_TS][0]["edited"] = {"user": BOT, "ts": ts(90)}
        self.assertEqual(edited.state("04"), "UNKNOWN")
        inactive = self.world()
        inactive.status["execution"]["workflow_active"] = False
        self.assertEqual(inactive.state("04"), "UNKNOWN")
        # n8n resolving orchestrator to another container cannot attest the selected Director.
        detached = self.world()
        detached.rows[0]["NetworkSettings"]["Networks"] = {"other_default": {"NetworkID": "9" * 64,
                                                                             "Aliases": ["orchestrator"]}}
        self.assertEqual(detached.state("04"), "UNKNOWN")
        # The execution started at 09:59:00; a container created after it was not the poster.
        recreated = self.world()
        recreated.rows[0]["Created"] = recreated.rows[0]["State"]["StartedAt"] = "2026-09-12T09:59:30Z"
        report = recreated.report()
        self.assertEqual(row(report, "04")["status"], "UNKNOWN")
        self.assertIn("before the running Director", row(report, "04")["explanation"])

    def test_flatted_decoder_resolves_shared_and_cyclic_references(self):
        self.assertEqual(sc._flatted(flatted({"a": ["x", 1, None, {"b": "y"}]})), {"a": ["x", 1, None, {"b": "y"}]})
        cyclic = sc._flatted('[{"self":"0","name":"1"},"n"]')
        self.assertIs(cyclic["self"], cyclic)
        for bad in ('{"a":1}', "[]", '[{"a":"9"}]', '[{"a":"x"}]'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sc._flatted(bad)

    def test_status_workflow_saves_success_executions_for_check_04(self):
        workflow = json.loads((Path(__file__).resolve().parents[1] / "n8n/workflows/director-status.json").read_text(encoding="utf-8"))
        settings = workflow["settings"]
        self.assertEqual(settings["saveDataSuccessExecution"], "all")
        self.assertIs(settings["saveManualExecutions"], False)
        self.assertIs(settings["saveExecutionProgress"], False)
        self.assertEqual(workflow["name"], "Director Status")
        self.assertIn("Post Status", {node["name"] for node in workflow["nodes"]})

    def test_approval_restart_06_08(self):
        report = self.world().report()
        self.assertEqual([row(report, key)["status"] for key in ("06", "08")], ["PASS", "PASS"])
        redispatched = self.world()
        readback = redispatched.tasks[APPROVAL_TASK]["qualification_evidence"]
        for event in json.loads(json.dumps(readback["provider_attempts"])):
            event.update(attempt_ordinal=2, attempt_digest="sha256:" + "3" * 64,
                         recorded_at=event["recorded_at"].replace("10:00", "10:01"))
            readback["provider_attempts"].append(event)
        tf.recount(readback)
        self.assertEqual(redispatched.state("06"), "FAIL")
        early = self.world()
        early.add(approval_entry(decided=iso(1789210000 + 30002)))
        self.assertEqual(early.state("08"), "FAIL")
        impostor = self.world()
        impostor.threads[ts(30000)][2]["user"] = OWNER
        self.assertEqual(impostor.state("08"), "FAIL")
        edited_prompt = self.world()
        edited_prompt.threads[ts(30000)][2]["edited"] = {"user": BOT, "ts": ts(30020)}
        self.assertEqual(edited_prompt.state("08"), "UNKNOWN")
        unscoped = self.world()
        unscoped.tasks[APPROVAL_TASK]["qualification_evidence"]["slack_deliveries"][-1]["owner_command_id"] = None
        self.assertEqual(unscoped.state("08"), "UNKNOWN")
        # The decision must match a control-plane approval_resume receipt, re-read, not only the delivery row.
        unreceipted = self.world()
        unreceipted.consistency["tasks"][APPROVAL_TASK]["approval_receipts"] = []
        self.assertEqual(unreceipted.state("08"), "UNKNOWN")
        retimed = self.world()
        retimed.consistency["tasks"][APPROVAL_TASK]["approval_receipts"] = [approval_receipt(iso(1789210000 + 30011))]
        self.assertEqual(retimed.state("08"), "UNKNOWN")
        other_command = self.world()
        other_command.consistency["tasks"][APPROVAL_TASK]["approval_receipts"][0]["command_id_sha256"] = "0" * 64
        self.assertEqual(other_command.state("08"), "UNKNOWN")
        no_snapshot = self.world()
        no_snapshot.consistency = None
        self.assertEqual([row(no_snapshot.report(), key)["status"] for key in ("06", "08")], ["PASS", "UNKNOWN"])
        queued_only = self.world()
        queued_only.tasks[APPROVAL_TASK]["qualification_evidence"]["slack_deliveries"][-1]["created_at"] = (
            "2026-09-12T10:00:02+00:00")
        self.assertEqual(queued_only.state("06"), "UNKNOWN")
        late_start = self.world()
        late_start.rows[0]["State"]["StartedAt"] = "2026-09-12T10:30:00Z"
        self.assertEqual(late_start.state("06"), "UNKNOWN")
        # An approval task created before the running container cannot attest it.
        recreated = self.world()
        recreated.rows[0]["Created"] = recreated.rows[0]["State"]["StartedAt"] = "2026-09-12T09:00:40Z"
        report = recreated.report()
        self.assertEqual(row(report, "08")["status"], "UNKNOWN")
        self.assertIn("before the running Director container", row(report, "08")["explanation"])
        report = self.world().report(approval_task=None)
        self.assertEqual([row(report, key)["status"] for key in ("06", "08")], ["UNKNOWN", "UNKNOWN"])
        self.assertIn("--approval-task", row(report, "06")["explanation"])
        missing = self.world()
        del missing.tasks[APPROVAL_TASK]
        report = missing.report()
        self.assertEqual([row(report, key)["status"] for key in ("06", "08")], ["UNKNOWN", "UNKNOWN"])

    def test_generation_tree_must_match_the_runtime_label_and_commit(self):
        for mutate in (lambda w: w.rows[0]["Config"]["Labels"].update({"org.director.candidate.tree": "e" * 40}),
                       lambda w: w.consistency["generation"].update(candidate_tree="e" * 40),
                       lambda w: setattr(w, "commit_tree", "e" * 40)):
            world = self.world()
            mutate(world)
            with self.subTest(mutate=mutate):
                self.assertEqual(world.state("07"), "UNKNOWN")

    def test_generation_commit_must_match_the_runtime_label(self):
        world = self.world()
        world.consistency["generation"]["candidate_commit"] = OTHER
        report = world.report()
        for key in ("07", "11", "12", "17"):
            self.assertEqual(row(report, key)["status"], "UNKNOWN", key)
        self.assertIn("runtime image label", row(report, "07")["explanation"])

    def test_consistency_07(self):
        self.assertEqual(self.world().state("07"), "PASS")
        conflicted = self.world()
        conflicted.consistency["counts"]["projection_batch_digest_conflicts"] = 1
        self.assertEqual(conflicted.state("07"), "FAIL")
        blocked = self.world()
        blocked.payload["langgraph_primary"]["historical_parallel_plan_hold_count"] = 1
        self.assertEqual(blocked.state("07"), "FAIL")
        busy = self.world()
        busy.consistency["counts"]["live_writer_claims"] = 1
        self.assertEqual(busy.state("07"), "UNKNOWN")
        held = self.world()
        held.consistency["counts"]["pending_owner_decisions"] = 1
        self.assertEqual(held.state("07"), "UNKNOWN")
        for change in ({"generation": {**consistency_fixture()["generation"], "manifest_sha256": "0" * 64}},
                       {"intake_fencing_generation": 82}, {"counts": {"projection_batch_digest_conflicts": 0}}):
            case = self.world()
            case.consistency.update(change)
            with self.subTest(change=change):
                self.assertEqual(case.state("07"), "UNKNOWN")
        unavailable = self.world()
        unavailable.consistency = None
        report = unavailable.report()
        self.assertEqual(row(report, "07")["status"], "UNKNOWN")
        self.assertIn("consistency command", row(report, "07")["explanation"])

    def test_final_reply_drills_09(self):
        self.assertEqual(self.world().state("09"), "PASS")
        twice = self.world()
        twice.threads[ts(300)].append({"ts": ts(331), "thread_ts": ts(300), "user": BOT, "text": "DIR-LG-D0 — SUCCEEDED"})
        self.assertEqual(twice.state("09"), "FAIL")
        split = self.world()
        split.threads[ts(300)][-1]["text"] = "*DIR-LG-5E — REQUEST RECEIVED*"
        self.assertEqual(split.state("09"), "FAIL")
        unresolved = self.world()
        terminal = unresolved.tasks["DIR-LG-A0"]["qualification_evidence"]["slack_deliveries"][0]
        terminal.update(state="AMBIGUOUS", readback_attempts=2)
        self.assertEqual(unresolved.state("09"), "FAIL")
        acknowledged = self.world()
        acknowledged.tasks["DIR-LG-A0"]["qualification_evidence"]["slack_deliveries"][0]["acknowledged_at"] = "2026-09-12T10:00:30+00:00"
        self.assertEqual(acknowledged.state("09"), "UNKNOWN")
        unexercised = self.world()
        unexercised.threads[ts(300)].pop()
        self.assertEqual(unexercised.state("09"), "UNKNOWN")
        duplicated = self.world()
        duplicated.tasks["DIR-LG-A0"]["qualification_evidence"]["task_scope_counters"]["thread_tasks"] = 2
        self.assertEqual(duplicated.state("09"), "FAIL")
        edited_final = self.world()
        final = next(m for m in edited_final.threads[ts(400)] if m.get("ts") == ts(430))
        final["edited"] = {"user": BOT, "ts": ts(490)}
        self.assertEqual(edited_final.state("09"), "UNKNOWN")

    def test_ten_offline_trials_10(self):
        self.assertEqual(self.world().state("10"), "PASS")
        called = self.world()
        called.add(task_entry("DIR-LG-7A01", 1000, offline=False,
                              text=called.tasks["DIR-LG-7A01"]["intake"]["text"]))
        report = called.report()
        self.assertEqual(row(report, "10")["status"], "FAIL")
        self.assertIn("DIR-LG-7A01", row(report, "10")["explanation"])
        world = self.world()
        report = world.report(trials=world.trials[:9])
        self.assertEqual(row(report, "10")["status"], "UNKNOWN")
        late = self.world()
        late.rows[0]["Created"] = "2026-09-12T10:30:00Z"
        report = late.report()
        self.assertEqual(row(report, "10")["status"], "UNKNOWN")
        self.assertIn("runtime_sha", row(report, "10")["explanation"])
        wrong = self.world()
        wrong.tasks["DIR-LG-7A02"]["status"] = "FAILED"
        self.assertEqual(wrong.state("10"), "FAIL")
        # An edited terminal reply keeps its ts but not the text it was posted with.
        edited = self.world()
        final = next(m for m in edited.threads[ts(2000)] if m.get("ts") == ts(2030))
        final["edited"] = {"user": BOT, "ts": ts(2090)}
        self.assertNotEqual(edited.state("10"), "PASS")
        missing = self.world()
        missing.failing.add(missing.trials[3])
        self.assertEqual(missing.state("10"), "UNKNOWN")
        # Ten passing threads are not enough: they must be consecutive owner posts.
        skipped = self.world()
        skipped.history_extra.append({"ts": ts(1500), "user": OWNER, "text": "DIRECTOR another trial"})
        report = skipped.report()
        self.assertEqual(row(report, "10")["status"], "UNKNOWN")
        self.assertIn("consecutive", row(report, "10")["explanation"])
        shared_file = self.world()
        shared_file.history_extra.append({"ts": ts(1500), "user": OWNER, "subtype": "file_share", "text": "DIRECTOR x"})
        self.assertEqual(shared_file.state("10"), "UNKNOWN")
        # A deleted post leaves a tombstone that may be a skipped trial.
        deleted = self.world()
        deleted.history_extra.append({"ts": ts(1500), "user": "USLACKBOT", "subtype": "tombstone", "hidden": True,
                                      "text": "This message was deleted."})
        self.assertEqual(deleted.state("10"), "UNKNOWN")
        # Every trial must itself be a visible top-level post in the window.
        not_a_post = self.world()
        not_a_post.history_hidden.add(not_a_post.trials[4])
        self.assertEqual(not_a_post.state("10"), "UNKNOWN")
        chatter_from_bot = self.world()
        chatter_from_bot.history_extra.append({"ts": ts(1500), "user": BOT, "bot_id": "BBOT123", "text": "status"})
        self.assertEqual(chatter_from_bot.state("10"), "PASS")
        no_history = self.world()
        no_history.failing.add("history")
        self.assertEqual(no_history.state("10"), "UNKNOWN")

    def test_real_products_11(self):
        self.assertEqual(self.world().state("11"), "PASS")
        for lane, product in (("cursor", None), ("grok", None), ("openai", "xai-grok-cli")):
            case = self.world()
            readback = case.tasks[PAID_TASK]["qualification_evidence"]
            case.tasks[PAID_TASK]["qualification_evidence"] = json.loads(json.dumps(readback).replace('"claude"', f'"{lane}"'))
            if lane in case.captures["lanes"]:
                case.captures["lanes"][lane]["product"] = product
            with self.subTest(lane=lane):
                self.assertEqual(case.state("11"), "FAIL")
        def openai_lane(product, transport):
            case = self.world()
            readback = json.dumps(case.tasks[PAID_TASK]["qualification_evidence"])
            case.tasks[PAID_TASK]["qualification_evidence"] = json.loads(
                readback.replace('"claude"', '"openai"').replace('"native-command"', f'"{transport}"'))
            case.captures["lanes"]["openai"]["product"] = product
            return case.state("11")
        # The OpenAI capture must be the product the attempts' transport actually used.
        self.assertEqual(openai_lane("openai-codex", "native-command"), "PASS")
        self.assertEqual(openai_lane("openai-responses", "native-command"), "FAIL")
        self.assertEqual(openai_lane("openai-codex", "responses-api"), "FAIL")
        self.assertEqual(openai_lane("openai-responses", "responses-api"), "PASS")
        # Captures must be the ones that gated the paid attempts, under the running bindings.
        minted_later = self.world()
        minted_later.captures["lanes"]["claude"]["surfaces"]["runtime_adapter"]["observed_at"] = "2026-09-12T10:00:10+00:00"
        self.assertEqual(minted_later.state("11"), "UNKNOWN")
        expired_during = self.world()
        expired_during.captures["lanes"]["claude"]["surfaces"]["runtime_adapter"]["expires_at"] = "2026-09-12T10:00:20+00:00"
        self.assertEqual(expired_during.state("11"), "UNKNOWN")
        undated = self.world()
        del undated.captures["lanes"]["claude"]["surfaces"]["runtime_adapter"]["observed_at"]
        self.assertEqual(undated.state("11"), "UNKNOWN")
        recreated = self.world()
        recreated.rows[0]["Created"] = "2026-09-12T10:30:00Z"
        self.assertEqual(recreated.state("11"), "UNKNOWN")
        rebound = self.world()
        rebound.captures["provider_bindings_sha256"] = "0" * 64
        self.assertEqual(rebound.state("11"), "UNKNOWN")
        regenerated = self.world()
        regenerated.captures["generation_manifest_sha256"] = "0" * 64
        self.assertEqual(regenerated.state("11"), "UNKNOWN")
        # A capture that no longer verifies reports no product: stale evidence, not a substitution.
        unverified = self.world()
        unverified.captures["lanes"]["claude"]["surfaces"]["runtime_adapter"]["capture_verified"] = False
        unverified.captures["lanes"]["claude"]["product"] = None
        self.assertEqual(unverified.state("11"), "UNKNOWN")
        # A verified, bound capture that names no product is still a substitution.
        productless = self.world()
        productless.captures["lanes"]["claude"]["product"] = None
        self.assertEqual(productless.state("11"), "FAIL")
        report = self.world().report(paid_thread=None, paid_rubric_path=None)
        self.assertEqual(row(report, "11")["status"], "UNKNOWN")
        unavailable = self.world()
        unavailable.captures = None
        self.assertEqual(unavailable.state("11"), "UNKNOWN")

    def test_activation_captures_12(self):
        self.assertEqual(self.world().state("12"), "PASS")
        expiring = self.world()
        expiring.captures["lanes"]["openai"]["surfaces"]["runtime_adapter"]["expires_at"] = "2026-09-12T10:01:00+00:00"
        self.assertEqual(expiring.state("12"), "FAIL")
        for mutate in (lambda c: c["lanes"]["claude"]["surfaces"].update(app=None),
                       lambda c: c["lanes"]["grok"]["surfaces"]["app"].update(capture_verified=False),
                       lambda c: c["lanes"]["grok"]["surfaces"]["app"].update(stage="DRY_RUN_OK"),
                       lambda c: c["lanes"]["openai"].update(binding_digest="not-a-digest"),
                       lambda c: c.update(provider_bindings_sha256="0" * 64)):
            case = self.world()
            mutate(case.captures)
            self.assertEqual(case.state("12"), "FAIL")
        unbound = self.world()
        unbound.captures["generation_manifest_sha256"] = "0" * 64
        self.assertEqual(unbound.state("12"), "UNKNOWN")
        unavailable = self.world()
        unavailable.captures = None
        self.assertEqual(unavailable.state("12"), "UNKNOWN")
        refused = self.world()
        refused.payload["langgraph_primary"]["provider_activation"]["combined_live_ready"] = False
        self.assertEqual(refused.state("12"), "FAIL")

    def ledger_report(self, path):
        with patch.object(sc, "_run_json", side_effect=ValueError("offline")):
            return sc.score(SHA, sc.collect(SHA, now=NOW, ledger_path=path), now=NOW)

    def test_ledger_bounds_14(self):
        self.assertEqual(row(sc.score(SHA, now=NOW), "14")["scope"], "coordination")
        with tempfile.TemporaryDirectory() as folder, \
                patch("director_orchestrator.engineering_workload_ledger._now", return_value=NOW.isoformat()):
            capped = Path(folder) / "capped.sqlite3"
            ledger_fixture(capped)
            report = self.ledger_report(capped)
            self.assertEqual([row(report, key)["status"] for key in ("13", "14")], ["PASS", "PASS"])
            uncapped = Path(folder) / "uncapped.sqlite3"
            ledger_fixture(uncapped, first_bounds=lambda reference: {
                "timeout_seconds": 60, "call_limit": 1, "turn_limit": 1, "output_bytes_limit": 1000, "usd_limit": 1,
                "hard_usd_cap_verified": False, "unverified_billing_authorization": reference})
            report = self.ledger_report(uncapped)
            self.assertEqual([row(report, key)["status"] for key in ("13", "14")], ["PASS", "FAIL"])
            empty = Path(folder) / "empty.sqlite3"
            from director_orchestrator.engineering_workload_ledger import EngineeringWorkloadLedger
            reference = {"reference": "test-authorized-fixture", "sha256": "d" * 64}
            EngineeringWorkloadLedger(empty).bootstrap({
                "authorization": reference, "operator": "fixture operator", "cutover_at": (NOW - timedelta(hours=1)).isoformat(),
                "policy": {"kind": "canonical", "profile": "engineering"}, "history_mode": "prospective",
                "history_evidence": reference, "prior_history_unknown": True, "quiescent_cutover_verified": True,
                "all_future_assignments_required": True, "history": [], "routes": []})
            self.assertEqual(row(self.ledger_report(empty), "14")["status"], "UNKNOWN")
            missing = Path(folder) / "missing.sqlite3"
            self.assertEqual(row(self.ledger_report(missing), "14")["status"], "UNKNOWN")
            self.assertFalse(missing.exists())

    def test_monetary_15(self):
        self.assertEqual(self.world().state("15"), "PASS")
        over_budget = self.world()
        over_budget.consistency["tasks"][PAID_TASK]["budgets"][0]["day_held_microusd"] = 5000001
        self.assertEqual(over_budget.state("15"), "FAIL")
        over_monitor = self.world()
        over_monitor.payload["usage_monitor"]["daily_accounting"]["grok"]["reserved_cost_usd"] = 5.5
        self.assertEqual(over_monitor.state("15"), "FAIL")
        unsettled = self.world()
        unsettled.consistency["tasks"][PAID_TASK]["spend"][0]["actual_microusd"] = None
        self.assertEqual(unsettled.state("15"), "UNKNOWN")
        uncorrelated = self.world()
        uncorrelated.consistency["tasks"][PAID_TASK]["spend"][0]["request_correlated"] = False
        self.assertEqual(uncorrelated.state("15"), "UNKNOWN")
        unmonitored = self.world()
        del unmonitored.payload["usage_monitor"]
        self.assertEqual(unmonitored.state("15"), "UNKNOWN")
        # reported_cost_usd sums known costs only: attempts with unknown, unreserved cost are not zero.
        unpriced = self.world()
        unpriced.payload["usage_monitor"]["daily_accounting"]["grok"].update(
            request_count=3, cost_visibility_complete=False, reported_cost_usd=0.0, reserved_cost_usd=None)
        report = unpriced.report()
        self.assertEqual(row(report, "15")["status"], "UNKNOWN")
        self.assertIn("grok", row(report, "15")["explanation"])
        idle = self.world()
        idle.payload["usage_monitor"]["daily_accounting"]["grok"].update(
            request_count=0, cost_visibility_complete=False, reported_cost_usd=0.0, reserved_cost_usd=None)
        self.assertEqual(idle.state("15"), "PASS")
        self.assertEqual(self.world().state("15", paid_thread=None, paid_rubric_path=None), "UNKNOWN")
        unavailable = self.world()
        unavailable.consistency = None
        self.assertEqual(unavailable.state("15"), "UNKNOWN")

    def ci_report(self, jobs_by_run, paths=None, size=100, total=None, **options):
        with patch.object(sc, "_run_json", side_effect=gh_runs(jobs_by_run, paths, size=size, total=total)):
            return sc.score(SHA, sc.collect(SHA, now=NOW, github=True, **options), now=NOW)

    def test_ci_self_hosted_16(self):
        # Newest run: hosted billing stubs. Older run: registered self-hosted runner that executed.
        report = self.ci_report({2: ci_jobs(steps=False), 1: ci_jobs()})
        self.assertEqual(row(report, "16")["status"], "PASS")
        with tempfile.TemporaryDirectory() as folder:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            key = Ed25519PrivateKey.generate()
            public = Path(folder) / "signer.pub"
            public.write_text(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(), encoding="ascii")
            receipt = Path(folder) / "receipt.json"
            receipt.write_text(json.dumps(replica_receipt(key)))
            with patch.object(sc, "CI_REPLICA_KEY", public):
                failed = self.ci_report({2: ci_jobs(steps=False), 1: ci_jobs(failed=("Secret hygiene",))},
                                        ci_replica_receipt=receipt)
                # Page 1 (size 1) holds only another workflow's run; ci.yml ran and failed on page 2.
                paged = self.ci_report({2: ci_jobs(), 1: ci_jobs(failed=("Secret hygiene",))},
                                       paths={2: ".github/workflows/pr-added.yml"}, size=1, ci_replica_receipt=receipt)
                unbounded = self.ci_report({2: ci_jobs(steps=False)}, total=5000, size=1, ci_replica_receipt=receipt)
        self.assertEqual(row(failed, "16")["status"], "FAIL")
        self.assertEqual(row(paged, "16")["status"], "FAIL")
        self.assertEqual(row(unbounded, "16")["status"], "UNKNOWN")
        self.assertEqual(row(self.ci_report({1: ci_jobs(steps=False)}), "16")["status"], "FAIL")
        # A same-named executed job of another workflow never stands in for a stubbed ci.yml job.
        impostor = self.ci_report({2: ci_jobs(steps=False), 1: ci_jobs()}, paths={1: ".github/workflows/pr-added.yml"})
        self.assertEqual(row(impostor, "16")["status"], "FAIL")
        self.assertNotIn("Secret hygiene", row(impostor, "16")["evidence"][0]["facts"]["jobs"])
        failing_other = self.ci_report({2: ci_jobs(), 1: ci_jobs(failed=("Secret hygiene",))},
                                       paths={1: ".github/workflows/recovery-soak.yml"})
        self.assertEqual(row(failing_other, "16")["status"], "FAIL")
        with patch.object(sc, "_run_json", side_effect=ValueError("gh unavailable")):
            report = sc.score(SHA, sc.collect(SHA, now=NOW, github=True), now=NOW)
        self.assertEqual(row(report, "16")["status"], "UNKNOWN")

    def test_ci_replica_receipt_16(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        key = Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as folder:
            public = Path(folder) / "signer.pub"
            public.write_text(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex() + "\n", encoding="ascii")
            def verdict(document, key_path=public, compare=None):
                receipt = Path(folder) / "receipt.json"
                receipt.write_text(json.dumps(document))
                with patch.object(sc, "CI_REPLICA_KEY", key_path), \
                        patch.object(sc, "_run_json", side_effect=gh_runs({1: ci_jobs(steps=False)}, compare=compare)):
                    observed = sc.collect(SHA, now=NOW, github=True, ci_replica_receipt=receipt)
                return row(sc.score(SHA, observed, now=NOW), "16")
            passed = verdict(replica_receipt(key))
            self.assertEqual(passed["status"], "PASS")
            facts = passed["evidence"][0]["facts"]
            self.assertEqual(facts["base_sha"], OTHER)
            self.assertEqual(facts["lanes"]["ubuntu-replica"]["new_failure_count"], 0)
            self.assertEqual(len(facts["signer_key_sha256"]), 64)
            regressed = replica_receipt(key, head_failures=("tests.test_fixture.T.test_known", "tests.test_new.T.test_break"))
            self.assertEqual(verdict(regressed)["status"], "FAIL")
            forged = replica_receipt(key)
            forged["document"]["lanes"][0]["head_failures"] = []
            self.assertEqual(verdict(forged)["status"], "UNKNOWN")
            other_signer = replica_receipt(Ed25519PrivateKey.generate())
            self.assertEqual(verdict(other_signer)["status"], "UNKNOWN")
            one_lane = replica_receipt(key)
            one_lane["document"]["lanes"].pop()
            self.assertEqual(verdict(resign(key, one_lane))["status"], "UNKNOWN")
            future = replica_receipt(key)
            future["document"]["produced_at"] = (NOW + timedelta(hours=1)).isoformat()
            self.assertEqual(verdict(resign(key, future))["status"], "UNKNOWN")
            self.assertEqual(verdict(replica_receipt(key), key_path=Path(folder) / "missing.pub")["status"], "UNKNOWN")
            self.assertEqual(verdict(replica_receipt(key, candidate=OTHER))["status"], "UNKNOWN")
            self.assertEqual(verdict(replica_receipt(key, head_tests_run=12))["status"], "UNKNOWN")
            for status in ({f"{OTHER}...{SHA}": "diverged"}, {f"{OTHER}...{SHA}": "identical"},
                           {f"{OTHER}...main": "diverged"}):
                with self.subTest(status=status):
                    self.assertEqual(verdict(replica_receipt(key), compare=status)["status"], "UNKNOWN")
            # Some required jobs executed: the receipt cannot cover the ones that did not.
            receipt = Path(folder) / "partial.json"
            receipt.write_text(json.dumps(replica_receipt(key)))
            partial = [dict(job, steps=[]) if index % 2 else job for index, job in enumerate(ci_jobs())]
            with patch.object(sc, "CI_REPLICA_KEY", public):
                report = self.ci_report({1: partial}, ci_replica_receipt=receipt)
            self.assertEqual(row(report, "16")["status"], "FAIL")
            # Another workflow's executed same-named jobs are not executed ci.yml jobs: the receipt applies.
            with patch.object(sc, "CI_REPLICA_KEY", public):
                report = self.ci_report({2: ci_jobs(steps=False), 1: ci_jobs()}, paths={1: ".github/workflows/pr-added.yml"},
                                        ci_replica_receipt=receipt)
            self.assertEqual(row(report, "16")["status"], "PASS")

    def test_adopted_acceptance_17(self):
        self.assertEqual(self.world().state("17"), "PASS")
        for change in ({"independent_reviewer": "Author-Login"}, {"security_reviewer": "AUTHOR-LOGIN"}):
            case = self.world()
            case.consistency["generation"]["adoption"].update(change)
            with self.subTest(change=change):
                self.assertEqual(case.state("17"), "FAIL")
        other = self.world()
        other.consistency["generation"]["adoption"]["reviewed_source_commit"] = OTHER
        self.assertEqual(other.state("17"), "UNKNOWN")
        fork = self.world()
        fork.consistency["generation"]["adoption"]["repository"] = "someone-else/director-orchestrator"
        self.assertEqual(fork.state("17"), "UNKNOWN")
        provider = self.world()
        provider.consistency["generation"]["acceptance_mode"] = "provider-v2"
        self.assertEqual(provider.state("17"), "UNKNOWN")
        unavailable = self.world()
        unavailable.consistency = None
        self.assertEqual(unavailable.state("17"), "UNKNOWN")

    def test_paid_run_20(self):
        world = self.world()
        report = world.report()
        self.assertEqual(row(report, "20")["status"], "PASS")
        record = row(report, "20")["evidence"][0]["facts"]["records"][0]
        self.assertEqual((record["actual_cost_usd"], record["wall_seconds"]), (0.3, 30.0))
        expensive = self.world()
        expensive.consistency["tasks"][PAID_TASK]["spend"][0]["actual_microusd"] = 1200000
        self.assertEqual(expensive.state("20"), "FAIL")
        slow = self.world()
        slow.add(task_entry(PAID_TASK, 20000, offline=False, text=slow.paid_text, answer="The audit answer is 42",
                            final_offset=65))
        self.assertEqual(slow.state("20"), "FAIL")
        incorrect = self.world()
        incorrect.add(task_entry(PAID_TASK, 20000, offline=False, text=incorrect.paid_text, answer="No idea"))
        self.assertEqual(incorrect.state("20"), "FAIL")
        edited = self.world()
        final = next(m for m in edited.threads[ts(20000)] if m.get("ts") == ts(20030))
        final["edited"] = {"user": BOT, "ts": ts(20090)}
        self.assertEqual(edited.state("20"), "UNKNOWN")
        edited_receipt = self.world()
        edited_receipt.threads[ts(20000)][1]["edited"] = {"user": BOT, "ts": ts(20090)}
        self.assertEqual(edited_receipt.state("20"), "UNKNOWN")
        simulated = self.world()
        simulated.add(task_entry(PAID_TASK, 20000, text=simulated.paid_text))
        self.assertEqual(simulated.state("20"), "FAIL")
        unsettled = self.world()
        unsettled.consistency["tasks"][PAID_TASK]["spend"][0]["actual_microusd"] = None
        report = unsettled.report()
        self.assertEqual(row(report, "20")["status"], "UNKNOWN")
        self.assertIn("actual_cost_usd", row(report, "20")["explanation"])
        self.assertEqual(self.world().state("20", paid_rubric_path=None), "UNKNOWN")
        unreadable = self.world()
        unreadable.failing.add(ts(20000))
        self.assertEqual(unreadable.state("20"), "UNKNOWN")

    def test_every_source_unavailable_never_crashes_or_leaks(self):
        world = self.world()
        selections = dict(deployment=DEPLOYMENT, channel=CHANNEL_FIXTURE, gateway=GATEWAY, github=True,
                          ledger_path=world.root / "missing.sqlite3", security_scan_id="12345678-1234-1234-1234-123456789abc",
                          drill_threads={"refusal-4xx": ts(100), "reserved-5xx": ts(200)}, approval_task=APPROVAL_TASK,
                          trials=list(world.trials), rubric_path=world.rubric_path, paid_thread=ts(20000),
                          paid_rubric_path=world.paid_rubric_path, ci_replica_receipt=world.root / "missing-receipt.json")
        with patch.object(sc, "_run_bytes", side_effect=ValueError("fixture-secret-bot")), \
                patch.object(sc, "build_opener", side_effect=OSError("fixture-director-secret")), \
                patch.object(sc, "_native_security_index", return_value=world.root / "missing-index.sqlite3"):
            report = sc.score(SHA, sc.collect(SHA, now=NOW, **selections), now=NOW)
        self.assertEqual(len(report["checks"]), 20)
        self.assertEqual(report["passed"], 0)
        # The runtime answers, but every other source is down or refuses.
        world.consistency = world.captures = world.status = None
        world.slack_down = True
        with patch.object(sc, "_native_security_index", return_value=world.root / "missing-index.sqlite3"):
            observations = world.observe(**{key: value for key, value in selections.items() if key != "deployment"})
        report = sc.score(SHA, observations, now=NOW)
        self.assertEqual(len(report["checks"]), 20)
        self.assertEqual({item["id"] for item in report["checks"] if item["status"] == "PASS"}, {"05", "19"})
        for key in ("03", "04", "06", "08", "09", "10", "11", "20"):
            self.assertIn("--channel", row(report, key)["explanation"])
        self.assertEqual(len({item.check_id for item in observations}), len(observations))
        text = json.dumps(report)
        for secret in (*SECRETS, CHANNEL_FIXTURE, OWNER, BOT):
            self.assertNotIn(secret, text)

    def test_cli_live_requires_deployment(self):
        script = Path(__file__).resolve().parents[1] / "scripts/director-scorecard.py"
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(script), "--candidate-sha", SHA, "--output-dir", folder, "--live"],
                                    capture_output=True, timeout=15, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--live requires --deployment", result.stderr)
            self.assertFalse((Path(folder) / "completion-scorecard.json").exists())

    def test_cli_new_flags_require_live(self):
        script = Path(__file__).resolve().parents[1] / "scripts/director-scorecard.py"
        with tempfile.TemporaryDirectory() as folder:
            for flag in (["--deployment", DEPLOYMENT], ["--channel", CHANNEL_FIXTURE], ["--drill-thread", "refusal-4xx=" + ts(1)],
                         ["--approval-task", APPROVAL_TASK], ["--trial", ts(1)], ["--rubric", "rubric.json"],
                         ["--paid-thread", ts(1)], ["--paid-rubric", "paid.json"], ["--ci-replica-receipt", "receipt.json"]):
                with self.subTest(flag=flag):
                    result = subprocess.run([sys.executable, str(script), "--candidate-sha", SHA, "--output-dir", folder, *flag],
                                            capture_output=True, timeout=15, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("require --live", result.stderr)
            duplicate = subprocess.run([sys.executable, str(script), "--candidate-sha", SHA, "--output-dir", folder, "--live",
                                        "--deployment", DEPLOYMENT, "--drill-thread", "refusal-4xx=" + ts(1),
                                        "--drill-thread", "refusal-4xx=" + ts(2)], capture_output=True, timeout=15, text=True)
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("--drill-thread", duplicate.stderr)

if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | END]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:36 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | END]]
# [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:09 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:47 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:58 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:17 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:50 UTC | END]]
