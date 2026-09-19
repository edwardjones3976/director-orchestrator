# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | BEGIN]]
"""scripts/zai-trial.py: the operator boundary between the deployment .env, the trial ledger and stdout.

Subprocess cases exercise the real entry point with a scrubbed environment (Settings reads the
environment at import, so in-process runs cannot test env-file loading). In-process cases patch
Settings and the lane to prove the dispatch output paths without any provider contact.
"""
import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.zai_lane import LEDGER_VERSION, ZaiTrialDispatch, ZaiTrialLedger
from director_orchestrator.zai_trial import ENDPOINT

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zai-trial.py"
SECRET = "test.secret.value"


def run_cli(*args, env_file=None, env=None):
    environment = {name: value for name, value in os.environ.items() if not name.startswith("ZAI_")}
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment.update(env or {})
    command = [sys.executable, str(SCRIPT)]
    if env_file is not None:
        command += ["--env-file", str(env_file)]
    command += list(args)
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180, env=environment)
    return completed.returncode, completed.stdout, completed.stderr


def work_packet() -> dict:
    work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text(encoding="utf-8"))
    work.update(allowed_tools=[], allowed_external_effects=[])
    work["limits"].update(budget_usd=0, timeout_seconds=60, retry_limit=1)
    return work


class CliSubprocessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sqlite = self.root / "director.sqlite3"
        self.ledger_path = self.root / "ledger.sqlite3"
        self.env = {"SQLITE_PATH": str(self.sqlite)}

    def env_file(self, *lines) -> Path:
        path = self.root / "trial.env"
        path.write_text(os.linesep.join(lines) + os.linesep, encoding="utf-8")
        return path

    def _refused(self, code, out, err):
        self.assertEqual(code, 2, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "REFUSED")
        self.assertNotIn("Traceback", err)
        return payload["reason"]

    def test_status_prints_booleans_and_never_the_key(self):
        code, out, err = run_cli("status", env_file=self.env_file("ZAI_API_KEY=" + SECRET, "ZAI_MODE=trial_only"),
                                 env=self.env)
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertTrue(all(isinstance(value, bool) for value in payload["readiness"].values()))
        self.assertTrue(payload["readiness"]["credential_configured"])
        self.assertTrue(payload["readiness"]["ledger_isolated"])
        self.assertIsNone(payload["ledger"])
        self.assertNotIn(SECRET, out + err)

    def test_status_reports_the_ledger_without_its_path(self):
        code, out, err = run_cli("status", env_file=self.env_file("ZAI_TRIAL_LEDGER_PATH=" + str(self.ledger_path)),
                                 env=self.env)
        self.assertEqual(code, 0, out + err)
        ledger = json.loads(out)["ledger"]
        self.assertEqual(ledger["version"], LEDGER_VERSION)
        self.assertTrue(ledger["route_available"])
        self.assertIs(ledger["production_credit_issued"], False)
        self.assertNotIn(str(self.ledger_path), out)
        self.assertTrue(self.ledger_path.exists())

    def test_widened_configuration_is_refused_as_json(self):
        reason = self._refused(*run_cli("status", env_file=self.env_file("ZAI_MODE=production"), env=self.env))
        self.assertIn("ZAI_MODE", reason)

    def test_ledger_path_naming_the_taskstore_is_refused_before_any_write(self):
        reason = self._refused(*run_cli("status", env_file=self.env_file("ZAI_TRIAL_LEDGER_PATH=" + str(self.sqlite)),
                                        env=self.env))
        self.assertIn("ZAI_TRIAL_LEDGER_PATH", reason)
        self.assertNotIn(str(self.sqlite), reason)
        self.assertFalse(self.sqlite.exists())

    def test_foreign_ledger_file_is_refused_and_left_alone(self):
        import sqlite3

        db = sqlite3.connect(self.ledger_path)
        try:
            db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
            db.commit()
        finally:
            db.close()
        before = self.ledger_path.read_bytes()
        reason = self._refused(*run_cli("status", env_file=self.env_file("ZAI_TRIAL_LEDGER_PATH=" + str(self.ledger_path)),
                                        env=self.env))
        self.assertEqual(reason, "zai_ledger_not_a_trial_ledger")
        self.assertEqual(self.ledger_path.read_bytes(), before)

    def test_resolve_and_evaluate_paths(self):
        reason = self._refused(*run_cli("resolve", "--attempt", "x", "--reason", "killed",
                                        env_file=self.env_file("ZAI_MODE=trial_only"), env=self.env))
        self.assertIn("ZAI_TRIAL_LEDGER_PATH", reason)
        ledger = ZaiTrialLedger(str(self.ledger_path))
        attempt = ledger.reserve(assignment_id="A-1", task_id="T-1", work_packet_digest="sha256:" + "a" * 64,
                                 idempotency_key="k-1", model="glm-5.3-flash", purpose="cli test")
        env_file = self.env_file("ZAI_TRIAL_LEDGER_PATH=" + str(self.ledger_path))
        code, out, err = run_cli("resolve", "--attempt", attempt, "--reason", "host rebooted mid-call",
                                 env_file=env_file, env=self.env)
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "RECORDED")
        self.assertEqual(payload["ledger"]["recorded_failures"], 1)
        row = ledger.attempts()[0]
        self.assertEqual(row["status"], "AMBIGUOUS")
        self.assertTrue(row["error_type"].startswith("operator_resolved:"))
        reason = self._refused(*run_cli("resolve", "--attempt", attempt, "--reason", "again",
                                        env_file=env_file, env=self.env))
        self.assertEqual(reason, "zai_outcome_already_recorded")
        evaluate = ("evaluate", "--attempt", attempt, "--correctness", "unverified",
                    "--privacy-fit", "unassessed", "--usefulness", "unusable")
        reason = self._refused(*run_cli(*evaluate, "--evaluator", "GLM-5.3 self check",
                                        env_file=env_file, env=self.env))
        self.assertEqual(reason, "zai_cannot_evaluate_its_own_work")
        code, out, err = run_cli(*evaluate, "--evaluator", "Claude Fable 5.1", "--notes", "checked by hand",
                                 env_file=env_file, env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["ledger"]["evaluated"], 1)
        self.assertNotIn("checked by hand", out)
        reason = self._refused(*run_cli(*evaluate, "--evaluator", "Claude Fable 5.1",
                                        env_file=env_file, env=self.env))
        self.assertEqual(reason, "zai_evaluation_already_recorded")
        reason = self._refused(*run_cli("evaluate", "--attempt", "unknown", "--evaluator", "someone",
                                        "--correctness", "correct", "--privacy-fit", "fit", "--usefulness", "accepted",
                                        env_file=env_file, env=self.env))
        self.assertEqual(reason, "zai_evaluation_requires_recorded_outcome")

    def test_dispatch_refuses_bad_packets_and_a_closed_lane_before_any_contact(self):
        broken = self.root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        env_file = self.env_file("ZAI_TRIAL_LEDGER_PATH=" + str(self.ledger_path))
        reason = self._refused(*run_cli("dispatch", "--work-packet", str(broken), "--purpose", "p",
                                        env_file=env_file, env=self.env))
        self.assertIn("JSONDecodeError", reason)
        packet = self.root / "work.json"
        packet.write_text(json.dumps(work_packet()), encoding="utf-8")
        reason = self._refused(*run_cli("dispatch", "--work-packet", str(packet), "--purpose", "p",
                                        env_file=env_file, env=self.env))
        self.assertIn("trial_enabled", reason)
        self.assertEqual(ZaiTrialLedger(str(self.ledger_path)).attempts(), [])


