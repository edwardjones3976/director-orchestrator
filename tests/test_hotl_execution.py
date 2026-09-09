# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | BEGIN]]
"""The predicate must fail closed, and a hold must name its blocker."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from director_orchestrator.hotl_execution import (
    NON_RECOVERABLE_TERMS,
    TERMS,
    TIER_ONE_CLASSES,
    CompletionRate,
    Decision,
    Outcome,
    ProposedAction,
    TermEvidence,
    classify,
    decide,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "human-out-of-the-loop-operation.md"

MERGE = ProposedAction(
    action_class="eligible_merge_non_protected_branch",
    repository="edwardjones3976/director-orchestrator",
    environment="local",
    candidate_sha="0" * 40,
    acting_identity="grok",
)


def _all(satisfied: bool = True, **overrides: TermEvidence) -> dict[str, TermEvidence]:
    evidence = {
        term: TermEvidence(True) if satisfied else TermEvidence(False, f"{term} unmet")
        for term in TERMS
    }
    evidence.update(overrides)
    return evidence


class TierTests(unittest.TestCase):
    def test_tier_one_matches_the_document_exactly(self):
        """The closed list is the document's, not a superset invented here.

        This compared len(bullets) against len(TIER_ONE_CLASSES) and nothing
        else, so any three identifiers passed -- including three the document
        never names. Found by an independent review of #146. It now binds each
        identifier to the bullet it claims to encode, in both directions.
        """

        section = DOC.read_text(encoding="utf-8").split("### Tier 1", 1)[1]
        section = section.split("### Tier 0", 1)[0]
        bullets = [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]

        # The words each identifier asserts are present in its own bullet. A
        # renamed identifier stops matching; a reworded bullet stops matching;
        # an added or removed bullet breaks the bijection below.
        encodes = {
            "eligible_merge_non_protected_branch": ("eligible merge", "non-protected branch"),
            "mark_pull_request_ready_for_review": ("marking a pull request ready for review",),
            "post_to_own_designated_slack_channel": ("designated slack channel",),
        }
        self.assertEqual(set(encodes), set(TIER_ONE_CLASSES))

        matched = {}
        for identifier, phrases in encodes.items():
            hits = [b for b in bullets if all(p in b.lower() for p in phrases)]
            self.assertEqual(len(hits), 1, f"{identifier} matched {len(hits)} bullets")
            matched[identifier] = hits[0]

        # Bijection: every bullet is claimed exactly once, so a fourth class
        # added to the document fails here instead of being silently omitted
        # from the code.
        self.assertEqual(sorted(matched.values()), sorted(bullets))

    def test_an_unlisted_class_is_tier_zero(self):
        for unlisted in ("deploy_to_production", "rotate_credential", "routine_merge", ""):
            action = ProposedAction(unlisted, "r", "local", "0" * 40, "grok")
            self.assertEqual(classify(action), 0, unlisted)

    def test_a_tier_zero_action_never_executes_even_with_every_term_met(self):
        """No combination of satisfied terms may promote a tier-0 act."""

        action = ProposedAction("deploy_to_production", "r", "local", "0" * 40, "grok")
        decision = decide(action, _all(True))
        self.assertIs(decision.outcome, Outcome.HOLD)
        self.assertFalse(decision.autonomous)
        self.assertEqual(decision.tier, 0)
        self.assertTrue(all(decision.terms.values()))

    def test_a_tier_zero_hold_names_the_class_not_a_term(self):
        action = ProposedAction("rotate_credential", "r", "local", "0" * 40, "grok")
        self.assertIn("not a tier 1 class", decide(action, _all(True)).blockers[0])


class GoverningDocumentTests(unittest.TestCase):
    def _main_copy(self):
        import subprocess

        result = subprocess.run(
            ["git", "show", "github/main:docs/human-out-of-the-loop-operation.md"],
            capture_output=True, text=True, cwd=ROOT,
        )
        if result.returncode != 0:
            self.skipTest("github/main is unavailable in this environment")
        crlf, lf = chr(13) + chr(10), chr(10)
        return result.stdout.replace(crlf, lf)

    def _bullets(self, text, heading):
        section = text.split(heading, 1)[1].split(chr(10) + "#", 1)[0]
        return [line.strip() for line in section.splitlines() if line.startswith("- ")]

    def test_the_owner_may_extend_the_document_but_no_gate_may_be_removed(self):
        """Rule 4: this may add approval requirements, never remove one.

        The owner directed this edit, which is a tier-0 act only the owner
        can authorise. What an agent still may not do is weaken a gate while
        editing, so every tier-0 entry on main must survive verbatim and
        tier 1 must stay closed at its existing size.
        """

        crlf, lf = chr(13) + chr(10), chr(10)
        current = DOC.read_text(encoding="utf-8").replace(crlf, lf)
        before = self._main_copy()

        for heading in ("### Tier 0", "### Tier 1"):
            was = self._bullets(before, heading)
            now = self._bullets(current, heading)
            for entry in was:
                self.assertIn(entry, now, f'{heading} lost: {entry}')
            if heading == "### Tier 1":
                self.assertEqual(len(now), len(was), 'tier 1 must stay closed')

    def test_the_predicate_document_defers_to_the_governing_one(self):
        text = (ROOT / "docs" / "hotl-execution-predicate.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("human-out-of-the-loop-operation.md", text)
        self.assertIn("governing document wins", text)

    def test_the_governing_document_names_the_executable_form(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("hotl_execution.py", text)
        self.assertIn("implements the rules above and adds none", text)


class ConjunctionTests(unittest.TestCase):
    def test_every_term_met_executes(self):
        decision = decide(MERGE, _all(True))
        self.assertIs(decision.outcome, Outcome.EXECUTE)
        self.assertTrue(decision.autonomous)
        self.assertEqual(decision.blockers, ())

    def test_any_single_unmet_term_blocks(self):
        """A conjunction, not a score: one failure is enough, for each term."""

        for term in TERMS:
            evidence = _all(True)
            evidence[term] = TermEvidence(False, f"{term} unmet")
            decision = decide(MERGE, evidence)
            self.assertIs(decision.outcome, Outcome.HOLD, term)
            self.assertFalse(decision.autonomous, term)

    def test_a_hold_names_every_unmet_term(self):
        evidence = _all(True)
        evidence["bounds"] = TermEvidence(False, "daily ceiling reached")
        evidence["independence"] = TermEvidence(False, "acting identity authored it")
        decision = decide(MERGE, evidence)
        joined = " ".join(decision.blockers)
        self.assertIn("daily ceiling reached", joined)
        self.assertIn("acting identity authored it", joined)

    def test_a_blocker_is_never_phrased_as_a_permission_question(self):
        """A hold reports a specific obstacle; it does not re-ask the owner."""

        evidence = _all(True)
        evidence["verification"] = TermEvidence(False, "evidence not bound to candidate")
        for blocker in decide(MERGE, evidence).blockers:
            self.assertNotIn("?", blocker)
            for asking in ("may i", "should i", "permission", "approve", "confirm"):
                self.assertNotIn(asking, blocker.lower())

    def test_every_term_must_be_evaluated(self):
        partial = _all(True)
        del partial["gates"]
        with self.assertRaises(ValueError) as caught:
            decide(MERGE, partial)
        self.assertIn("gates", str(caught.exception))

    def test_an_unmet_term_must_state_a_reason(self):
        with self.assertRaises(ValueError):
            TermEvidence(False)


class RecoveryTests(unittest.TestCase):
    def test_a_recoverable_blocker_routes_to_recovery_not_the_owner(self):
        evidence = _all(True)
        evidence["verification"] = TermEvidence(
            False, "evidence stale", recovery="re-run exact-SHA verification"
        )
        decision = decide(MERGE, evidence)
        self.assertIs(decision.outcome, Outcome.RECOVER)
        self.assertEqual(decision.recovery, "re-run exact-SHA verification")
        self.assertFalse(decision.autonomous)

    def test_recovery_never_promotes_a_tier_zero_action(self):
        action = ProposedAction("deploy_to_production", "r", "local", "0" * 40, "grok")
        evidence = _all(True)
        evidence["gates"] = TermEvidence(False, "CI red", recovery="re-run CI")
        self.assertIs(decide(action, evidence).outcome, Outcome.HOLD)

    def test_an_unrecoverable_blocker_holds(self):
        evidence = _all(True)
        evidence["authorization"] = TermEvidence(False, "no standing grant for this class")
        self.assertIs(decide(MERGE, evidence).outcome, Outcome.HOLD)

    def test_a_sibling_recovery_cannot_carry_a_missing_authorization(self):
        """An unmet authorization needs authority no recovery supplies.

        The document routes to the owner exactly when a blocker needs authority
        outside the standing grant, and "no standing grant covers this" is that
        blocker. Recovery was offered on the first unmet term that carried one,
        so authorization unmet plus any recoverable second term returned RECOVER
        and the owner was never reached. Constructed by an independent review of
        #146.
        """

        evidence = _all(True)
        evidence["authorization"] = TermEvidence(False, "no standing grant for this class")
        evidence["verification"] = TermEvidence(
            False, "evidence stale", recovery="re-run exact-SHA verification"
        )
        decision = decide(MERGE, evidence)
        self.assertIs(decision.outcome, Outcome.HOLD)
        self.assertIsNone(decision.recovery)
        # The recoverable term is still reported, so the hold names every
        # obstacle rather than only the one that stopped it.
        self.assertEqual(len(decision.blockers), 2)

    def test_a_recovery_only_fires_when_every_unmet_term_authorises_one(self):
        """One term's remedy must not carry the others.

        Offering the first recovery found let a red `gates` advance on the
        strength of a recoverable `verification` -- re-verification that has
        nothing to do with the failing check. Constructed by a delta review of
        #146. The document routes "a temporary failure" to "an authorized
        recovery", one per failure.
        """

        evidence = _all(True)
        evidence["bounds"] = TermEvidence(False, "daily ceiling reached")
        evidence["verification"] = TermEvidence(
            False, "evidence stale", recovery="re-run exact-SHA verification"
        )
        # bounds carries no recovery, so nothing authorises progress on it.
        self.assertIs(decide(MERGE, evidence).outcome, Outcome.HOLD)

        # Give it one and the decision may recover.
        evidence["bounds"] = TermEvidence(
            False, "daily ceiling reached", recovery="wait for the ceiling to reset"
        )
        decision = decide(MERGE, evidence)
        self.assertIs(decision.outcome, Outcome.RECOVER)
        self.assertEqual(len(decision.blockers), 2)

    def test_every_owner_routed_term_holds_even_carrying_its_own_recovery(self):
        """A recovery string on these does not make them recoverable.

        Each is a trip condition or an explicit refusal in the governing
        document, and a delta review of #146 reached RECOVER on every one by
        attaching a recovery to it or to a sibling.
        """

        for term in ("authorization", "gates", "independence", "scope", "audit"):
            self.assertIn(term, NON_RECOVERABLE_TERMS, term)
            # Its own recovery, and every other term satisfied. Emptying
            # NON_RECOVERABLE_TERMS makes this RECOVER, which the previous
            # fall-through test could not detect.
            evidence = _all(True)
            evidence[term] = TermEvidence(
                False, f"{term} unmet", recovery=f"retry {term}"
            )
            decision = decide(MERGE, evidence)
            self.assertIs(decision.outcome, Outcome.HOLD, term)
            self.assertIsNone(decision.recovery, term)

    def test_the_governing_formula_names_every_term_the_code_requires(self):
        """The document is authoritative, so a short formula recreates the gap.

        It carried the six-letter form while the evidence bar carried items 6
        and 10, which is how an implementation following it returned execute
        for a prohibited path with no audit record.
        """

        text = DOC.read_text(encoding="utf-8")
        formula = [line for line in text.splitlines() if line.startswith("H(a) = ")]
        self.assertEqual(len(formula), 1, formula)
        for letter in ("A(a)", "G(a)", "B(a)", "V(a)", "I(a)", "S(a)", "P(a)", "D(a)"):
            self.assertIn(letter, formula[0], letter)
        self.assertEqual(formula[0].count(" and "), len(TERMS) - 1)

    def test_an_unrecognised_term_is_refused_rather_than_dropped(self):
        """A term this predicate does not evaluate must not pass silently.

        decide() walks TERMS, so a caller supplying an unmet seventh term had
        it ignored and still received EXECUTE. That is the mechanism by which a
        bar item leaves the predicate without anyone noticing. Constructed by an
        independent review of #146.
        """

        evidence = _all(True)
        evidence["provenance"] = TermEvidence(False, "artifact provenance unproved")
        with self.assertRaises(ValueError) as caught:
            decide(MERGE, evidence)
        self.assertIn("provenance", str(caught.exception))

    def test_every_bar_item_reaches_a_term(self):
        """A bar item with no term is unevaluated, not relaxed.

        This is the defect the refusal above only mitigates: `audit` and
        `scope` were genuinely absent, so a change touching prohibited paths
        with no audit record satisfied H(a). The document numbers its items, so
        the count is checkable directly.
        """

        section = DOC.read_text(encoding="utf-8").split("## The evidence bar", 1)[1]
        section = section.split("Any element that is missing", 1)[0]
        items = [line for line in section.splitlines() if re.match(r"^\d+\. ", line)]
        self.assertEqual(len(items), 10)

        # Items 3, 4 and 5 are all independence, and 1, 2 and 7 are all gates,
        # so the terms are fewer than the items by exactly those foldings.
        self.assertEqual(len(TERMS), 8)
        for required in ("scope", "audit"):
            self.assertIn(required, TERMS)

        # The mapping is recorded beside TERMS; every item number must appear
        # there, so adding an eleventh item to the document without mapping it
        # fails here rather than passing unevaluated.
        import inspect

        from director_orchestrator import hotl_execution

        source = inspect.getsource(hotl_execution)
        mapping = source.split("ten-item evidence bar", 1)[1].split("TERMS: tuple", 1)[0]
        cited = set()
        for run in re.findall(r"items? (\d+(?:\s*,\s*\d+)*)", mapping):
            cited.update(int(number) for number in run.replace(" ", "").split(","))
        self.assertEqual(cited, set(range(1, 11)))

    def test_a_satisfied_term_cannot_carry_a_recovery(self):
        with self.assertRaises(ValueError):
            TermEvidence(True, recovery="pointless")


class CompletionRateTests(unittest.TestCase):
    def test_blocked_work_stays_in_the_denominator(self):
        rate = CompletionRate(completed_without_intervention=3, eligible_past_deadline=4)
        self.assertEqual(rate.percent, 75.0)

    def test_completions_cannot_exceed_the_population(self):
        with self.assertRaises(ValueError):
            CompletionRate(completed_without_intervention=5, eligible_past_deadline=4)

    def test_an_empty_population_has_no_rate_rather_than_a_flattering_one(self):
        """Zero eligible must not report 100 percent."""

        self.assertIsNone(CompletionRate(0, 0).percent)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:32 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:52 UTC | END]]
