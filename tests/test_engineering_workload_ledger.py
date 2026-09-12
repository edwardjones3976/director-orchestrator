# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]]
"""Behavioral checks against disposable real SQLite journals, never live lanes."""
from contextlib import closing
import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.engineering_workload_ledger import (
    EngineeringWorkloadLedger, LedgerError, human_status, policy_preview,
)


def evidence(label="fixture-only"):
    return {"reference": "fixture://" + label, "sha256": hashlib.sha256(label.encode()).hexdigest()}


def route(product, **kwargs):
    return {"product_id": product, "route_id": product + ":fixture-route", "actor_id": product + ":fixture-actor",
            "provider_identity": product + ":fixture-provider", "model_family": product + ":fixture-family",
            "eligible": True, "roles": ["implementation", "review", "validation", "research", "coordination"],
            "enrollment_evidence": evidence(), **kwargs}


def manifest(product_policy=False):
    products = ["openai-codex", "claude-code", "cursor", "xai-grok", "gemini-antigravity"]
    policy = {"kind": "canonical", "profile": "engineering"}
    if product_policy:
        products = ["openai-codex", "grok-bot", "gemini-antigravity"]
        policy = {"kind": "product", "targets": dict(zip(products, (40, 40, 20)))}
    return {"authorization": evidence("owner-bootstrap-fixture"), "policy_authorization": evidence("owner-policy-fixture"),
            "operator": "test-operator", "cutover_at": "2000-01-01T00:00:00Z", "policy": policy,
            "history_mode": "prospective", "history_evidence": evidence("quiescent-boundary-fixture"),
            "prior_history_unknown": True, "quiescent_cutover_verified": True,
            "all_future_assignments_required": True, "routes": [route(p) for p in products]}


def request(number, product="openai-codex", **kwargs):
    return {"assignment_id": f"test-{number}", "task_id": f"task-{number}", "scope": "Bounded fixture task",
            "kind": "implementation", "product_id": product, "route_id": product + ":fixture-route", "packet_evidence": evidence(str(number)),
            "transport": "local_cli", "bounds": {"timeout_seconds": 3600, "call_limit": 3,
                "turn_limit": 6, "output_bytes_limit": 1000, "usd_limit": None,
                "hard_usd_cap_verified": False, "unverified_billing_authorization": evidence("billing-fixture")}, **kwargs}


def result(outcome="succeeded", **usage):
    return {"outcome": outcome, "evidence": evidence("native-result-fixture"),
            "usage": {"usd": None, "turns": 1, "output_bytes": 10, **usage}}


def race_reserve(path, number, ready, start, output):
    ready.put(True)
    start.wait(10)
    try:
        EngineeringWorkloadLedger(path).reserve(request(number))
        output.put("reserved")
    except LedgerError as exc:
        output.put(str(exc))


class EngineeringWorkloadLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "external.sqlite3"
        self.ledger = EngineeringWorkloadLedger(self.path)

    def bootstrap(self, product_policy=False):
        return self.ledger.bootstrap(manifest(product_policy))

    def start(self, number=1, product="openai-codex", **kwargs):
        self.ledger.reserve(request(number, product, **kwargs))
        return self.ledger.begin_attempt(f"test-{number}", transport="local_cli")

    def test_missing_history_is_unknown_and_status_does_not_create_file(self):
        state = self.ledger.status()
        self.assertEqual(state["history_mode"], "unknown")
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(LedgerError, "bootstrap_required"):
            self.ledger.reserve(request(1))

    def test_prospective_start_is_not_verified_historical_compliance(self):
        state = self.bootstrap()
        self.assertFalse(state["pre_cutover_history_verified"])
        self.assertEqual(state["denominator"], 20)
        self.assertEqual(state["unfilled_slots"], 20)
        self.assertFalse(state["rolling_window_complete"])
        self.assertFalse(state["history_attestations_authenticated_by_this_tool"])
        self.assertIn("20 real assignments; denominator 20", human_status(state))

    def test_prospective_requires_authority_and_quiescent_cutover_evidence(self):
        for missing in ("authorization", "history_evidence", "quiescent_cutover_verified", "prior_history_unknown", "all_future_assignments_required"):
            candidate = manifest()
            del candidate[missing]
            with self.subTest(missing=missing), self.assertRaises(LedgerError):
                self.ledger.bootstrap(candidate)
        self.assertFalse(self.path.exists())

    def test_bootstrap_cannot_reset_even_an_empty_established_ledger(self):
        self.bootstrap()
        with self.assertRaisesRegex(LedgerError, "must_not_be_reset"):
            self.bootstrap()
        self.ledger.reserve(request(1))
        with self.assertRaisesRegex(LedgerError, "must_not_be_reset"):
            self.bootstrap()
        self.assertEqual(self.ledger.status()["total_admitted_assignments"], 1)

    def test_foreign_database_is_preserved(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE runtime_history(value TEXT)")
            db.execute("INSERT INTO runtime_history VALUES ('preserve')")
        with self.assertRaisesRegex(LedgerError, "must_not_be_reset"):
            self.bootstrap()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM runtime_history").fetchone()[0], "preserve")

    def test_same_reservation_is_idempotent_but_does_not_issue_dispatch_permission(self):
        self.bootstrap()
        first = self.ledger.reserve(request(1))
        second = self.ledger.reserve(request(1))
        self.assertFalse(first["dispatch_permitted"])
        self.assertTrue(second["already_reserved"])
        self.assertFalse(second["dispatch_permitted"])
        self.assertEqual(self.ledger.status()["total_admitted_assignments"], 1)
        changed = request(1, scope="Different task")
        with self.assertRaisesRegex(LedgerError, "reused_with_different"):
            self.ledger.reserve(changed)

    def test_new_id_cannot_duplicate_the_same_native_packet(self):
        self.bootstrap()
        self.ledger.reserve(request(1))
        second = request(1, assignment_id="new-id")
        with self.assertRaisesRegex(LedgerError, "same_packet"):
            self.ledger.reserve(second)

    def test_hard_ceiling_counts_unsettled_and_failed_assignments(self):
        self.bootstrap()
        for i in range(9):
            self.ledger.reserve(request(i))
            if i < 3:
                self.ledger.settle(f"test-{i}", "cancelled", evidence())
        with self.assertRaisesRegex(LedgerError, "hard_cap_exceeded"):
            self.ledger.reserve(request(9))
        state = self.ledger.status()
        row = next(r for r in state["utilization"] if r["bucket"] == "openai")
        self.assertEqual((row["admitted"], row["share_percent"], row["delta_points"]), (9, 45, 5))
        self.assertEqual(row["normal_band_state"], "above")
        self.assertEqual(row["reserved_or_in_flight"], 6)

    def test_two_processes_cannot_claim_the_last_openai_slot(self):
        self.bootstrap()
        for i in range(8):
            self.ledger.reserve(request(i))
        context = multiprocessing.get_context("spawn")
        ready, output, start = context.Queue(), context.Queue(), context.Event()
        children = [context.Process(target=race_reserve, args=(str(self.path), 20+i, ready, start, output)) for i in range(2)]
        for child in children:
            child.start()
        for _ in children:
            ready.get(timeout=15)
        start.set()
        outcomes = [output.get(timeout=20) for _ in children]
        for child in children:
            child.join(15)
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(outcomes.count("reserved"), 1)
        self.assertEqual(sum("hard_cap_exceeded" in value for value in outcomes), 1)
        self.assertEqual(self.ledger.status()["total_admitted_assignments"], 9)

    def test_twenty_first_assignment_evicts_oldest_from_window_not_storage(self):
        self.bootstrap()
        products = ["openai-codex"]*8 + ["claude-code"]*5 + ["cursor"]*3 + ["xai-grok"]*2 + ["gemini-antigravity"]*2
        for i, product in enumerate(products):
            self.ledger.reserve(request(i, product))
        self.ledger.reserve(request(20))
        state = self.ledger.status()
        self.assertEqual(state["total_admitted_assignments"], 21)
        self.assertEqual(len(state["records"]), 20)
        self.assertEqual(state["records"][0]["assignment_id"], "test-1")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 21)

    def test_coordination_does_not_inflate_production_window(self):
        self.bootstrap()
        self.ledger.reserve(request(0))
        self.ledger.settle("test-0", "cancelled", evidence())
        for i in range(1, 105):
            self.ledger.reserve(request(i, kind="coordination"))
        state = self.ledger.status()
        self.assertEqual(state["real_assignments_in_window"], 1)
        self.assertEqual(state["coordination_records"], 104)
        self.assertEqual(state["records"][0]["state"], "cancelled")
        self.assertFalse(state["production_credit_issued"])

    def test_coordination_is_record_only_and_cannot_obtain_a_provider_permit(self):
        self.bootstrap()
        reserved = self.ledger.reserve(request(1, kind="coordination"))
        self.assertFalse(reserved["dispatch_permitted"])
        self.assertEqual(reserved["next"], "settle")
        with self.assertRaisesRegex(LedgerError, "record_only_no_provider_dispatch"):
            self.ledger.begin_attempt("test-1", transport="local_cli")
        with self.assertRaisesRegex(LedgerError, "attempt_unknown"):
            self.ledger.record_outcome("test-1", 1, result())
        with self.assertRaisesRegex(LedgerError, "reused_with_different"):
            self.ledger.reserve(request(1, kind="implementation"))
        completed = self.ledger.settle("test-1", "succeeded", evidence("coordination-only"))
        self.assertFalse(completed["productive_credit_issued"])
        self.assertEqual(self.ledger.status()["total_admitted_assignments"], 0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)

    def test_grok_bot_is_never_implicitly_direct_grok_or_cursor(self):
        self.bootstrap()
        with self.assertRaisesRegex(LedgerError, "allocation_binding_missing"):
            self.ledger.enroll_route(route("grok-bot"))
        rows = {r["product_id"]: r for r in self.ledger.status()["routes"]}
        self.assertIsNone(rows["grok-bot"]["allocation_bucket"])
        self.assertEqual(rows["xai-grok"]["allocation_bucket"], "grok")
        self.assertEqual(rows["cursor"]["allocation_bucket"], "cursor")

    def test_product_policy_preview_is_not_an_active_policy(self):
        state = policy_preview(manifest(True)["policy"])
        self.assertEqual(state["maximum_slots"], {"openai-codex": 9, "grok-bot": 9, "gemini-antigravity": 5})
        self.assertFalse(state["dispatch_authorized"])
        self.assertFalse(self.path.exists())
        proposed = manifest(True)
        del proposed["policy_authorization"]
        with self.assertRaisesRegex(LedgerError, "policy_authorization"):
            self.ledger.bootstrap(proposed)

    def test_explicit_product_policy_does_not_change_runtime_profile(self):
        state = self.bootstrap(True)
        self.assertEqual(state["policy"]["targets"]["grok-bot"], 40)
        self.assertNotIn("xai-grok", state["next_eligible_routes"])
        for i in range(9):
            self.ledger.reserve(request(i, "grok-bot"))
        with self.assertRaisesRegex(LedgerError, "hard_cap"):
            self.ledger.reserve(request(9, "grok-bot"))
        from director_orchestrator.allocation_profiles import allocation_targets
        self.assertEqual(dict(allocation_targets())["grok"], 10)

    def test_explicit_shared_bucket_does_not_duplicate_its_quota(self):
        data = manifest()
        data["policy"]["product_bindings"] = {"grok-bot": "grok"}
        data["routes"].append(route("grok-bot"))
        self.ledger.bootstrap(data)
        self.ledger.reserve(request(1, "xai-grok"))
        self.ledger.reserve(request(2, "grok-bot"))
        self.ledger.reserve(request(3, "grok-bot"))
        with self.assertRaisesRegex(LedgerError, "hard_cap"):
            self.ledger.reserve(request(4, "xai-grok"))
        self.assertEqual({r["product_id"] for r in self.ledger.status()["records"]}, {"xai-grok", "grok-bot"})

    def test_zero_product_target_is_ineligible_even_if_enrolled(self):
        data = manifest(True)
        data["policy"]["targets"] = {"openai-codex": 50, "grok-bot": 0, "gemini-antigravity": 50}
        self.ledger.bootstrap(data)
        with self.assertRaisesRegex(LedgerError, "product_ineligible"):
            self.ledger.reserve(request(1, "grok-bot"))

    def test_parent_and_native_tool_names_do_not_exempt_coding_assignments(self):
        self.bootstrap()
        self.ledger.reserve(request(1, kind="coordination"))
        self.ledger.reserve(request(2, parent_assignment_id="test-1", scope="Actual Codex subagent coding"))
        state = self.ledger.status()
        self.assertEqual(state["coordination_records"], 1)
        self.assertEqual(state["real_assignments_in_window"], 1)
        self.assertEqual(state["records"][0]["product_id"], "openai-codex")

    def test_import_requires_exactly_twenty_attested_real_assignments(self):
        data = manifest()
        data.update(history_mode="verified_last20", complete_last20_attested=True)
        with self.assertRaisesRegex(LedgerError, "exactly_20"):
            self.ledger.bootstrap(data)
        products = ["openai-codex"]*8 + ["claude-code"]*5 + ["cursor"]*3 + ["xai-grok"]*2 + ["gemini-antigravity"]*2
        data["history"] = [{"request": request(i, product), "admitted_at": "1999-01-01T00:00:00Z",
                           "outcome": "failed", "evidence": evidence(str(i))} for i, product in enumerate(products)]
        state = self.ledger.bootstrap(data)
        self.assertTrue(state["pre_cutover_history_verified"])
        self.assertFalse(state["history_attestations_authenticated_by_this_tool"])
        self.assertEqual(len(state["records"]), 20)
        self.assertTrue(all(r["state"] == "failed" for r in state["records"]))

    def test_missing_import_evidence_rolls_back_every_assignment(self):
        data = manifest()
        data.update(history_mode="verified_last20", complete_last20_attested=True)
        data["history"] = [{"request": request(i), "admitted_at": "1999-01-01T00:00:00Z",
                            "outcome": "failed", "evidence": evidence(str(i))} for i in range(20)]
        del data["history"][10]["evidence"]
        with self.assertRaisesRegex(LedgerError, "historical_assignment_evidence"):
            self.ledger.bootstrap(data)
        self.bootstrap()
        self.assertEqual(self.ledger.status()["total_admitted_assignments"], 0)

    def test_route_and_history_cannot_be_rewritten_by_update_or_delete(self):
        self.bootstrap()
        self.ledger.reserve(request(1))
        for statement in ("DELETE FROM assignments", "UPDATE routes SET payload='{}'", "DELETE FROM metadata"):
            with closing(sqlite3.connect(self.path)) as db, self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
                db.execute(statement)
        with self.assertRaisesRegex(LedgerError, "binding_immutable"):
            self.ledger.enroll_route(route("openai-codex"))
        with self.assertRaisesRegex(LedgerError, "no_route_alias_reset"):
            self.ledger.enroll_route(route("openai-codex", route_id="same-actor-new-alias"))

    def test_native_acceptance_rejects_shared_product_actor_provider_or_family(self):
        self.bootstrap()
        independent = {"product_id": "other", "actor_id": "other", "provider_identity": "other", "model_family": "other"}
        for key in independent:
            author = {**independent, key: route("openai-codex")[key]}
            candidate = request(1, kind="review", review_policy="native_acceptance", candidate_sha="a"*40, material_authors=[author], authorship_evidence=evidence())
            with self.subTest(key=key), self.assertRaisesRegex(LedgerError, "not_independent"):
                self.ledger.reserve(candidate)
        good = request(1, kind="review", review_policy="native_acceptance", candidate_sha="a"*40, material_authors=[independent], authorship_evidence=evidence())
        self.assertFalse(self.ledger.reserve(good)["dispatch_permitted"])

    def test_unknown_managed_model_can_implement_but_not_certify_independence(self):
        data = manifest(True)
        data["routes"][1] = route("grok-bot", provider_identity=None, model_family=None)
        self.ledger.bootstrap(data)
        self.ledger.reserve(request(1, "grok-bot"))
        with self.assertRaisesRegex(LedgerError, "independence_unverified"):
            self.ledger.reserve(request(2, "grok-bot", kind="review", review_policy="native_acceptance", candidate_sha="a"*40,
                material_authors=[route("openai-codex")], authorship_evidence=evidence()))

    def test_ordinary_pr_review_is_per_actual_actor_not_product_or_model_family(self):
        self.bootstrap()
        self.ledger.enroll_route(route("openai-codex", route_id="openai-reviewer", actor_id="distinct-reviewer"))
        candidate = request(1, kind="review", route_id="openai-reviewer", review_policy="ordinary_pr",
                            candidate_sha="a"*40, material_authors=[route("openai-codex")], authorship_evidence=evidence())
        self.ledger.reserve(candidate)
        self.assertEqual(self.ledger.status()["records"][0]["route_id"], "openai-reviewer")
        own = request(2, kind="review", route_id="openai-reviewer", review_policy="ordinary_pr",
                      candidate_sha="a"*40, material_authors=[{"actor_id": "distinct-reviewer"}], authorship_evidence=evidence())
        with self.assertRaisesRegex(LedgerError, "not_independent"):
            self.ledger.reserve(own)

    def test_ordinary_review_does_not_require_grok_bot_to_disclose_its_family(self):
        data = manifest(True)
        data["routes"][1] = route("grok-bot", provider_identity=None, model_family=None)
        self.ledger.bootstrap(data)
        candidate = request(1, kind="review", review_policy="ordinary_pr", candidate_sha="a"*40,
                            material_authors=[data["routes"][1]], authorship_evidence=evidence())
        self.ledger.reserve(candidate)
        unknown_actor = request(2, kind="review", review_policy="ordinary_pr", candidate_sha="a"*40,
                                material_authors=[{"actor_id": None}], authorship_evidence=evidence())
        with self.assertRaisesRegex(LedgerError, "material_author_actor_id"):
            self.ledger.reserve(unknown_actor)

    def test_paid_billing_unknown_requires_explicit_bound_authorization(self):
        self.bootstrap()
        candidate = request(1)
        del candidate["bounds"]["unverified_billing_authorization"]
        with self.assertRaisesRegex(LedgerError, "billing_authorization"):
            self.ledger.reserve(candidate)
        candidate["bounds"].update(hard_usd_cap_verified=True, usd_limit=1)
        with self.assertRaisesRegex(LedgerError, "usd_cap_evidence"):
            self.ledger.reserve(candidate)

    def test_unsettled_attempt_is_never_automatically_reissued(self):
        self.bootstrap()
        permit = self.start()
        self.assertTrue(permit["dispatch_permitted"])
        self.assertEqual(permit["remaining_bounds"]["calls"], 1)
        self.assertEqual(permit["remaining_bounds"]["assignment_calls_remaining"], 3)
        self.assertIn("deadline_at", permit)
        self.assertIsNone(self.ledger.status()["current_window_checks"]["reported_resource_bounds_ok"])
        with self.assertRaisesRegex(LedgerError, "unknown_no_duplicate"):
            self.ledger.begin_attempt("test-1", transport="local_cli")
        with self.assertRaisesRegex(LedgerError, "ambiguous_settlement"):
            self.ledger.settle("test-1", "failed", evidence())
        self.ledger.settle("test-1", "ambiguous", evidence())
        with self.assertRaisesRegex(LedgerError, "already_terminal"):
            self.ledger.begin_attempt("test-1", transport="browser")

    def test_transient_second_attempt_and_informed_third_attempt_only(self):
        self.bootstrap()
        self.start()
        self.ledger.record_outcome("test-1", 1, result("transient"))
        second = self.ledger.begin_attempt("test-1", transport="local_cli")
        self.assertEqual(second["remaining_bounds"]["turns"], 5)
        self.ledger.record_outcome("test-1", 2, result("failed"))
        with self.assertRaisesRegex(LedgerError, "retry_evidence_insufficient"):
            self.ledger.begin_attempt("test-1", transport="local_cli")
        third = self.ledger.begin_attempt("test-1", transport="browser")
        self.assertEqual(third["attempt_number"], 3)
        self.ledger.record_outcome("test-1", 3, result("failed"))
        with self.assertRaisesRegex(LedgerError, "route_unavailable"):
            self.ledger.begin_attempt("test-1", transport="provider_api")
        with self.assertRaisesRegex(LedgerError, "route_unavailable"):
            self.ledger.reserve(request(2))

    def test_third_attempt_may_use_actual_new_information_evidence(self):
        self.bootstrap()
        self.start()
        for n in (1, 2):
            self.ledger.record_outcome("test-1", n, result("transient"))
            if n == 1:
                self.ledger.begin_attempt("test-1", transport="local_cli")
        permit = self.ledger.begin_attempt("test-1", transport="local_cli", new_information_evidence=evidence("new-observation"))
        self.assertEqual(permit["attempt_number"], 3)

    def test_terminal_first_failure_is_not_a_transient_retry(self):
        self.bootstrap()
        self.start()
        self.ledger.record_outcome("test-1", 1, result("failed"))
        with self.assertRaisesRegex(LedgerError, "requires_transient"):
            self.ledger.begin_attempt("test-1", transport="browser")

    def test_ambiguous_dispatch_never_retries_even_with_new_transport(self):
        self.bootstrap()
        self.start()
        self.ledger.record_outcome("test-1", 1, result("ambiguous"))
        with self.assertRaisesRegex(LedgerError, "must_not_be_repeated"):
            self.ledger.begin_attempt("test-1", transport="browser", new_information_evidence=evidence("new"))
        with self.assertRaisesRegex(LedgerError, "cannot_be_relabelled"):
            self.ledger.settle("test-1", "cancelled", evidence())

    def test_unknown_usage_is_not_zero_and_cannot_fund_a_retry(self):
        self.bootstrap()
        candidate = request(1)
        candidate["bounds"]["usd_limit"] = 1
        self.ledger.reserve(candidate)
        self.ledger.begin_attempt("test-1", transport="local_cli")
        self.ledger.record_outcome("test-1", 1, result("transient", usd=None))
        with self.assertRaisesRegex(LedgerError, "usage_unknown"):
            self.ledger.begin_attempt("test-1", transport="local_cli")
        self.assertIsNone(self.ledger.status()["records"][0]["reported_usage"]["usd"])

    def test_remaining_turn_output_and_cost_bounds_are_enforced(self):
        self.bootstrap()
        for i, exhausted in enumerate(({"turns": 6}, {"output_bytes": 1000}, {"usd": 1})):
            candidate = request(i)
            candidate["bounds"]["usd_limit"] = 1
            self.ledger.reserve(candidate)
            self.ledger.begin_attempt(f"test-{i}", transport="local_cli")
            self.ledger.record_outcome(f"test-{i}", 1, result("transient", **{"usd": 0, **exhausted}))
            with self.assertRaisesRegex(LedgerError, "budget_exhausted|route_unavailable"):
                self.ledger.begin_attempt(f"test-{i}", transport="local_cli")

    def test_timeout_is_assignment_wide_and_clock_is_tested_at_boundary(self):
        self.bootstrap()
        with patch("director_orchestrator.engineering_workload_ledger._now", return_value="2020-01-01T00:00:00Z"):
            self.ledger.reserve(request(1))
        with patch("director_orchestrator.engineering_workload_ledger._now", return_value="2020-01-01T01:00:01Z"):
            with self.assertRaisesRegex(LedgerError, "timeout_budget_exhausted"):
                self.ledger.begin_attempt("test-1", transport="local_cli")

    def test_successful_result_and_settlement_survive_restart(self):
        self.bootstrap()
        self.start()
        self.ledger.record_outcome("test-1", 1, result())
        with self.assertRaisesRegex(LedgerError, "must_not_be_repeated"):
            self.ledger.begin_attempt("test-1", transport="local_cli")
        self.ledger.settle("test-1", "succeeded", evidence())
        restarted = EngineeringWorkloadLedger(self.path)
        self.assertEqual(restarted.status()["records"][0]["state"], "succeeded")
        self.assertFalse(restarted.status()["records"][0]["productive_credit_issued"])
        self.assertEqual(restarted.settle("test-1", "succeeded", evidence())["outcome"], "succeeded")
        with self.assertRaisesRegex(LedgerError, "completion_immutable"):
            restarted.settle("test-1", "failed", evidence())

    def test_attempt_outcomes_cannot_be_relabelled_and_excess_usage_is_retained(self):
        self.bootstrap()
        self.start()
        self.ledger.record_outcome("test-1", 1, result(output_bytes=1001))
        self.assertTrue(self.ledger.record_outcome("test-1", 1, result(output_bytes=1001))["already_recorded"])
        with self.assertRaisesRegex(LedgerError, "outcome_immutable"):
            self.ledger.record_outcome("test-1", 1, result(output_bytes=0))
        self.assertEqual(self.ledger.status()["records"][0]["reported_bound_violations"], ["output_bytes"])

    def test_cli_reports_missing_history_and_previews_without_creating_a_db(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "engineering-workload.py"
        command = [sys.executable, str(script), "--db", str(self.path), "status"]
        output = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(output.stdout)["history_mode"], "unknown")
        self.assertFalse(self.path.exists())
        proposal = Path(self.temp.name) / "proposal.json"
        proposal.write_text(json.dumps(manifest(True)["policy"]), encoding="utf-8")
        output = subprocess.run([sys.executable, str(script), "policy-preview", "--input", str(proposal)], capture_output=True, text=True, check=True)
        self.assertFalse(json.loads(output.stdout)["dispatch_authorized"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:04 UTC | END]]
