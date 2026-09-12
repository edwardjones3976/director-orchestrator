# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:54 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 17:56 UTC | BEGIN]]
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

    def test_tier_one_is_a_closed_set_that_excludes_every_tier_zero_act(self) -> None:
        # The tier-0 test below checks that certain phrases appear under tier 0.
        # That cannot catch the failure mode this document exists to prevent:
        # widening tier 1. This pins the delegable set exactly, so adding a class
        # to it is a test failure rather than a silent policy change.
        classes = self.section("Decision classes")
        tier_one = classes.split("### Tier 1", 1)[1].split("### Tier 0", 1)[0]
        bullets = [
            " ".join(line.lstrip("- ").split())
            for line in tier_one.splitlines()
            if line.startswith("- ")
        ]
        self.assertEqual(
            bullets,
            [
                "Executing an eligible merge of a pull request into a non-protected branch.",
                "Marking a pull request ready for review.",
                "Posting to the project's own designated Slack channel.",
            ],
        )
        # Scope the remaining checks to the delegable acts themselves. The prose
        # around them legitimately discusses what may not be delegated.
        delegable = " ".join(bullets).lower()
        # No residual or catch-all class may reappear.
        for weasel in ("routine", "such as", "including", "for example", "other similar"):
            self.assertNotIn(weasel, delegable)
        # No tier-0 act may leak into the delegable set.
        for forbidden in (
            "deployment",
            "publication",
            "deletion",
            "purchase",
            "billing",
            "credential",
            "secret",
            # "non-protected branch" is legitimate in bullet one, so match the act.
            "into a protected branch",
        ):
            self.assertNotIn(forbidden, delegable)

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
            "two distinct identities",
            "`owned_paths`",
            "`prohibited_paths`",
            "durably written before",
        ):
            self.assertIn(requirement, bar)
        self.assertIn("Absence of evidence is never\nconsent", bar)
        self.assertIn("out of quota, unauthenticated, or silent has not\napproved anything", bar)

    def test_the_one_verdict_must_come_from_a_genuinely_distinct_provider(self) -> None:
        quorum = self.section("Why one independent verdict")
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
        self.assertIn("cancelled session", limits)
        self.assertIn("reported to the owner as empty", limits)
        self.assertNotIn("has not yet produced a verdict", limits)
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
            "distinct identities",
            "Absence of evidence is never consent",
        ):
            self.assertIn(required, section)

    def test_review_gate_is_satisfied_by_a_provider_verdict_not_substituted(self) -> None:
        section = self.section("How the existing gates are satisfied, not substituted")
        self.assertIn("That sentence stays exactly as written", section)
        self.assertIn("never consumed an owner verdict", section)
        self.assertIn("least-recent eligible non-author", section)
        self.assertIn("satisfied in its ordinary\nform", section)
        self.assertIn("the same review bar", section)
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
        self.assertIn("two distinct identities", rule_three)
        self.assertIn("an author cannot become eligible by being delegated to", rule_three)

    def test_identity_terms_follow_the_landed_authorship_boundary(self) -> None:
        # The evidence bar turns on who the producer and material authors are.
        # The charter's authorship boundary defines that, and it excludes
        # control-plane work, so this framework must not be readable as turning
        # a coordinator into an author or an author into an eligible actor.
        relationship = " ".join(
            self.section("Relationship to the existing rules").split()
        )
        self.assertIn("Director and provider authorship boundary", relationship)
        self.assertIn(
            "merge execution create no authorship, so performing them neither"
            " makes an identity the producer for evidence-bar items 3, 4, and 5"
            " nor makes an actual author eligible under them",
            relationship,
        )
        boundary = " ".join(
            self.charter.split("\n## Director and provider authorship boundary", 1)[1]
            .split("\n## ", 1)[0]
            .split()
        )
        self.assertIn(
            "those control-plane activities create no code authorship", boundary
        )
        self.assertIn("removes no deterministic gate", boundary)
        agents_boundary = " ".join(
            self.agents.split("\n## Director and provider authorship", 1)[1]
            .split("\n## ", 1)[0]
            .split()
        )
        self.assertIn("does not inherit authorship through delegation", agents_boundary)
        self.assertIn(
            "may not independently accept or merge code it actually materially"
            " authored",
            agents_boundary,
        )

    def test_charter_points_at_the_framework_without_weakening_its_gates(self) -> None:
        self.assertIn("docs/human-out-of-the-loop-operation.md", self.charter)
        # The sentence the framework hangs from must survive unchanged.
        self.assertIn(
            "never substitutes for applicable deterministic\napproval, exact-SHA review, security, CI, budget, allowlist, or rollback gates.",
            self.charter,
        )
        self.assertIn("it never removes a\ngate named above", self.charter)
        handoff = self.charter.split("\n## Review-to-merge handoff", 1)[1].split("\n## ", 1)[0]
        self.assertIn("two distinct identities", handoff)
        self.assertIn("does not alter what a review must contain", handoff)




# [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 04:45 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 17:56 UTC | END]]


