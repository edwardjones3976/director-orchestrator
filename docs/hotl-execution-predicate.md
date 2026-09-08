<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | BEGIN]] -->
# Standing-authorization execution predicate

This describes the executable form of the delegation rules. It adds nothing to
them. [`human-out-of-the-loop-operation.md`](human-out-of-the-loop-operation.md)
remains the governing document, is unchanged by this change, and a test pins
that. Where the two could ever disagree, the governing document wins and this
one is wrong.

## The predicate

A proposed action runs without returning to the owner only when every term
holds:

```
H(a) = A(a) and G(a) and B(a) and V(a) and I(a) and S(a)
```

| Term | Required condition |
| --- | --- |
| `A` authorization | A live standing grant covers this action class, repository, environment and scope. |
| `G` gates | Every applicable deterministic policy, security and CI gate passes. |
| `B` bounds | The action fits the remaining spend, time, call and retry limits. |
| `V` verification | Evidence is current and bound to the exact candidate; any earlier ambiguous attempt is reconciled. |
| `I` independence | The acting identity did not produce or materially author the work. |
| `S` safe stopping | Failure handling, cancellation and any required recovery are defined and authorized. |

It is a conjunction, not a score. One unmet term is decisive, and
`test_any_single_unmet_term_blocks` exercises that for each of the six.

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
              recover        if an unmet term authorizes a recovery
              hold(blocker)  otherwise
```

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
