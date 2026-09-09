# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:12 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:22 UTC | BEGIN]]
"""Readiness must be derived from inputs, never defaulted to a hoped-for value."""

from __future__ import annotations

import unittest

from director_orchestrator.launch_status import (
    NON_OPTIONAL_CHECKS,
    OWNER_TOUCHPOINTS,
    Source,
    done_terms,
    estimated_duration,
    max_speedup,
    owner_touchpoints,
    remaining_k,
    should_recheck,
)

# The live stack as measured on 2026-09-08.
# An illustrative health document, NOT a measurement of the live stack. An
# earlier comment called it "the live stack as measured on 2026-09-08"; a
# review compared it against real configuration readiness on this host and
# found it names fields that readiness does not contain, and that the live
# figures were 0 of 5 with k=22 rather than the 2 and 20 claimed. It exists to
# exercise the mapping. Every expectation below is pinned to it by name, so a
# hardcoded answer that happens to match a total no longer passes.
TODAY = {
    "langgraph_primary": {
        "new_intake_owner": "langgraph",
        "primary_runtime_active": False,
        "live_provider_dispatch_requested": False,
        "effect_ledger_ready": True,
        "provider_activation_bindings_configured": False,
        "authority_root_configured": True,
        "authority_pins_valid": True,
        "completion_evidence_actor_allowlist_configured": True,
    }
}


