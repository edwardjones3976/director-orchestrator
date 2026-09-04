# [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:45 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | BEGIN]]
"""Contract tests for the human-out-of-the-loop delegation framework.

The framework is documented authority only: it defines who may supply a verdict
when the owner is absent, and it must never be readable as removing a gate.
These tests pin the properties that make it safe to have written down at all.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "human-out-of-the-loop-operation.md"


class HumanOutOfTheLoopAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        # read_text opens in text mode with newline=None, so a CRLF checkout
        # is already folded to LF before any assertion below sees it.
        self.text = DOCUMENT.read_text(encoding="utf-8")
        self.agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.charter = (ROOT / "docs" / "team-operating-charter.md").read_text(encoding="utf-8")

    def section(self, heading: str) -> str:
        return self.text.split(f"\n## {heading}", 1)[1].split("\n## ", 1)[0]

    def test_document_is_documented_authority_only_and_default_off(self) -> None:
        status = self.text.split("**Status:**", 1)[1].split("**Schema authority:**", 1)[0]
        self.assertIn("Documented authority only", status)
        self.assertIn("Default off", status)
        self.assertIn("grants nothing", status)
        self.assertIn("director-delegated-authority/v1", self.text)
        # The frozen starting point must be an exact commit, not a branch name.
        frozen = self.text.split("**Frozen starting point:**", 1)[1].split("\n", 1)[0]
        self.assertRegex(frozen, r"`[0-9a-f]{40}`")

    def test_delegation_never_removes_a_gate(self) -> None:
        self.assertIn("who supplies a verdict", self.text)
        self.assertIn("whether a gate runs", self.text)
        for promise in (
            "No deterministic gate is removed, relaxed, skipped, or made advisory",
            "strictly more evidence",
        ):
            self.assertIn(promise, self.text)
        relationship = self.section("Relationship to the existing rules")
        self.assertIn("rule 3 is unchanged", relationship)
        self.assertIn("rule 10 is unchanged as the default", relationship)
        self.assertIn("may never\n  remove them", relationship)

    def test_three_states_default_to_attended_and_fail_closed(self) -> None:
        states = self.section("Operating states")
        for state in ("`ATTENDED`", "`DELEGATED`", "`SUSPENDED`"):
            self.assertIn(state, states)
        self.assertIn("starts in `ATTENDED`", states)
        self.assertIn("absent, invalid, expired, or unreadable", states)
        # Recovery from SUSPENDED is never automatic.
        self.assertIn("explicit owner action", states)

    def test_tier_zero_classes_are_never_delegable(self) -> None:
        classes = self.section("Decision classes")
        tier_zero = classes.split("### Tier 0", 1)[1]
        for never in (
            "Production deployment",
            "Publication",
            "Deletion of data",
            "Purchases",
            "Credential, secret, token, or access-scope changes",
            "protected branch",
            "Editing this document",
        ):
            self.assertIn(never, tier_zero)
        # The enumeration must fail closed rather than defaulting to delegable.
        self.assertIn("fails closed", tier_zero)
        self.assertIn("is tier 0 until", tier_zero)

    def test_evidence_bar_is_conjunctive_and_complete(self) -> None:
        bar = self.section("The evidence bar")
        self.assertIn("every", bar)
        self.assertIn("conjunctive", bar)
        self.assertIn("no majority", bar.replace("no weighting, no majority", "no majority"))
        items = re.findall(r"^\s*(\d+)\. ", bar, re.M)
        self.assertEqual(items, [str(n) for n in range(1, 11)])
        for requirement in (
            "required CI check is green",
            "Independent security evidence",
            "neither the producer nor a material author",
            "three distinct\n   identities",
            "`owned_paths`",
            "`prohibited_paths`",
            "durably written before",
        ):
            self.assertIn(requirement, bar)
        self.assertIn("Absence of evidence is never\nconsent", bar)
        self.assertIn("out of quota, unauthenticated, or silent has not\napproved anything", bar)

    def test_two_verdicts_must_come_from_genuinely_distinct_providers(self) -> None:
        quorum = self.section("Why two independent verdicts")
        self.assertIn("single point of failure", quorum)
        self.assertIn("share a backing\nmodel are not distinct", quorum)
        self.assertIn("two runs of the same\nprovider", quorum)
        self.assertIn("property of the failure mode", quorum)

    def test_grants_are_bounded_and_revocable_and_do_not_compose(self) -> None:
        grants = self.section("Grants")
        for bound in ("expiry", "maximum number of exercises", "revocation switch"):
            self.assertIn(bound, grants)
        self.assertIn("before the next decision, not after", grants)
        self.assertIn("do not compose, inherit, or extend by\nanalogy", grants)

    def test_trip_conditions_include_an_unwritable_ledger(self) -> None:
        trips = self.section("Trip conditions")
        for trip in (
            "a gate result changes",
            "a new commit appears",
            "`SERIOUS_SAFETY_STOP`",
            "reverses two consecutive delegated decisions",
            "quota failure",
            "audit ledger cannot be written",
        ):
            self.assertIn(trip, trips)
        audit = self.section("Audit record")
        self.assertIn("before the action, not after", audit)

    def test_execution_is_phased_and_no_phase_executes_yet(self) -> None:
        phases = self.section("Implementation phases")
        self.assertIn("separately built, reviewed, and accepted", phases)
        self.assertIn("acceptance of one grants nothing to the next", phases)
        # Phases 0 through 3 must not execute a decision.
        rows = [row for row in phases.splitlines() if row.startswith("| ")]
        executes = [row.rsplit("|", 2)[1].strip() for row in rows if re.match(r"\|\s*\d", row)]
        self.assertEqual(executes[:4], ["No", "No", "No", "No"])

    def test_limitations_are_stated_including_the_authorship_conflict(self) -> None:
        limits = self.section("Known limitations")
        self.assertIn("not equivalent to human judgment", limits)
        self.assertIn("correlated ways", limits)
        self.assertIn("untrusted material", limits)
        self.assertIn("has not yet produced a verdict", limits)
        # An agent wrote the rules that govern agents; the document must say so.
        self.assertIn("conflict of interest", limits)
        self.assertIn("drafted by an agent", limits)

    def test_agents_rules_keep_stopping_as_the_default(self) -> None:
        rule_ten = next(line for line in self.agents.splitlines() if line.startswith("10. "))
        self.assertIn("Stopping is the default and stays the default", rule_ten)
        self.assertIn("docs/human-out-of-the-loop-operation.md", rule_ten)
        self.assertIn("never removes a deterministic gate", rule_ten)
        # Rule 3's no-self-merge bar must survive verbatim.
        rule_three = next(line for line in self.agents.splitlines() if line.startswith("3. "))
        self.assertIn("merge your own pull request", rule_three)
        section = self.agents.split("\n## Human-out-of-the-loop operation", 1)[1].split("\n## ", 1)[0]
        for required in (
            "default off",
            "`ATTENDED`",
            "grants nothing",
            "never changes whether a gate runs",
            "never delegable under any configuration",
            "three distinct identities",
            "Absence of evidence is never consent",
        ):
            self.assertIn(required, section)

    def test_review_gate_is_satisfied_by_a_provider_verdict_not_substituted(self) -> None:
        section = self.section("How the existing gates are satisfied, not substituted")
        self.assertIn("That sentence stays exactly as written", section)
        self.assertIn("never consumed an owner verdict", section)
        self.assertIn("least-recent eligible non-author", section)
        self.assertIn("satisfied in its ordinary\nform", section)
        self.assertIn("strictly higher review bar", section)
        # The charter sentence it defers to must still exist verbatim.
        self.assertIn(
            "never substitutes for applicable deterministic\napproval, exact-SHA review, security, CI, budget, allowlist, or rollback gates.",
            self.charter,
        )
        self.assertIn("not an authorization substituting for that gate", self.charter)

    def test_no_self_merge_bar_never_lifts_for_any_agent(self) -> None:
        section = self.section("How the existing gates are satisfied, not substituted")
        self.assertIn("That bar\nnever lifts", section)
        self.assertIn("does not create an exception", section)
        self.assertIn("Two agents are not one\nagent", section)
        self.assertIn("permanently ineligible", section)
        self.assertIn("no\ngrant can make it eligible", section)
        # A single available agent must be unable to merge at all.
        self.assertIn("cannot proceed at all", section)
        rule_three = next(line for line in self.agents.splitlines() if line.startswith("3. "))
        self.assertIn("merge your own pull request", rule_three)
        self.assertIn("per acting identity", rule_three)
        self.assertIn("that bar never lifts", rule_three)
        self.assertIn("three distinct identities", rule_three)
        self.assertIn("an author cannot become eligible by being delegated to", rule_three)

    def test_charter_points_at_the_framework_without_weakening_its_gates(self) -> None:
        self.assertIn("docs/human-out-of-the-loop-operation.md", self.charter)
        # The sentence the framework hangs from must survive unchanged.
        self.assertIn(
            "never substitutes for applicable deterministic\napproval, exact-SHA review, security, CI, budget, allowlist, or rollback gates.",
            self.charter,
        )
        self.assertIn("it never removes a\ngate named above", self.charter)
        handoff = self.charter.split("\n## Review-to-merge handoff", 1)[1].split("\n## ", 1)[0]
        self.assertIn("three\ndistinct identities", handoff)
        self.assertIn("does not alter what a review must contain", handoff)


if __name__ == "__main__":
    unittest.main()


# [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]]
