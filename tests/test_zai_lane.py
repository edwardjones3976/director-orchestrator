# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:20 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 02:55 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | BEGIN]]
import json
import os
import re
import sqlite3
import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.native_acceptance_issuer import IssuerError
from director_orchestrator.packet_contracts import PacketContractError, document_digest
from director_orchestrator.zai_lane import (
    LEDGER_VERSION,
    MAX_RECORDED_FAILURES,
    ZAI_CONTACT_LANE,
    ZaiTrialLane,
    ZaiTrialLaneUnavailable,
    ZaiTrialLedger,
    ZaiTrialLedgerError,
    attempt_id_for,
    is_self_evaluator,
)
from director_orchestrator.zai_trial import ENDPOINT, TRIAL_END, ZaiLiteTrialTransport

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test.secret"
BEFORE_END = TRIAL_END - timedelta(days=1)


def work_packet(assignment="A-1", key="k-1", **limits) -> dict:
    work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text(encoding="utf-8"))
    work.update(assignment_id=assignment, idempotency_key=key, allowed_tools=[], allowed_external_effects=[])
    work["limits"].update(budget_usd=0, timeout_seconds=60, retry_limit=1)
    work["limits"].update(limits)
    return work


def _context(work: dict, **overrides) -> ProviderAdapterContext:
    fields = dict(
        task_id=work["task_id"], assignment_id=work["assignment_id"], idempotency_key=work["idempotency_key"],
        work_packet_digest=document_digest(work), writer_token_digest="sha256:" + "0" * 64,
        timeout_seconds=60, budget_usd=0, max_hops=1, max_revisions=1,
        allowed_tools=(), allowed_external_effects=(),
    )
    fields.update(overrides)
    return ProviderAdapterContext(**fields)


class FakeAdapter:
    provider = "zai"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    def dispatch(self, work_packet, *, context):
        self.calls += 1
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


def closed_settings() -> Settings:
    """A closed lane built explicitly, so the host environment cannot open it."""
    return replace(
        Settings(), zai_trial_enabled=False, zai_api_key="", zai_api_base=ENDPOINT, zai_mode="trial_only",
        zai_model="glm-5.3-flash", zai_cli_executable="", zai_trial_journal_dir="", zai_trial_ledger_path="",
        zai_timeout_seconds=60, zai_lite_entitlement_verified=False, zai_production_activation="",
        zai_allow_repository_write=False, zai_allow_review_verdict=False,
    )