class ApproverCarriesTheMergeTests(unittest.TestCase):
    """The section no existing test read.

    The suite above slices the HOTL section at the next `##`, so "Approver
    carries the merge" was never inspected by anything. An independent review of
    #151 constructed the consequence: its operative first sentence read as one
    exact-SHA ACCEPT carrying a candidate to a terminal result, with the
    two-provider clause reading as who executes rather than as a precondition.
    An agent following that sentence alone could merge on one verdict, which is
    weaker than AGENTS.md rule 10 and than the evidence bar. Both files are
    pinned here so a later section cannot weaken them untested again.
    """

    FILES = ("AGENTS.md", "docs/team-operating-charter.md")

    def _section(self, name: str) -> str:
        """The section body, whitespace-collapsed and stripped of emphasis.

        Matching raw markdown is unreliable for prose: `**One\\n`ACCEPT` is not
        merge authority.**` wraps mid-phrase and carries inline emphasis, so a
        plain substring test fails on a sentence that is plainly present. That
        already cost a round on a different document. Normalise, then match.
        """

        text = (ROOT / name).read_text(encoding="utf-8")
        marker = "Approver carries the merge"
        self.assertIn(marker, text, name)
        body = text.split(marker, 1)[1]
        # Stop at the next heading of any depth, so the section cannot be
        # extended past its own boundary to satisfy these assertions.
        for boundary in ("\n## ", "\n### "):
            body = body.split(boundary, 1)[0]
        return " ".join(body.replace("**", "").split())

    def _operative(self, name: str) -> str:
        """Only the operative paragraphs, not the whole section.

        Asserting against the section as a whole was itself too weak: "live
        grant" and "ATTENDED" also appear in the blocker list further down, so
        deleting them from the operative sentence left both assertions passing.
        The finding this section exists to fix was precisely that a walk-back
        elsewhere does not repair an operative sentence, so the operative
        sentence is what gets pinned.
        """

        body = self._section(name)
        # End on a marker both files carry, immediately after the identity
        # paragraph. Ending on an AGENTS.md-only phrase made the WHOLE charter
        # section operative, so moving the live grant, the one verdict and
        # ATTENDED into its closing walk-back still satisfied every assertion.
        # A walk-back is exactly what does not repair an operative sentence.
        end = body.find("no-self-merge bar")
        self.assertGreater(end, 0, f"{name}: no shared end marker")
        return body[:end]

    def test_the_first_operative_sentence_is_the_disclaimer(self):
        """Restoring the old sentence as the opening kept all five tests green.

        Asserting the disclaimer appears SOMEWHERE in the operative text let
        "an eligible reviewer that returns an exact-SHA ACCEPT carries that
        candidate to a terminal result" go back in front of it, which is the
        exact sentence a review of #151 showed an agent could merge on. What
        matters is which sentence is read first.
        """

        for name in self.FILES:
            operative = self._operative(name)
            disclaimer = operative.index("One `ACCEPT` is not merge authority")
            carries = operative.find("carries that candidate to a terminal result")
            self.assertEqual(
                carries, -1,
                f"{name}: the superseded operative sentence is back",
            )
            # Nothing but a lead-in may precede it.
            self.assertLess(disclaimer, 200, f"{name}: the disclaimer is buried")

    def test_one_accept_is_never_stated_as_merge_authority(self):
        for name in self.FILES:
            operative = self._operative(name)
            self.assertIn("One `ACCEPT` is not merge authority", operative, name)
            self.assertIn("does not authorise the merge", operative, name)
            self.assertIn("OWNER_DECISION_REQUIRED", operative, name)
            # The preconditions belong in the operative sentence itself.
            # "a live grant under" rather than "live grant": the latter also
            # occurs in the ATTENDED sentence a few lines down, so deleting the
            # precondition entirely still satisfied it.
            self.assertIn("a live grant under", operative, name)
            self.assertIn("one exact-SHA `ACCEPT`", operative, name)
            self.assertIn("every other item of the evidence bar", operative, name)

    def test_the_reviewer_that_accepts_is_the_one_that_merges(self):
        for name in self.FILES:
            operative = self._operative(name)
            self.assertIn("performs", operative, name)
            self.assertIn("accepts and merges the PR", operative, name)
            self.assertIn("returns it to the author with notes", operative, name)

    def test_the_attended_default_names_the_blocker_instead_of_merging(self):
        for name in self.FILES:
            operative = self._operative(name)
            self.assertIn("ATTENDED", operative, name)
            self.assertIn("not to merge", operative, name)
            # And the blocker list further down must offer it as a reason.
            self.assertIn("no live grant", self._section(name), name)

    def test_scope_is_stated_as_non_protected_branches_only(self):
        """The combined phrase. "only" alone also matches "owner-only".

        Softening the charter to "The obligation reaches non-protected
        branches." kept this green, because the word "only" survived elsewhere
        in the paragraph.
        """

        for name in self.FILES:
            self.assertIn("non-protected branches only", self._section(name), name)

    def test_the_tier_one_class_is_not_described_as_already_assigned(self):
        """Equating the tier-1 entry with this duty reads as turning it on.

        Banning one exact string is not enough: "exactly the act this section
        assigns" says the same thing and passed. Every paraphrase that equates
        them is rejected, and the section must state the grant condition
        instead.
        """

        for name in self.FILES:
            body = self._section(name)
            for equating in (
                "exactly the act assigned here",
                "exactly the act this section assigns",
                "the act assigned here",
                "the act this section assigns",
            ):
                self.assertNotIn(equating, body, f"{name}: {equating}")
            # And the condition that actually governs it is stated.
            self.assertIn("only while a live grant names it", body, name)
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:54 UTC | END]]


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:40 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:27 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | END]]