class FakeLane:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def dispatch(self, work, *, context, purpose, recovery_basis=None):
        self.calls.append((work["assignment_id"], context.idempotency_key, purpose, recovery_basis))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


class CliDispatchOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet = self.root / "work.json"
        self.packet.write_text(json.dumps(work_packet()), encoding="utf-8")
        self.settings = replace(
            Settings(), zai_trial_enabled=True, zai_api_key=SECRET, zai_api_base=ENDPOINT, zai_mode="trial_only",
            zai_model="glm-5.3-flash", zai_cli_executable=sys.executable,
            zai_trial_journal_dir=str(self.root / "journal"), zai_trial_ledger_path=str(self.root / "ledger.sqlite3"),
            zai_lite_entitlement_verified=True, zai_timeout_seconds=60, zai_allow_repository_write=False,
            zai_allow_review_verdict=False, zai_production_activation="", sqlite_path=str(self.root / "director.sqlite3"),
        )
        self.main = runpy.run_path(str(SCRIPT), run_name="zai_trial_cli")["main"]

    def _run(self, lane):
        out = io.StringIO()
        with patch("director_orchestrator.config.Settings", lambda: self.settings), \
             patch("director_orchestrator.zai_lane.ZaiTrialLane.from_settings", return_value=lane), \
             contextlib.redirect_stdout(out):
            code = self.main(["dispatch", "--work-packet", str(self.packet), "--purpose", "output test",
                              "--recovery-basis", "provider 503 at 12:00"])
        return code, out.getvalue()

    def test_dispatch_output_is_redacted_and_carries_no_credit(self):
        lane = FakeLane(ZaiTrialDispatch(attempt_id="a" * 64, latency_ms=7, result={
            "status": "SUCCEEDED", "outcome": {"summary": "the key was " + SECRET, "nested": ["x " + SECRET]},
        }))
        code, out = self._run(lane)
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "DISPATCHED")
        self.assertEqual(payload["attempt_id"], "a" * 64)
        self.assertIs(payload["production_credit"], False)
        self.assertEqual(payload["outcome"]["summary"], "the key was [REDACTED]")
        self.assertNotIn(SECRET, out)
        self.assertEqual(lane.calls[0][2:], ("output test", "provider 503 at 12:00"))

    def test_dispatch_failure_reports_the_type_only(self):
        code, out = self._run(FakeLane(RuntimeError("provider said " + SECRET)))
        self.assertEqual(code, 1, out)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertNotIn(SECRET, out)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | END]]
