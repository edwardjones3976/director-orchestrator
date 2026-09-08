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
    def test_todays_state_is_two_of_five(self):
        terms = done_terms(TODAY)
        holding = [t.name for t in terms if t.holds]
        self.assertEqual(len(terms), 5)
        self.assertEqual(len(holding), 2)
        self.assertIn("intake_reaches_the_primary_plane", holding)
        self.assertIn("durable_results", holding)

    def test_a_wave_needs_both_runtime_and_live_dispatch(self):
        """Runtime active alone is not execution; dispatch must be requested."""

        health = {"langgraph_primary": dict(TODAY["langgraph_primary"])}
        health["langgraph_primary"]["primary_runtime_active"] = True
        term = next(t for t in done_terms(health) if t.name == "real_execution_of_a_wave")
        self.assertFalse(term.holds)
        health["langgraph_primary"]["live_provider_dispatch_requested"] = True
        term = next(t for t in done_terms(health) if t.name == "real_execution_of_a_wave")
        self.assertTrue(term.holds)

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
        for term in done_terms(TODAY):
            self.assertTrue(term.evidence, term.name)


class TouchpointTests(unittest.TestCase):
    def test_k_counts_only_outstanding_touchpoints(self):
        satisfied = [t.name for t in owner_touchpoints(TODAY) if t.satisfied]
        self.assertIn("snapshot_root", satisfied)
        self.assertIn("authority_pins", satisfied)
        self.assertNotIn("provider_binding_map", satisfied)
        self.assertEqual(remaining_k(TODAY), len(OWNER_TOUCHPOINTS) - len(satisfied))

    def test_an_unprobeable_touchpoint_is_never_reported_satisfied(self):
        """Only the owner can settle these, so they may not default to done."""

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
