<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 18:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | BEGIN]] -->
# Standing-authorization execution predicate

This describes the executable form of the delegation rules. It adds nothing to
them. [`human-out-of-the-loop-operation.md`](human-out-of-the-loop-operation.md)
remains authoritative. Where the two could ever disagree, the
governing document wins and this one is wrong.

That document **is** edited by this change, by 65 added lines and 3 removed. An
earlier draft of this page claimed it was unchanged, which was false; an
independent review of #146 caught the contradiction against the diff. What a
test pins is narrower and is the property that matters: no gate is removed from
it. The owner may extend the document, and the additions here are the evaluated
form of the predicate and the note that provider activation evidence keeps
expiring after `LIVE_OK` — both requirements, neither a relaxation.

## The predicate

A proposed action runs without returning to the owner only when every term
holds:

```
H(a) = A(a) and G(a) and B(a) and V(a) and I(a) and S(a) and P(a) and D(a)
```

The right-hand column names the numbered item of the governing document's
evidence bar that each term carries. That mapping is the load-bearing part: a
bar item reaching no term is not a relaxed requirement, it is an unevaluated
one, and the predicate returns `execute` without ever reading it.

| Term | Required condition | Bar |
| --- | --- | --- |
| `A` authorization | A grant is live, unexpired, unrevoked, and scoped to this repository, branch, decision class and assignment. | 9 |
| `G` gates | Every required CI check is green at the exact candidate head, independent security evidence exists at that same head, and no tier-0 class is implicated directly or as a consequence. | 1, 2, 7 |
| `B` bounds | Budget, attempt and hop ledgers are all within their configured bounds. | 8 |
| `V` verification | Evidence is current and bound to the exact candidate; any earlier ambiguous attempt is reconciled. | — |
| `I` independence | Two concurring exact-SHA `ACCEPT` verdicts from **different** providers, neither the producer nor a material author; and producer, reviewer and executing agent are three distinct identities. | 3, 4, 5 |
| `S` safe stopping | Failure handling, cancellation and any required recovery are defined and authorized. | — |
| `P` path scope | The change touches only paths inside the assignment's `owned_paths` and none of its `prohibited_paths`. | 6 |
| `D` durable audit | The audit record for the decision is durably written **before** the decision executes. | 10 |

`P` and `D` are recent. The predicate was first implemented as the six-letter
form from the owner's approved analysis, taken literally, and that form is short
by two: items 6 and 10 mapped to nothing, so a change reaching prohibited paths
with no audit record satisfied `H(a)`. An independent review of #146 found it.
Adding the two terms adds approval requirements, which AGENTS.md rule 4 permits;
removing one is what it forbids.

`I` was also stated here as "the acting identity did not produce or materially
author the work", which is weaker than items 3 to 5 together — it admits a
single verdict, and it says nothing about the reviewer being a third identity.
The row above now states the bar.

It is a conjunction, not a score. One unmet term is decisive, and
`test_any_single_unmet_term_blocks` exercises that for each of the eight. A term
this predicate does not recognise is refused rather than ignored, because
silently dropping one is exactly how items 6 and 10 went missing.

## The tier check runs first

`classify()` is evaluated **before** any term, and fails closed. Tier 1 is the
closed three-class list in the governing document; anything not literally in it
is tier 0 and can never run autonomously, however many terms pass. A grant that
names an unlisted class does not create one.

This ordering is the load-bearing part. Evaluating the conjunction first and the
tier afterwards would let six satisfied terms carry a production deployment,
which is why `test_a_tier_zero_action_never_executes_even_with_every_term_met`
asserts the negative directly.

## A hold is not a permission question

```
Decision(a) = execute        if H(a)
              recover        if EVERY unmet term authorizes its own recovery,
                             and none of them is owner-routed
              hold(blocker)  otherwise
```

That quantifier is the whole rule. "If **an** unmet term authorizes a recovery"
is existential, and it lets one term's remedy carry every other unmet term:
a red `gates` paired with a recoverable `verification` recovers, advancing a
decision whose CI is failing on the strength of a re-verification that has
nothing to do with it. The code was corrected first and this page was left
behind, still specifying the rejected form — the same mistake, in the same
shape, as the governing document keeping the six-term `H(a)`. This page is the
only place `Decision(a)` is written down, so leaving it would have restored the
bug in the next implementation to read it.

Owner-routed terms are `NON_RECOVERABLE_TERMS`: `authorization`, `gates`,
`independence`, `scope` and `audit`. A recovery string attached to any of them
changes nothing, because each is a trip condition or an explicit refusal in the
governing document.

A hold names the blocking term and what is missing. It does not re-ask the
owner. Missing or stale evidence routes to verification; a temporary failure
routes to its authorized recovery; only a blocker needing authority outside the
standing grant reaches a person. `test_a_blocker_is_never_phrased_as_a_
permission_question` rejects blocker text containing a question mark or asking
language, because a system that renames an interruption has not removed one.

Only the first available recovery is offered. Recoveries re-enter this function
from the top, so chaining them internally would skip a fresh reading of every
term.

## Measuring it

```
autonomous completion rate =
    eligible workflows completed correctly within bounds, without new human
    intervention
    ------------------------------------------------------------------
    all eligible workflows whose deadline has passed
```

Eligibility and deadline are fixed before execution, so blocked or failed work
cannot leave the denominator by later reclassification. An empty population
reports no rate rather than 100 percent, which
`test_an_empty_population_has_no_rate_rather_than_a_flattering_one` pins.

## The provider-evidence expiry window, measured

There is no expiry-window constant. `expires_at` is supplied per record, and the
only shape rules are that it is UTC, later than `observed_at`, and in the future
when recorded.

The operational constraint is stronger than a deadline on activation.
`_surface_readiness` scans **every** record in the five-stage chain and reports
the surface not ready if **any** one has expired:

```python
for record in records:
    if record.expires_at <= now:
        return ActivationSurfaceReadiness(surface, None, False, False,
                                          f"{record.stage.value} evidence expired", ...)
```

Two consequences follow, and both differ from the intuitive reading:

- `LIVE_OK` does not settle the question. The earliest record, `INSTALLED`,
  keeps the surface live only while it is unexpired. When it lapses,
  `live_ready` goes false and live dispatch stops, however recent `LIVE_OK` is.
  The condition is continuing, not a race to activation.
- Letting the prior stage lapse mid-chain does not merely cost a retry:
  advancing then raises *the prior activation stage expired; use a new binding
  version*, discarding the whole chain for that binding.

So advances belong immediately before activation, and the expiry chosen at
advance time must cover the intended operating life of the lane, not just the
gap to `Activate`.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:51 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 02:32 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 18:40 UTC | END]] -->