class DoneConjunctionTests(unittest.TestCase):
    def test_the_conjunction_reports_exactly_the_named_terms(self):
        """Pin the names and their holds, not a count against a hoped-for total.

        This asserted len(terms)==5 and len(holding)==2 against a fixture
        authored to match the commit message. An independent review of #150
        hardcoded the two flags true and ignored the health document
        altogether, and this test still passed.
        """

        terms = done_terms(TODAY)
        self.assertEqual(
            [t.name for t in terms],
            [
                "intake_reaches_the_primary_plane",
                "real_execution_of_a_wave",
                "durable_result_store_ready",
                "provider_binding_map_configured",
                "recovery_proof",
            ],
        )
        self.assertEqual(
            {t.name: t.holds for t in terms},
            {
                "intake_reaches_the_primary_plane": True,
                "real_execution_of_a_wave": False,
                "durable_result_store_ready": True,
                "provider_binding_map_configured": False,
                "recovery_proof": False,
            },
        )

    def test_a_configured_plane_is_not_an_executed_wave(self):
        """Both switches on with no task run is not execution.

        Reproduced by a review at graph_task_count=0: the term held on the two
        readiness flags alone.
        """

        primary = dict(TODAY["langgraph_primary"])
        primary["primary_runtime_active"] = True
        primary["live_provider_dispatch_requested"] = True
        primary["graph_task_count"] = 0
        term = next(
            t for t in done_terms({"langgraph_primary": primary})
            if t.name == "real_execution_of_a_wave"
        )
        self.assertFalse(term.holds)
        primary["graph_task_count"] = 1
        term = next(
            t for t in done_terms({"langgraph_primary": primary})
            if t.name == "real_execution_of_a_wave"
        )
        self.assertTrue(term.holds)

    def test_a_json_false_string_never_satisfies_a_term(self):
        """bool("false") is True, and a health document is JSON off the wire.

        Constructed by a review: effect_ledger_ready="false" produced a holding
        term whose evidence read effect_ledger_ready=True.
        """

        for bogus in ("false", "0", 0, 1, {}, {"satisfied": False}, [], None):
            primary = dict(TODAY["langgraph_primary"])
            primary["effect_ledger_ready"] = bogus
            term = next(
                t for t in done_terms({"langgraph_primary": primary})
                if t.name == "durable_result_store_ready"
            )
            self.assertFalse(term.holds, repr(bogus))
            self.assertNotIn("=True", term.evidence, repr(bogus))

    def test_every_condition_of_a_wave_is_separately_necessary(self):
        """Drop any one of the three and the term must stop holding.

        This previously asserted that runtime plus live dispatch was
        sufficient. It is not: a review reproduced both switches on with
        graph_task_count=0, which is a plane configured to run rather than one
        that has. Sufficiency is pinned in
        test_a_configured_plane_is_not_an_executed_wave; this pins necessity,
        one condition at a time, so no single condition can quietly drop out
        of the conjunction.
        """

        met = dict(TODAY["langgraph_primary"])
        met.update(
            primary_runtime_active=True,
            live_provider_dispatch_requested=True,
            graph_task_count=3,
        )
        term = next(
            t for t in done_terms({"langgraph_primary": met})
            if t.name == "real_execution_of_a_wave"
        )
        self.assertTrue(term.holds)

        for field, unmet in (
            ("primary_runtime_active", False),
            ("live_provider_dispatch_requested", False),
            ("graph_task_count", 0),
        ):
            primary = dict(met)
            primary[field] = unmet
            term = next(
                t for t in done_terms({"langgraph_primary": primary})
                if t.name == "real_execution_of_a_wave"
            )
            self.assertFalse(term.holds, field)

    def test_recovery_proof_is_attested_and_defaults_to_false(self):
        """No health field reports a restore drill, so it must not be assumed."""

        term = next(t for t in done_terms(TODAY) if t.name == "recovery_proof")
        self.assertIs(term.source, Source.ATTESTED)
        self.assertFalse(term.holds)
        supplied = next(
            t for t in done_terms(TODAY, restore_drill_recorded=True)
            if t.name == "recovery_proof"
        )
        self.assertTrue(supplied.holds)

    def test_an_empty_health_document_holds_nothing(self):
        """A missing field is not a satisfied term."""

        self.assertEqual([t for t in done_terms({}) if t.holds], [])

    def test_every_term_carries_its_evidence(self):
        """An empty tuple satisfies "every term" vacuously.

        A review returned () from done_terms and this passed -- the same
        empty-collection hole an earlier round pinned on NON_OPTIONAL_CHECKS
        and left open here. The population is pinned before it is walked.
        """

        terms = done_terms(TODAY)
        self.assertEqual(len(terms), 5)
        for term in terms:
            self.assertTrue(term.evidence, term.name)

    def test_an_attested_term_reads_differently_when_it_is_unmet(self):
        """A constant caption is not evidence; it read the same either way."""

        absent = next(t for t in done_terms(TODAY) if t.name == "recovery_proof")
        present = next(
            t for t in done_terms(TODAY, restore_drill_recorded=True)
            if t.name == "recovery_proof"
        )
        self.assertNotEqual(absent.evidence, present.evidence)
        self.assertFalse(absent.holds)
        self.assertTrue(present.holds)
        # And it never claims to have checked anything.
        self.assertIn("not verified here", present.evidence)


class TouchpointTests(unittest.TestCase):
    def test_k_counts_only_outstanding_touchpoints(self):
        """Pin the names and the number, not an identity that always holds.

        `remaining_k == len(OWNER_TOUCHPOINTS) - len(satisfied)` is true by
        construction. A review marked every attested touchpoint satisfied and
        this passed; so did hardcoding remaining_k to 20.
        """

        satisfied = {t.name for t in owner_touchpoints(TODAY) if t.satisfied}
        self.assertEqual(satisfied, {"snapshot_root", "actor_allowlist"})
        self.assertEqual(len(OWNER_TOUCHPOINTS), 23)
        self.assertEqual(remaining_k(TODAY), 21)

    def test_authority_pins_is_not_settled_by_a_field_true_when_unset(self):
        """authority_pins_valid returns true when both pins are empty.

        Using it as the probe credited this touchpoint on a default
        configuration and reduced k with no evidence that any pin was set.
        Found by a review of #150 against the real Settings.
        """

        self.assertIsNone(dict(OWNER_TOUCHPOINTS)["authority_pins"])
        primary = dict(TODAY["langgraph_primary"])
        primary["authority_pins_valid"] = True
        pins = next(
            t for t in owner_touchpoints({"langgraph_primary": primary})
            if t.name == "authority_pins"
        )
        self.assertFalse(pins.satisfied)
        self.assertIs(pins.source, Source.ATTESTED)

    def test_an_unprobeable_touchpoint_is_never_reported_satisfied(self):
        """Only the owner can settle these, so they may not default to done.

        Emptying OWNER_TOUCHPOINTS passed this vacuously in a review, so the
        attested population is pinned before it is walked.
        """

        attested = [t for t in owner_touchpoints(TODAY) if t.source is Source.ATTESTED]
        self.assertEqual(len(attested), 19)
        for touch in owner_touchpoints(TODAY):
            if touch.source is Source.ATTESTED:
                self.assertFalse(touch.satisfied, touch.name)

    def test_k_falls_only_when_the_health_document_says_so(self):
        before = remaining_k(TODAY)
        health = {"langgraph_primary": dict(TODAY["langgraph_primary"])}
        health["langgraph_primary"]["provider_activation_bindings_configured"] = True
        self.assertEqual(remaining_k(health), before - 1)


