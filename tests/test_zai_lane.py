# [[Claude Code | Claude Fable 5.1 | 2026-09-19 | 06:05 UTC | BEGIN]]
import json
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
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.zai_lane import (
    MAX_RECORDED_FAILURES,
    ZAI_CONTACT_LANE,
    ZaiTrialLane,
    ZaiTrialLaneUnavailable,
    ZaiTrialLedger,
    ZaiTrialLedgerError,
    attempt_id_for,
)
from director_orchestrator.zai_trial import ENDPOINT, TRIAL_END, ZaiLiteTrialTransport

SECRET = "test.secret"


def _context(assignment="A-1", key="k-1", **overrides) -> ProviderAdapterContext:
    fields = dict(
        task_id="T-1", assignment_id=assignment, idempotency_key=key,
        work_packet_digest="sha256:" + "a" * 64, writer_token_digest="sha256:" + "0" * 64,
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
        from director_orchestrator import engine, team_charter
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


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ready = replace(
            Settings(),
            zai_trial_enabled=True, zai_api_key=SECRET, zai_api_base=ENDPOINT,
            zai_mode="trial_only", zai_model="glm-5.3-flash",
            zai_cli_executable=sys.executable,
            zai_trial_journal_dir=str(root / "journal"),
            zai_trial_ledger_path=str(root / "ledger.sqlite3"),
            zai_timeout_seconds=60, zai_lite_entitlement_verified=True,
            zai_production_activation="",
            zai_allow_repository_write=False, zai_allow_review_verdict=False,
        )
        self.before = TRIAL_END - timedelta(days=1)

    def test_defaults_fail_closed_and_stay_boolean(self):
        settings = Settings()
        readiness = settings.zai_trial_readiness(moment=self.before)
        self.assertTrue(all(isinstance(value, bool) for value in readiness.values()))
        self.assertFalse(readiness["trial_enabled"])
        self.assertFalse(readiness["credential_configured"])
        self.assertTrue(readiness["official_endpoint"])
        self.assertTrue(readiness["trial_only_mode"])
        self.assertTrue(readiness["production_activation_absent"])
        self.assertIs(settings.readiness()["zai"], False)
        settings.validate_zai_trial_configuration()  # defaults are a valid closed lane

    def test_fully_configured_lane_is_ready_only_inside_the_trial_window(self):
        self.assertTrue(all(self.ready.zai_trial_readiness(moment=self.before).values()))
        at_end = self.ready.zai_trial_readiness(moment=TRIAL_END)
        self.assertFalse(at_end["trial_window_open"])
        self.assertTrue(all(value for name, value in at_end.items() if name != "trial_window_open"))
        self.assertNotIn(SECRET, json.dumps(self.ready.zai_trial_readiness(moment=self.before)))

    def test_each_widening_setting_fails_startup_validation(self):
        widenings = {
            "ZAI_MODE": dict(zai_mode="production"),
            "ZAI_ALLOW_REPOSITORY_WRITE": dict(zai_allow_repository_write=True),
            "ZAI_ALLOW_REVIEW_VERDICT": dict(zai_allow_review_verdict=True),
            "ZAI_PRODUCTION_ACTIVATION": dict(zai_production_activation="sha256:" + "f" * 64),
            "ZAI_API_BASE": dict(zai_api_base="https://api.anthropic.com"),
            "ZAI_TRIAL_ENABLED": dict(zai_lite_entitlement_verified=False),
        }
        for variable, change in widenings.items():
            with self.subTest(variable=variable):
                widened = replace(self.ready, **change)
                with self.assertRaises(ValueError) as caught:
                    widened.validate_zai_trial_configuration()
                self.assertIn(variable, str(caught.exception))
                self.assertFalse(all(widened.zai_trial_readiness(moment=self.before).values()))
        self.ready.validate_zai_trial_configuration()

    def test_individual_readiness_checks_close_the_lane(self):
        cases = {
            "credential_configured": dict(zai_api_key="has space"),
            "glm_model_configured": dict(zai_model="claude-opus"),
            "cli_configured": dict(zai_cli_executable="relative/claude"),
            "journal_configured": dict(zai_trial_journal_dir=""),
            "ledger_configured": dict(zai_trial_ledger_path=""),
            "timeout_valid": dict(zai_timeout_seconds=70),
            "lite_entitlement_verified": dict(zai_lite_entitlement_verified=False),
        }
        for check, change in cases.items():
            with self.subTest(check=check):
                readiness = replace(self.ready, **change).zai_trial_readiness(moment=self.before)
                self.assertFalse(readiness[check])
                self.assertFalse(all(readiness.values()))

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
            ZaiTrialLane.from_settings(Settings(), moment=self.before)
        self.assertIn("trial_enabled", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        with self.assertRaises(ZaiTrialLaneUnavailable) as caught:
            ZaiTrialLane.from_settings(self.ready, moment=TRIAL_END)
        self.assertIn("trial_window_open", str(caught.exception))
        lane = ZaiTrialLane.from_settings(self.ready, moment=self.before)
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
        status = self.ledger.status()
        self.assertEqual((status["attempts"], status["succeeded"], status["evaluated"]), (1, 1, 1))
        self.assertEqual(status["first_pass_yield"], 1.0)
        self.assertEqual(status["correction_rate"], 0.0)
        self.assertEqual(status["mean_latency_ms"], 1200)
        self.assertEqual(status["cost_known"], 0)
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

    def test_success_and_ambiguity_are_never_redispatched(self):
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

    def test_route_closes_after_three_recorded_failures(self):
        for index in range(MAX_RECORDED_FAILURES):
            attempt = self.reserve(assignment=f"A-{index}", key=f"k-{index}")
            self.ledger.record_outcome(attempt, status="FAILED" if index % 2 else "AMBIGUOUS",
                                       latency_ms=1, error_type="RuntimeError")
        self.assertFalse(self.ledger.route_available())
        self.assertFalse(self.ledger.status()["route_available"])
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
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO zai_trial_evaluations (attempt_id, evaluated_at, evaluator,
                correctness, first_pass, correction_count, privacy_fit, usefulness)
                VALUES (?, 'now', 'ZAI', 'correct', 1, 0, 'fit', 'accepted')""",
                (attempt,),
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
            (dict(correctness="maybe"), "correctness_unknown"),
            (dict(first_pass=True, correction_count=2), "zero_corrections"),
            (dict(privacy_fit="?"), "privacy_fit_unknown"),
            (dict(usefulness="?"), "usefulness_unknown"),
        ):
            values = dict(evaluator="cursor", correctness="incorrect", first_pass=False,
                          correction_count=2, privacy_fit="fit", usefulness="partially_useful")
            values.update(kwargs)
            with self.subTest(fragment=fragment), self.assertRaises(ZaiTrialLedgerError) as caught:
                self.ledger.record_evaluation(attempt, **values)
            self.assertIn(fragment, str(caught.exception))
        self.ledger.record_evaluation(attempt, evaluator="cursor", correctness="incorrect",
                                      first_pass=False, correction_count=2,
                                      privacy_fit="fit", usefulness="partially_useful")
        with self.assertRaises(ZaiTrialLedgerError):
            self.ledger.record_evaluation(attempt, evaluator="cursor", correctness="correct",
                                          first_pass=True, correction_count=0,
                                          privacy_fit="fit", usefulness="accepted")
        status = self.ledger.status()
        self.assertEqual(status["first_pass_yield"], 0.0)
        self.assertEqual(status["correction_rate"], 2.0)

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


class LaneDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = ZaiTrialLedger(str(Path(self.tmp.name) / "ledger.sqlite3"))
        self.work = {"objective": "add"}
        self.result = {"provider": "zai", "status": "SUCCEEDED", "outcome": "def add(a, b): return a + b"}

    def lane(self, behaviour):
        return ZaiTrialLane(adapter=FakeAdapter(behaviour), ledger=self.ledger, model="glm-5.3-flash")

    def test_success_is_journaled_with_latency_digest_and_unknown_cost(self):
        dispatched = self.lane(self.result).dispatch(self.work, context=_context(), purpose="smoke")
        self.assertEqual(dispatched.result, self.result)
        row = self.ledger.attempts()[0]
        self.assertEqual(row["status"], "SUCCEEDED")
        self.assertEqual(row["result_packet_digest"], document_digest(self.result))
        self.assertIsNone(row["cost_usd"])
        self.assertGreaterEqual(row["latency_ms"], 0)
        self.assertEqual(row["purpose"], "smoke")

    def test_provider_reported_failure_status_is_a_recorded_failure(self):
        failed = dict(self.result, status="FAILED")
        self.lane(failed).dispatch(self.work, context=_context(), purpose="smoke")
        row = self.ledger.attempts()[0]
        self.assertEqual((row["status"], row["error_type"]), ("FAILED", "FAILED"))

    def test_exceptions_map_to_failed_or_ambiguous_and_propagate(self):
        with self.assertRaises(RuntimeError):
            self.lane(RuntimeError("boom")).dispatch(self.work, context=_context("A-f", "f"), purpose="x")
        with self.assertRaises(IssuerError):
            self.lane(IssuerError("timeout")).dispatch(self.work, context=_context("A-t", "t"), purpose="x")
        with self.assertRaises(FileExistsError):
            self.lane(FileExistsError("reserved")).dispatch(self.work, context=_context("A-r", "r"), purpose="x")
        statuses = {row["assignment_id"]: row["status"] for row in self.ledger.attempts()}
        self.assertEqual(statuses, {"A-f": "FAILED", "A-t": "AMBIGUOUS", "A-r": "AMBIGUOUS"})

    def test_second_dispatch_of_an_assignment_never_reaches_the_provider(self):
        adapter = FakeAdapter(RuntimeError("boom"))
        lane = ZaiTrialLane(adapter=adapter, ledger=self.ledger, model="glm-5.3-flash")
        with self.assertRaises(RuntimeError):
            lane.dispatch(self.work, context=_context(), purpose="x")
        with self.assertRaises(ZaiTrialLedgerError):
            lane.dispatch(self.work, context=_context(key="k-2"), purpose="x")
        self.assertEqual(adapter.calls, 1)
        lane.adapter.behaviour = self.result
        lane.dispatch(self.work, context=_context(key="k-2"), purpose="x",
                      recovery_basis="transient provider error")
        self.assertEqual(adapter.calls, 2)

    def test_tools_budget_and_expiry_refuse_before_reservation(self):
        lane = self.lane(self.result)
        with self.assertRaises(ZaiTrialLaneUnavailable):
            lane.dispatch(self.work, context=_context(allowed_tools=("READ_REPOSITORY",)), purpose="x")
        with self.assertRaises(ZaiTrialLaneUnavailable):
            lane.dispatch(self.work, context=_context(budget_usd=0.5), purpose="x")
        with patch("director_orchestrator.zai_lane.datetime") as clock:
            clock.now.return_value = TRIAL_END
            with self.assertRaises(ZaiTrialLaneUnavailable):
                lane.dispatch(self.work, context=_context(), purpose="x")
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
