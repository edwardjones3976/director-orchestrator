# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]]
import dataclasses
import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from director_orchestrator import completion_scorecard as sc


SHA = "a" * 40
OTHER = "b" * 40
IMAGE = "sha256:" + "c" * 64
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
GATEWAY = "director-orchestrator-slackbot-release-879c4eb4a375-pre193-02"
OLD_GATEWAY = "director-orchestrator-slackbot-explicit-alias"


def containers():
    return [{"Config": {"Labels": {"org.director.candidate.commit": SHA}}, "Image": IMAGE,
             "NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}}}]


def health():
    return {"environment": "local", "paid_runs_enabled": True, "langgraph_primary": {
        "new_intake_owner": "langgraph", "legacy_new_intake_disabled": True,
        "intake_fencing_generation": 83, "ambiguous_effect_count": 0,
        "historical_parallel_plan_hold_count": 0, "reconciliation_worker_ready": True,
        "reconciliation_hold_reason": None,
        "primary_runtime_active": True, "effect_executor_ready": True,
        "provider_activation": {"combined_live_ready": True}}}


def runtime_observations(payload=None):
    # Substitute external I/O, not the score's trust check or predicates.
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.geturl.return_value = sc.HEALTH_URL
    response.read.return_value = json.dumps(payload if payload is not None else health()).encode()
    with patch.object(sc, "_run_json", return_value=containers()), patch.object(sc, "build_opener") as opener:
        opener.return_value.open.return_value = response
        return sc.collect(SHA, now=NOW)


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


def topology():
    api = containers()[0]
    api.update(Id="1" * 64, Name="/director-orchestrator-orchestrator-1", State={"Running": True})
    api["Config"].update(Cmd=["uvicorn", "director_orchestrator.main:app"], Env=["SLACK_APP_TOKEN=fixture-app-token"])
    gateway = {"Id": "2" * 64, "Image": IMAGE, "Name": "/" + GATEWAY, "State": {"Running": True},
               "Config": {"Cmd": ["python", "-m", "director_orchestrator.slack_bot"],
                          "Env": ["SLACK_APP_TOKEN=fixture-app-token", "SLACK_BOT_TOKEN=fixture-secret-bot",
                                  "SLACK_DIRECTOR_CHANNEL_ID=C0BQ6GSCNLR", "N8N_DIRECTOR_WEBHOOK_URL=http://n8n:5678/webhook/director-intake"]}}
    n8n = {"Id": "3" * 64, "Name": "/director-orchestrator-n8n-1", "State": {"Running": True},
           "NetworkSettings": {"Ports": {"5678/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5678"}]}}, "Config": {}}
    return [api, gateway, n8n]


def collect_topology(rows, host=None, upstream="local", workflow=None, gateway=GATEWAY, later_rows=None):
    host = {"inspected": 5, "unreadable": 0, "gateway_process_count": 0} if host is None else host
    workflow = {"active": True, "active_version": "active-version", "stored_version": "active-version", "http_node_count": 1,
                "supported_request": True, "static_request": True, "supported_webhook": True, "direct_connection": True} if workflow is None else workflow
    listings = [rows, rows if later_rows is None else later_rows]
    def process(args):
        if args[:2] == ["docker", "ps"]:
            return "\n".join(row["Id"] for row in listings.pop(0)).encode()
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(rows).encode()
        if args[:2] == ["docker", "exec"]:
            if args[2] == "director-orchestrator-n8n-db-1":
                return json.dumps(workflow).encode()
            return json.dumps({"environment": upstream}).encode()
        if isinstance(host, Exception):
            raise host
        return json.dumps(host).encode()
    with patch.object(sc, "_run_bytes", side_effect=process):
        observations = sc._topology_observations(NOW, {"runtime_sha": SHA, "image_id": IMAGE}, gateway)
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


class CompletionScorecardTests(unittest.TestCase):
    def test_zero_hold_counters_are_not_reconciliation_proof(self):
        # Release 879 reported these exact zeros while authoritative plan digests conflicted.
        payload = health()
        payload["langgraph_primary"]["reconciliation_worker_ready"] = False
        report = sc.score(SHA, runtime_observations(payload), now=NOW)
        self.assertEqual(report["checks"][6]["status"], "UNKNOWN")
        self.assertIn("Missing collector", report["checks"][6]["explanation"])
        self.assertEqual(report["checks"][18]["status"], "FAIL")
        self.assertIn("negative evidence for 07", json.dumps(report["limitations"]))

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
        for name in (OLD_GATEWAY, "director-orchestrator-orchestrator-1", "director-orchestrator-n8n-1"):
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
                rows[2]["NetworkSettings"]["Ports"]["5678/tcp"][0]["HostPort"] = "45678"
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
            with patch.object(sc, "_native_security_index", return_value=index), patch.object(sc, "_run_bytes", return_value=b"src/thing.py\0"):
                observation = sc._security_observation(scan_id, SHA, NOW)
                self.assertEqual(sc.score(SHA, [observation], now=NOW)["checks"][17]["status"], "PASS")
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
            with patch.object(sc, "_native_security_index", return_value=index), patch.object(sc, "_run_bytes", return_value=b"src/thing.py\0src/unreviewed.py\0"):
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
        data[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"] = "18082"
        with patch.object(sc, "_run_json", return_value=data):
            report = sc.score(SHA, sc.collect(SHA, now=NOW), now=NOW)
        self.assertEqual(report["passed"], 0)

    def test_command_failure_does_not_disclose_secret_error_or_retry(self):
        with patch.object(sc, "_run_json", side_effect=ValueError("secret-token-should-not-display")) as query:
            report = sc.score(SHA, sc.collect(SHA, now=NOW), now=NOW)
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
                return {"jobs": jobs}
            self.assertIn("head_sha=" + SHA, args[-1])
            return {"workflow_runs": [{"id": 123}]}
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
            return {"jobs": []} if "/jobs?" in args[-1] else {"workflow_runs": [{"id": 1}]}
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
        self.assertIn("Next: Collect fresh generation-bound consistency proof over durable projections, "
                      "batches, assignments and unresolved effects from an authoritative collector. "
                      "This collector does not exist yet.", text)

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


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | END]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 22:36 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | END]]