class ScheduleTests(unittest.TestCase):
    def test_owner_latency_dominates_when_k_is_large(self):
        """20 touchpoints at 4h each dwarfs the agent work; batching is the lever."""

        unbatched = estimated_duration(20, 4.0, 10.0)
        batched = estimated_duration(3, 4.0, 10.0)
        self.assertEqual(unbatched, 90.0)
        self.assertEqual(batched, 22.0)
        self.assertLess(batched, unbatched / 3)

    def test_amdahl_caps_agent_speedup_on_a_serial_chain(self):
        """Four fifths serial gives at most 1.25x, however many agents."""

        self.assertAlmostEqual(max_speedup(0.8, 10**6), 1.25, places=4)
        self.assertLessEqual(max_speedup(0.8, 1000), 1.25)

    def test_a_fully_serial_chain_gains_nothing(self):
        self.assertEqual(max_speedup(1.0, 1000), 1.0)

    def test_negative_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            estimated_duration(-1, 4.0, 10.0)
        with self.assertRaises(ValueError):
            max_speedup(1.5, 4)


class StoppingRuleTests(unittest.TestCase):
    def test_security_and_review_gates_are_never_optional(self):
        # Named explicitly. Iterating the set alone would pass after the set
        # was emptied, which is the mutation that slipped through first.
        for required in ("security", "approval", "exact_sha_review", "ci"):
            self.assertIn(required, NON_OPTIONAL_CHECKS, required)

        for check in NON_OPTIONAL_CHECKS:
            run, reason = should_recheck(
                check,
                state_changed_since_last_result=False,
                probability_result_changes_decision=0.0,
                cost_of_wrong_decision=0.0,
                cost_of_check=10**6,
            )
            self.assertTrue(run, check)
            self.assertIn("never optional", reason)

    def test_no_state_change_means_no_recheck(self):
        run, reason = should_recheck(
            "profile_render",
            state_changed_since_last_result=False,
            probability_result_changes_decision=1.0,
            cost_of_wrong_decision=10**6,
            cost_of_check=1.0,
        )
        self.assertFalse(run)
        self.assertIn("no state change", reason)

    def test_a_cheap_check_against_an_expensive_mistake_runs(self):
        run, _ = should_recheck(
            "profile_render",
            state_changed_since_last_result=True,
            probability_result_changes_decision=0.1,
            cost_of_wrong_decision=1000.0,
            cost_of_check=1.0,
        )
        self.assertTrue(run)

    def test_an_expensive_check_against_a_trivial_mistake_does_not(self):
        run, _ = should_recheck(
            "profile_render",
            state_changed_since_last_result=True,
            probability_result_changes_decision=0.1,
            cost_of_wrong_decision=1.0,
            cost_of_check=1000.0,
        )
        self.assertFalse(run)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:22 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:12 UTC | END]]