class IdentityTests(unittest.TestCase):
    def test_contact_lane_holds_no_authority_and_names_product_and_family(self):
        lane = ZAI_CONTACT_LANE.as_dict()
        self.assertEqual(lane["lane_id"], "zai")
        self.assertEqual(lane["provider_family"], "zai")
        self.assertNotEqual(lane["product"], lane["provider_family"])
        self.assertEqual(lane["endpoint"], ENDPOINT)
        for flag in ("production_lane", "production_credit", "review_authority", "merge_authority"):
            self.assertIs(lane[flag], False, flag)

    def test_zai_is_absent_from_every_production_and_review_enumeration(self):
        # This absence is the structural guarantee: no admission, credit,
        # review verdict, committee vote or reviewer independence can name zai.
        from director_orchestrator import engine, langgraph_postgres, team_charter
        from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES, LANES
        from director_orchestrator.interactions import InteractionProvider, ProviderTransport
        from director_orchestrator.models import ProviderName
        from director_orchestrator.native_v3_review_issuer import QUALIFIED_PRODUCTS
        from director_orchestrator.primary_roster import KNOWN_LANES, REVIEWERS
        from director_orchestrator.primary_transports import PRIMARY_PROVIDER_LANES
        from director_orchestrator.workload_policy import DirectProductionProvider, Provider

        enumerations = {
            "ProviderName": {member.value for member in ProviderName},
            "Provider": {member.value for member in Provider},
            "DirectProductionProvider": {member.value for member in DirectProductionProvider},
            "LANES": set(LANES),
            "PRIMARY_PROVIDER_LANES": set(PRIMARY_PROVIDER_LANES),
            "KNOWN_LANES": set(KNOWN_LANES),
            "REVIEWERS": set(REVIEWERS),
            "_TRUSTED_PRIMARY_REVIEWERS": {
                getattr(member, "value", member) for member in engine._TRUSTED_PRIMARY_REVIEWERS
            },
            "QUALIFIED_PRODUCTS": set(QUALIFIED_PRODUCTS),
            "InteractionProvider": {member.value for member in InteractionProvider},
            "ProviderTransport": {member.value for member in ProviderTransport},
            "committee": set(team_charter._TEAM_OPERATING_CHARTER["committee"]["members"]),
        }
        for name, values in enumerations.items():
            self.assertNotIn("zai", {str(value).lower() for value in values}, name)
        for profile, targets in ALLOCATION_PROFILES.items():
            self.assertNotIn("zai", targets, profile)
        # The Postgres admission ledger's lane allowlist is literal SQL, not an enum.
        ddl = Path(langgraph_postgres.__file__).read_text(encoding="utf-8")
        literals = re.findall(r"provider_lane\s+IN\s*\(([^)]*)\)", ddl)
        self.assertTrue(literals, "expected at least one provider_lane IN (...) literal")
        for literal in literals:
            lanes = {item.strip().strip("'") for item in literal.split(",")}
            self.assertNotIn("zai", lanes)
            self.assertEqual(lanes, set(PRIMARY_PROVIDER_LANES))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ready = replace(
            closed_settings(),
            zai_trial_enabled=True, zai_api_key=SECRET, zai_cli_executable=sys.executable,
            zai_trial_journal_dir=str(root / "journal"), zai_trial_ledger_path=str(root / "ledger.sqlite3"),
            zai_lite_entitlement_verified=True,
        )

    def test_defaults_fail_closed_and_stay_boolean(self):
        settings = closed_settings()
        readiness = settings.zai_trial_readiness(moment=BEFORE_END)
        self.assertTrue(all(isinstance(value, bool) for value in readiness.values()))
        self.assertFalse(readiness["trial_enabled"])
        self.assertFalse(readiness["credential_configured"])
        self.assertTrue(readiness["official_endpoint"])
        self.assertTrue(readiness["trial_only_mode"])
        self.assertTrue(readiness["production_activation_absent"])
        self.assertIs(settings.readiness()["zai"], False)
        settings.validate_zai_trial_configuration()  # a closed lane is a valid configuration
        # The shipped defaults are the closed lane above, whatever the host exports.
        for field in ("zai_trial_enabled", "zai_lite_entitlement_verified", "zai_allow_repository_write",
                      "zai_allow_review_verdict"):
            self.assertIs(Settings.__dataclass_fields__[field].default, False, field)
        self.assertEqual(Settings.__dataclass_fields__["zai_mode"].default, "trial_only")
        self.assertEqual(Settings.__dataclass_fields__["zai_api_base"].default, ENDPOINT)

    def test_fully_configured_lane_is_ready_only_inside_the_trial_window(self):
        self.assertTrue(all(self.ready.zai_trial_readiness(moment=BEFORE_END).values()))
        at_end = self.ready.zai_trial_readiness(moment=TRIAL_END)
        self.assertFalse(at_end["trial_window_open"])
        self.assertTrue(all(value for name, value in at_end.items() if name != "trial_window_open"))
        self.assertNotIn(SECRET, json.dumps(self.ready.zai_trial_readiness(moment=BEFORE_END)))

    def test_each_widening_setting_fails_startup_validation(self):
        widenings = {
            "ZAI_MODE": dict(zai_mode="production"),
            "ZAI_ALLOW_REPOSITORY_WRITE": dict(zai_allow_repository_write=True),
            "ZAI_ALLOW_REVIEW_VERDICT": dict(zai_allow_review_verdict=True),
            "ZAI_PRODUCTION_ACTIVATION": dict(zai_production_activation="sha256:" + "f" * 64),
            "ZAI_API_BASE": dict(zai_api_base="https://api.anthropic.com"),
            "ZAI_TRIAL_ENABLED": dict(zai_lite_entitlement_verified=False),
            "ZAI_TRIAL_LEDGER_PATH": dict(zai_trial_ledger_path=self.ready.sqlite_path),
        }
        for variable, change in widenings.items():
            with self.subTest(variable=variable):
                widened = replace(self.ready, **change)
                with self.assertRaises(ValueError) as caught:
                    widened.validate_zai_trial_configuration()
                self.assertIn(variable, str(caught.exception))
                self.assertFalse(all(widened.zai_trial_readiness(moment=BEFORE_END).values()))
        self.ready.validate_zai_trial_configuration()

    def test_individual_readiness_checks_close_the_lane(self):
        cases = {
            "credential_configured": dict(zai_api_key="has space"),
            "glm_model_configured": dict(zai_model="claude-opus"),
            "cli_configured": dict(zai_cli_executable="relative/claude"),
            "journal_configured": dict(zai_trial_journal_dir=""),
            "ledger_configured": dict(zai_trial_ledger_path=""),
            "ledger_isolated": dict(zai_trial_ledger_path=self.ready.langgraph_checkpoint_path),
            "timeout_valid": dict(zai_timeout_seconds=70),
            "lite_entitlement_verified": dict(zai_lite_entitlement_verified=False),
        }
        for check, change in cases.items():
            with self.subTest(check=check):
                readiness = replace(self.ready, **change).zai_trial_readiness(moment=BEFORE_END)
                self.assertFalse(readiness[check])
                self.assertFalse(all(readiness.values()))

    def test_ledger_path_must_be_its_own_file_even_through_a_link(self):
        root = Path(self.tmp.name)
        taskstore = root / "director.sqlite3"
        taskstore.write_bytes(b"")
        link = root / "ledger-link.sqlite3"
        try:
            os.link(taskstore, link)
        except OSError as exc:  # filesystem without hard links
            self.skipTest(f"hard links unavailable here: {type(exc).__name__}")
        colliding = replace(self.ready, sqlite_path=str(taskstore), zai_trial_ledger_path=str(link))
        self.assertFalse(colliding.zai_trial_readiness(moment=BEFORE_END)["ledger_isolated"])
        with self.assertRaises(ValueError) as caught:
            colliding.validate_zai_trial_configuration()
        self.assertIn("ZAI_TRIAL_LEDGER_PATH", str(caught.exception))
        self.assertNotIn(self.tmp.name, str(caught.exception))
        for store in ("langgraph_runtime_ledger_path", "completion_gate_store_path"):
            with self.subTest(store=store):
                same = replace(self.ready, **{store: self.ready.zai_trial_ledger_path})
                self.assertFalse(same.zai_trial_readiness(moment=BEFORE_END)["ledger_isolated"])
        separate = replace(self.ready, sqlite_path=str(taskstore), zai_trial_ledger_path=str(root / "own.sqlite3"))
        self.assertTrue(separate.zai_trial_readiness(moment=BEFORE_END)["ledger_isolated"])
        separate.validate_zai_trial_configuration()

    def test_health_exposes_booleans_only_for_the_trial_lane(self):
        import importlib

        from fastapi.testclient import TestClient

        main_module = importlib.import_module("director_orchestrator.main")
        with patch("director_orchestrator.main.primary_default_activation", None), \
             patch("director_orchestrator.main.settings", self.ready):
            payload = TestClient(main_module.app).get("/health").json()
        self.assertIn("zai", payload["providers"])
        self.assertIsInstance(payload["providers"]["zai"], bool)
        self.assertTrue(all(isinstance(value, bool) for value in payload["zai_trial"].values()))
        self.assertNotIn(SECRET, json.dumps(payload))
        self.assertNotIn(self.tmp.name, json.dumps(payload))
        self.assertNotIn(ENDPOINT, json.dumps(payload))

    def test_transport_accepts_the_settings_credential_without_an_env_file(self):
        transport = ZaiLiteTrialTransport(
            executable=Path(sys.executable), journal=Path(self.tmp.name) / "j",
            model="glm-5.3-flash", lite_verified=True, api_key=SECRET,
        )
        self.assertEqual(transport.key, SECRET)
        with self.assertRaises(ValueError):
            ZaiLiteTrialTransport(
                executable=Path(sys.executable), journal=Path(self.tmp.name) / "j",
                model="glm-5.3-flash", lite_verified=True,
            )
        with self.assertRaises(ValueError):
            ZaiLiteTrialTransport(
                executable=Path(sys.executable), journal=Path(self.tmp.name) / "j",
                model="glm-5.3-flash", lite_verified=True, api_key="with space",
            )

    def test_from_settings_refuses_a_closed_lane_and_builds_an_open_one(self):
        with self.assertRaises(ZaiTrialLaneUnavailable) as caught:
            ZaiTrialLane.from_settings(closed_settings(), moment=BEFORE_END)
        self.assertIn("trial_enabled", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        with self.assertRaises(ZaiTrialLaneUnavailable) as caught:
            ZaiTrialLane.from_settings(self.ready, moment=TRIAL_END)
        self.assertIn("trial_window_open", str(caught.exception))
        lane = ZaiTrialLane.from_settings(self.ready, moment=BEFORE_END)
        self.assertEqual(lane.adapter.provider, "zai")
        self.assertEqual(lane.model, "glm-5.3-flash")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "trial" / "ledger.sqlite3")
        self.ledger = ZaiTrialLedger(self.path)

    def reserve(self, assignment="A-1", key="k-1", **kwargs):
        return self.ledger.reserve(
            assignment_id=assignment, task_id="T-1", work_packet_digest="sha256:" + "a" * 64,
            idempotency_key=key, model="glm-5.3-flash", purpose="trial", **kwargs,
        )

    def test_reserve_outcome_and_evaluation_round_trip(self):
        attempt = self.reserve()
        self.assertEqual(attempt, attempt_id_for("k-1"))
        self.ledger.record_outcome(attempt, status="SUCCEEDED", latency_ms=1200,
                                   result_packet_digest="sha256:" + "b" * 64)
        self.ledger.record_evaluation(attempt, evaluator="claude", correctness="correct",
                                      first_pass=True, correction_count=0,
                                      privacy_fit="fit", usefulness="accepted", notes="fine")
        status = self.ledger.status(moment=BEFORE_END)
        self.assertEqual((status["attempts"], status["succeeded"], status["evaluated"]), (1, 1, 1))
        self.assertEqual(status["first_pass_yield"], 1.0)
        self.assertEqual(status["correction_rate"], 0.0)
        self.assertEqual(status["mean_latency_ms"], 1200)
        self.assertEqual(status["cost_known"], 0)
        self.assertTrue(status["trial_window_open"])
        for flag in ("dispatch_authorized", "production_credit_issued", "review_authority", "merge_authority"):
            self.assertIs(status[flag], False, flag)
        row = self.ledger.attempts()[0]
        self.assertNotIn("fine", json.dumps(row))
        self.assertIsNone(row["cost_usd"])

    def test_one_primary_attempt_per_assignment_and_one_key_per_attempt(self):
        self.reserve()
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(key="k-1b")
        self.assertIn("zai_primary_attempt_already_recorded", str(caught.exception))
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(assignment="A-2", key="k-1")
        self.assertIn("idempotency_key", str(caught.exception))

    def test_recovery_only_after_a_recorded_failure_with_a_basis(self):
        attempt = self.reserve()
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(key="k-1r", recovery_basis="provider 502")
        self.assertIn("unresolved", str(caught.exception))
        self.ledger.record_outcome(attempt, status="FAILED", latency_ms=10, error_type="RuntimeError")
        with self.assertRaises(ZaiTrialLedgerError):
            self.reserve(key="k-1r", recovery_basis="  ")
        recovery = self.reserve(key="k-1r", recovery_basis="provider 502 during the first attempt")
        self.ledger.record_outcome(recovery, status="FAILED", latency_ms=10, error_type="RuntimeError")
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(key="k-1rr", recovery_basis="again")
        self.assertIn("zai_recovery_already_used", str(caught.exception))

    def test_success_ambiguity_and_owner_blocks_are_never_redispatched(self):
        done = self.reserve(assignment="A-ok", key="ok")
        self.ledger.record_outcome(done, status="SUCCEEDED", latency_ms=5,
                                   result_packet_digest="sha256:" + "b" * 64)
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(assignment="A-ok", key="ok-r", recovery_basis="why")
        self.assertIn("must_not_be_repeated", str(caught.exception))
        unknown = self.reserve(assignment="A-amb", key="amb")
        self.ledger.record_outcome(unknown, status="AMBIGUOUS", latency_ms=69000, error_type="IssuerError")
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(assignment="A-amb", key="amb-r", recovery_basis="timeout")
        self.assertIn("never_redispatched", str(caught.exception))
        blocked = self.reserve(assignment="A-blk", key="blk")
        self.ledger.record_outcome(blocked, status="BLOCKED", latency_ms=5, error_type="OWNER_DECISION_REQUIRED",
                                   result_packet_digest="sha256:" + "c" * 64)
        for kwargs in (dict(), dict(recovery_basis="owner said go")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ZaiTrialLedgerError) as caught:
                self.reserve(assignment="A-blk", key="blk-2", **kwargs)
            self.assertIn("owner_decision_required", str(caught.exception))
        status = self.ledger.status(moment=BEFORE_END)
        self.assertEqual((status["blocked"], status["recorded_failures"]), (1, 1))
        self.assertTrue(status["route_available"])

    def test_refused_attempts_consume_nothing(self):
        refused = self.reserve()
        self.ledger.record_outcome(refused, status="REFUSED", latency_ms=0, error_type="ValueError")
        again = self.reserve(key="k-1-again")  # a fresh primary attempt is still allowed
        self.assertNotEqual(again, refused)
        with self.assertRaises(ZaiTrialLedgerError):
            self.reserve(key="k-1-r", recovery_basis="basis")  # refused rows never justify a recovery
        status = self.ledger.status(moment=BEFORE_END)
        self.assertEqual((status["refused"], status["recorded_failures"], status["unresolved"]), (1, 0, 1))
        self.assertIsNone(status["mean_latency_ms"])
        self.assertTrue(status["route_available"])

    def test_route_closes_after_three_recorded_failures(self):
        for index in range(MAX_RECORDED_FAILURES):
            attempt = self.reserve(assignment=f"A-{index}", key=f"k-{index}")
            self.ledger.record_outcome(attempt, status="FAILED" if index % 2 else "AMBIGUOUS",
                                       latency_ms=1, error_type="RuntimeError")
        self.assertFalse(self.ledger.route_available())
        self.assertFalse(self.ledger.status(moment=BEFORE_END)["route_available"])
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.reserve(assignment="A-next", key="k-next")
        self.assertIn("three_failures", str(caught.exception))

    def test_rows_are_append_only_and_can_never_carry_credit_or_authority(self):
        attempt = self.reserve()
        self.ledger.record_outcome(attempt, status="FAILED", latency_ms=1, error_type="X")
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        for statement in (
            "UPDATE zai_trial_attempts SET purpose = 'edited'",
            "DELETE FROM zai_trial_attempts",
            "UPDATE zai_trial_outcomes SET status = 'SUCCEEDED'",
            "DELETE FROM zai_trial_outcomes",
        ):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                connection.execute(statement)
        for column in ("production_credit", "review_authority", "merge_authority"):
            with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    f"""INSERT INTO zai_trial_attempts (attempt_id, assignment_id, task_id,
                    work_packet_digest, model, purpose, recovery, started_at, {column})
                    VALUES ('x{column}', 'A', 'T', 'd', 'glm-x', 'p', 0, 'now', 1)"""
                )
        for evaluator in ("ZAI", "glm-5.3-flash", "Z.ai GLM", "zai-lane", "z-ai"):
            with self.subTest(evaluator=evaluator), self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO zai_trial_evaluations (attempt_id, evaluated_at, evaluator,
                    correctness, first_pass, correction_count, privacy_fit, usefulness)
                    VALUES (?, 'now', ?, 'correct', 1, 0, 'fit', 'accepted')""",
                    (attempt, evaluator),
                )

    def test_evaluation_rules(self):
        attempt = self.reserve()
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            self.ledger.record_evaluation(attempt, evaluator="claude", correctness="correct",
                                          first_pass=True, correction_count=0,
                                          privacy_fit="fit", usefulness="accepted")
        self.assertIn("requires_recorded_outcome", str(caught.exception))
        self.ledger.record_outcome(attempt, status="SUCCEEDED", latency_ms=1,
                                   result_packet_digest="sha256:" + "b" * 64)
        for kwargs, fragment in (
            (dict(evaluator="z.ai"), "own_work"),
            (dict(evaluator="GLM-5.3-FLASH"), "own_work"),
            (dict(evaluator="Z.ai GLM reviewer"), "own_work"),
            (dict(evaluator="   "), "own_work"),
            (dict(correctness="maybe"), "correctness_unknown"),
            (dict(first_pass=True, correction_count=2), "zero_corrections"),
            (dict(privacy_fit="?"), "privacy_fit_unknown"),
            (dict(usefulness="?"), "usefulness_unknown"),
        ):
            values = dict(evaluator="cursor", correctness="incorrect", first_pass=False,
                          correction_count=2, privacy_fit="fit", usefulness="partially_useful")
            values.update(kwargs)
            with self.subTest(fragment=fragment, kwargs=kwargs), self.assertRaises(ZaiTrialLedgerError) as caught:
                self.ledger.record_evaluation(attempt, **values)
            self.assertIn(fragment, str(caught.exception))
        self.ledger.record_evaluation(attempt, evaluator="cursor", correctness="incorrect",
                                      first_pass=False, correction_count=2,
                                      privacy_fit="fit", usefulness="partially_useful")
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_evaluation(attempt, evaluator="cursor", correctness="correct",
                                          first_pass=True, correction_count=0,
                                          privacy_fit="fit", usefulness="accepted")
        status = self.ledger.status(moment=BEFORE_END)
        self.assertEqual(status["first_pass_yield"], 0.0)
        self.assertEqual(status["correction_rate"], 2.0)
        self.assertTrue(is_self_evaluator("glm-5.3", None))
        self.assertTrue(is_self_evaluator("some-model", "SOME-MODEL"))
        self.assertFalse(is_self_evaluator("owner", "glm-5.3"))

    def test_outcome_rules(self):
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_outcome("missing", status="FAILED", latency_ms=1)
        attempt = self.reserve()
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_outcome(attempt, status="DONE", latency_ms=1)
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_outcome(attempt, status="FAILED", latency_ms=1, cost_usd=0)
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_outcome(attempt, status="SUCCEEDED", latency_ms=1)
        self.ledger.record_outcome(attempt, status="FAILED", latency_ms=1, error_type="X")
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_outcome(attempt, status="FAILED", latency_ms=1, error_type="X")


def _sqlite_exec(path: Path, *statements: str) -> None:
    db = sqlite3.connect(path)
    try:
        for statement in statements:
            db.execute(statement)
        db.commit()
    finally:
        db.close()


class LedgerReuseTests(unittest.TestCase):
    """An existing file is reused only when it is exactly this build's ledger."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "ledger.sqlite3"

    @staticmethod
    def _reserve(ledger, key="k-1"):
        return ledger.reserve(assignment_id="A-1", task_id="T-1", work_packet_digest="sha256:" + "a" * 64,
                              idempotency_key=key, model="glm-5.3-flash", purpose="reuse test")

    def test_a_matching_ledger_reopens_with_its_rows(self):
        attempt = self._reserve(ZaiTrialLedger(str(self.path)))
        again = ZaiTrialLedger(str(self.path))
        self.assertEqual([row["attempt_id"] for row in again.attempts()], [attempt])
        self.assertEqual(again.status()["version"], LEDGER_VERSION)

    def test_foreign_partial_weakened_older_or_non_sqlite_files_are_refused(self):
        foreign = self.root / "director.sqlite3"
        _sqlite_exec(foreign, "CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        partial = self.root / "partial.sqlite3"
        _sqlite_exec(partial, "CREATE TABLE zai_trial_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                     f"INSERT INTO zai_trial_metadata VALUES ('version', '{LEDGER_VERSION}')")
        weakened = self.root / "weakened.sqlite3"
        ZaiTrialLedger(str(weakened))
        _sqlite_exec(weakened, "DROP TRIGGER zai_trial_attempts_no_update")
        older = self.root / "older.sqlite3"
        ZaiTrialLedger(str(older))
        _sqlite_exec(older, "UPDATE zai_trial_metadata SET value = 'zai-trial-ledger/v0' WHERE key = 'version'")
        text = self.root / "notes.sqlite3"
        text.write_text("not a database at all, just text", encoding="utf-8")
        for label, path in (("foreign TaskStore", foreign), ("partial schema", partial),
                            ("missing append-only trigger", weakened), ("older version", older),
                            ("not sqlite", text)):
            with self.subTest(label=label):
                with self.assertRaises(ZaiTrialLedgerError) as caught:
                    ZaiTrialLedger(str(path))
                self.assertTrue(str(caught.exception).startswith("zai_ledger_"), str(caught.exception))
                self.assertNotIn(self.tmp.name, str(caught.exception))
        db = sqlite3.connect(foreign)
        try:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        finally:
            db.close()
        self.assertFalse(any(name.startswith("zai_trial") for name in names), names)
        self.assertEqual(text.read_text(encoding="utf-8"), "not a database at all, just text")

    def test_a_file_replaced_by_an_empty_database_is_refused_not_recreated(self):
        ledger = ZaiTrialLedger(str(self.path))
        self._reserve(ledger)
        self.path.unlink()
        _sqlite_exec(self.path, "PRAGMA user_version = 0")  # a valid but empty SQLite file
        with self.assertRaises(ZaiTrialLedgerError) as caught:
            ledger.status()
        self.assertEqual(str(caught.exception), "zai_ledger_not_a_trial_ledger")
        with self.assertRaises(ZaiTrialLedgerError):
            self._reserve(ledger, key="k-2")
        db = sqlite3.connect(self.path)
        try:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        finally:
            db.close()
        self.assertEqual(names, set(), "the ledger must never recreate its schema into a replaced file")
        # A fresh ledger object may still initialise an empty file: that is the only sanctioned path.
        self.assertEqual(ZaiTrialLedger(str(self.path)).status()["attempts"], 0)

    def test_initialising_flag_is_never_observable_outside_the_lock(self):
        ledger = ZaiTrialLedger(str(self.path))
        self.assertFalse(ledger._initializing)
        observed = []
        original = ledger._verify_schema

        def spy(connection, *, allow_empty):
            observed.append(allow_empty)
            return original(connection, allow_empty=allow_empty)

        ledger._verify_schema = spy
        ledger.status()
        self._reserve(ledger)
        self.assertEqual(observed, [False, False], "ordinary connections must never run with allow_empty")
        with self.assertRaises(ZaiTrialLedgerError):  # a failure inside _initialize leaves the flag cleared
            with patch.object(ZaiTrialLedger, "_verify_schema", side_effect=ZaiTrialLedgerError("zai_ledger_not_a_trial_ledger")):
                ZaiTrialLedger(str(self.root / "other.sqlite3"))

    def test_a_file_replaced_after_opening_is_refused_on_the_next_call(self):
        ledger = ZaiTrialLedger(str(self.path))
        self._reserve(ledger)
        self.path.unlink()
        _sqlite_exec(self.path, "CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        with self.assertRaises(ZaiTrialLedgerError):
            self._reserve(ledger, key="k-2")
        with self.assertRaises(ZaiTrialLedgerError):
            ledger.status()
        db = sqlite3.connect(self.path)
        try:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        finally:
            db.close()
        self.assertFalse(any(name.startswith("zai_trial") for name in names), names)


class LaneDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = ZaiTrialLedger(str(Path(self.tmp.name) / "ledger.sqlite3"))
        self.journal = Path(self.tmp.name) / "journal"
        self.journal.mkdir()
        self.work = work_packet()
        self.context = _context(self.work)
        self.result = {"provider": "zai", "status": "SUCCEEDED", "outcome": "def add(a, b): return a + b"}

    def lane(self, behaviour, *, now=None):
        return ZaiTrialLane(adapter=FakeAdapter(behaviour), ledger=self.ledger, model="glm-5.3-flash",
                            journal=self.journal, now=now or (lambda: BEFORE_END))

    def mark_contacted(self, key="k-1"):
        (self.journal / f"{attempt_id_for(key)}.json").write_text("{}", encoding="utf-8")

    def test_success_is_journaled_with_latency_digest_and_unknown_cost(self):
        dispatched = self.lane(self.result).dispatch(self.work, context=self.context, purpose="smoke")
        self.assertEqual(dispatched.result, self.result)
        row = self.ledger.attempts()[0]
        self.assertEqual(row["status"], "SUCCEEDED")
        self.assertEqual(row["result_packet_digest"], document_digest(self.result))
        self.assertIsNone(row["cost_usd"])
        self.assertGreaterEqual(row["latency_ms"], 0)
        self.assertEqual(row["purpose"], "smoke")

    def test_provider_reported_statuses_map_to_failed_or_blocked(self):
        self.lane(dict(self.result, status="FAILED")).dispatch(self.work, context=self.context, purpose="x")
        blocked_work = work_packet(assignment="A-2", key="k-2")
        self.lane(dict(self.result, status="OWNER_DECISION_REQUIRED")).dispatch(
            blocked_work, context=_context(blocked_work), purpose="x")
        rows = {row["assignment_id"]: (row["status"], row["error_type"]) for row in self.ledger.attempts()}
        self.assertEqual(rows["A-1"], ("FAILED", "FAILED"))
        self.assertEqual(rows["A-2"], ("BLOCKED", "OWNER_DECISION_REQUIRED"))
        status = self.ledger.status(moment=BEFORE_END)
        self.assertEqual((status["failed"], status["blocked"], status["recorded_failures"]), (1, 1, 1))

    def test_exceptions_map_to_failed_ambiguous_or_refused_and_propagate(self):
        # Two refusals consume nothing; the three contacted failures then close the route.
        cases = {
            "A-v": (ValueError("Trial packet exceeds input limit"), False, "REFUSED"),
            "A-c": (PacketContractError("bad result"), False, "REFUSED"),
            "A-x": (ValueError("provider said no after contact"), True, "FAILED"),
            "A-r": (FileExistsError("reserved"), False, "AMBIGUOUS"),
            "A-t": (IssuerError("timeout"), True, "AMBIGUOUS"),
        }
        for assignment, (exc, contacted, expected) in cases.items():
            work = work_packet(assignment=assignment, key=f"k-{assignment}")
            if contacted:
                self.mark_contacted(f"k-{assignment}")
            with self.subTest(assignment=assignment), self.assertRaises(type(exc)):
                self.lane(exc).dispatch(work, context=_context(work), purpose="x")
        statuses = {row["assignment_id"]: row["status"] for row in self.ledger.attempts()}
        self.assertEqual(statuses, {a: expected for a, (_, _, expected) in cases.items()})
        status = self.ledger.status(moment=BEFORE_END)
        self.assertEqual((status["refused"], status["recorded_failures"]), (2, 3))
        self.assertFalse(status["route_available"])

    def test_refused_pre_io_attempt_keeps_the_primary_attempt_available(self):
        with self.assertRaises(ValueError):
            self.lane(ValueError("Trial packet exceeds input limit")).dispatch(
                self.work, context=self.context, purpose="x")
        retry = work_packet(key="k-1-retry")
        dispatched = self.lane(self.result).dispatch(retry, context=_context(retry), purpose="x")
        self.assertEqual(self.ledger.attempts()[-1]["status"], "SUCCEEDED")
        self.assertEqual(dispatched.result["status"], "SUCCEEDED")

    def test_second_dispatch_of_an_assignment_never_reaches_the_provider(self):
        adapter = FakeAdapter(RuntimeError("boom"))
        lane = ZaiTrialLane(adapter=adapter, ledger=self.ledger, model="glm-5.3-flash",
                            journal=self.journal, now=lambda: BEFORE_END)
        self.mark_contacted("k-1")
        with self.assertRaises(RuntimeError):
            lane.dispatch(self.work, context=self.context, purpose="x")
        retry = work_packet(key="k-2")
        with self.assertRaises(ZaiTrialLedgerError):
            lane.dispatch(retry, context=_context(retry), purpose="x")
        self.assertEqual(adapter.calls, 1)
        lane.adapter.behaviour = self.result
        lane.dispatch(retry, context=_context(retry), purpose="x", recovery_basis="transient provider error")
        self.assertEqual(adapter.calls, 2)

    def test_preflight_refusals_happen_before_reservation(self):
        lane = self.lane(self.result)
        budgeted = work_packet(budget_usd=1.0)
        mismatched = work_packet()
        mismatched["objective"] = "changed after the context was signed"
        refusals = [
            (self.work, _context(self.work, allowed_tools=("READ_REPOSITORY",))),
            (self.work, _context(self.work, budget_usd=0.5)),
            (self.work, _context(self.work, timeout_seconds=70)),
            (budgeted, _context(budgeted)),
            (mismatched, self.context),
            ({"not": "a packet"}, self.context),
        ]
        for work, context in refusals:
            with self.subTest(context=context), self.assertRaises(ZaiTrialLaneUnavailable):
                lane.dispatch(work, context=context, purpose="x")
        with self.assertRaises(ZaiTrialLaneUnavailable):
            self.lane(self.result, now=lambda: TRIAL_END).dispatch(self.work, context=self.context, purpose="x")
        self.assertEqual(self.ledger.attempts(), [])
        self.assertEqual(lane.adapter.calls, 0)

    def test_lane_only_accepts_the_zai_adapter(self):
        class Other(FakeAdapter):
            provider = "claude"

        with self.assertRaises(ValueError):
            ZaiTrialLane(adapter=Other(self.result), ledger=self.ledger, model="glm-5.3-flash")


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 19:30 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 02:55 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-20 | 03:20 UTC | END]]
