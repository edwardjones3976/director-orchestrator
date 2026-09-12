<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:25 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 02:50 UTC | BEGIN]] -->
# Director decision rules

**Status:** Governing for the 2026-09-13 delivery and for Director coordination
after it. Adopted by owner direction on 2026-09-08.

These are decision procedures, not aspirations. Each one states what it changes
about the work, and each is written so that a lane can apply it without asking
the Director. They remove no deterministic gate, relax no approval, and create
no authority. Where a rule and `AGENTS.md` or
`src/director_orchestrator/policy.py` disagree, the gate wins.

---

## R1 — Sequence checks by failure probability per unit cost

**Rule.** When several conditions must all hold, determine them in decreasing
order of `p_fail / cost_to_determine`. Check the thing most likely to fail and
cheapest to check, first.

```
order by:  p(this condition is unmet) / (time to find out)     descending
```

**Why it binds here.** The activation chain is roughly a dozen sequential gates,
each able to refuse. If they were independent at 90% each, a clean first pass
has probability 0.9^12 ≈ 0.28. Expect a refusal; the only real question is how
early you learn about it.

**Distinguish two costs.** The cost of *determining* whether a gate is satisfied
is usually far below the cost of *satisfying* it. Gate 8 is the clearest case:
the Long Table canary bundle either exists or it does not, and looking takes
minutes, while producing it takes days.

**What this changes.** Gate 8 is determined on Wednesday, not at the Saturday
go/no-go. Every gate whose determination is cheap is determined before any gate
whose satisfaction is expensive. The Saturday go/no-go becomes a confirmation of
a mostly-known answer rather than the moment of discovery.

**Who applies it.** Director, when ordering any checklist; any lane, when
choosing which of several unknowns to resolve first.

## R2 — The gate ledger is three-valued and computed, never asserted

**Rule.** Every gate carries a value in `{true, false, unknown}`. Completion is
computed by Kleene conjunction: false if any gate is false; unknown if any gate
is unknown and none is false; true only when every gate is true.

```
Done      = AND over gates                    ( {T,F,U} )
Permitted = Done  AND  NOT (OR over no-go conditions)
```

Both must be evaluated. A satisfied conjunction never overrides a triggered
no-go.

**Why it binds here.** The failure this project is exposed to is not a gate
reading false — it is a gate reading unknown and being counted as true. This is
the formal statement of the rule already in `AGENTS.md`: absence of evidence is
never consent, and a provider that is out of quota, unauthenticated, silent or
absent has not approved anything.

**What this changes.** The ledger in the activation plan stores `T`, `F` or `U`
per gate with the evidence reference beside it, and the completion line is
derived from those values. No document, status message or report states that the
project is done; each states the computed value and the gates still unknown.

**Who applies it.** Director, in every status and in the close-out report. Any
lane reporting a gate reports one of the three values and never omits `U`.

## R3 — An optional action must name the decision it could flip

**Rule.** Before any optional investigation, status check, review pass, or
provider call, name at least one decision whose outcome could change as a
result. If no possible result changes any action, the information is worth
nothing regardless of how uncertain the question feels, and the action is not
taken.

```
value of information = 0   whenever every outcome leads to the same next action
paid probe worthwhile iff  p(decisive) × (time or cost saved) > cost of probe
```

**Why it binds here.** A repeat of an unchanged attempt has `p(decisive) ≈ 0` by
construction, which is why blind retries are already forbidden. The rule
generalises that prohibition from retries to all optional work.

**What this changes.** Every work order that proposes an investigation states the
decision it serves. The Director does not re-read settled files, and does not
re-run a check whose result cannot move a gate. Required security, CI, approval
and exact-candidate checks are mandatory and out of scope for this rule —
optionality is what the rule tests, not importance.

**Who applies it.** Every lane, before spending a paid attempt or an
investigation pass.

## R4 — The schedule is a temporal network, not a critical path

**Rule.** Model the schedule as constraints of both signs and check consistency,
rather than summing a longest path.

```
minimum lag:   t_j − t_i ≥ d_ij      precedence
maximum lag:   t_j − t_i ≤ D_ij      expiry, deadline, freshness window
release:       t_i ≥ r_i             an owner window opens
capacity:      attended tasks do not overlap        (operator capacity 1)

feasible  iff  the constraint graph contains no negative cycle
```

**Why it binds here.** Two constraints in this delivery are maximum lags, which
critical-path analysis cannot express. `LIVE_OK` evidence is valid for at most
86,400 seconds with no renewal path, so
`t(Activate) − t(finish ValidateProviders) ≤ 24 h`. And the owner windows are
release times on a resource of capacity one.

**A consequence that ordinary scheduling intuition gets backwards.** Because the
freshness window cannot be renewed and the evidence campaign spends budget,
`ValidateProviders` should start **as late as the buffer allows**, not as early
as possible. Earliness is a cost here.

**What this changes.** WO-14 runs the consistency check before Thursday's
window. If it reports a negative cycle, the plan is infeasible as written and
the specific constraint pair is named, which is a far more useful failure than
discovering the conflict on Saturday.

**Who applies it.** Director, before committing any window.

## R5 — Operator-minutes is the binding resource

**Rule.** Every task that requires the owner present carries a duration
estimate. The feasibility test is:

```
Σ attended durations  ≤  Σ available owner-window minutes
```

with capacity one. No amount of delegation, parallelism or additional providers
changes either side of that inequality.

**Why it binds here.** Every control action requires the operator for its whole
pass, and the writer lease is single-writer by design. The stated windows total
roughly fourteen and a half hours. That number, not compute, decides whether the
plan fits.

**What this changes.** WO-14 sums the attended estimates against the windows. If
the sum exceeds the budget, scope moves — the response is never "add an agent."

**Corollary on parallelism.** Split the project in two before reasoning about
concurrency. The engineering lane has a high parallel fraction; the activation
lane has essentially none, and concurrent writers there are an explicit no-go.
For the engineering lane, useful concurrency is bounded by the number of
disjoint file partitions with independent tests — a number that can be counted
off a diff rather than estimated:

```
Speedup(N) ≈ 1 / [ (1−P) + P/N + c·N(N−1)/2 ]      interior maximum near N* ≈ sqrt(2P/c)
```

**Who applies it.** Director, when sizing a window; owner, when accepting one.

## R6 — Evidence carries a binding key and is reused on match

**Rule.** Every piece of verification carries an explicit key, and is reusable
exactly when the key still matches.

```
K(e) = ( artifact identity, inputs, environment, issuer, freshness )

work remaining = Σ over required checks r of  cost(r) · [ no valid evidence satisfies K(r) ]
```

**Key granularity is the whole lever.** Coarse keys invalidate everything on
every change; fine keys survive. The repository already computes tree digests,
so a per-subtree key is available at no cost.

**The line that never moves.** The exact-candidate review verdict, CI, and
security results stay bound to the candidate commit. That is a security
property, not an inefficiency, and a changed head always needs its own verdicts.
Historical approvals never transfer. The reuse applies to *supporting analysis*
— a blocker audit keyed to specific files, a topology answer, a control-script
behaviour question — which today would be discarded on a rebase that does not
touch those files.

**What this changes.** WO-02, WO-03 and WO-05 record each finding with the file
paths and digests it depends on, so a candidate change re-runs only the findings
whose inputs moved.

**Who applies it.** Every lane producing analysis; the Director when
reconciling evidence.

## R7 — Allocation quotas are integers

**Rule.** Over a rolling window `W`, convert target shares to integer quotas by
largest-remainder apportionment so the quotas sum to `W` exactly, and express
the bands and ceilings as integer bounds.

```
q_i = round to integers by largest remainder,  Σ q_i = W
floor((s_i − 0.03)·W)  ≤  n_i  ≤  floor((s_i + 0.05)·W)
```

At `W = 20`, a 40% target is 8 assignments with a ceiling of 9. A zero target
refuses a lane **in that profile**, including for accepted non-code research;
the same lane is allocation-admissible under a profile with a non-zero target,
subject to remaining capacity and other admission gates. In particular,
`engineering` refuses Perplexity and Firecrawl research (both targets 0%), while
`public_research` allows the same requests through allocation admission with
targets of 25% and 15% respectively, when the other conditions pass.

[`is_accepted_productive_kind`, workload_policy.py:512–529](../src/director_orchestrator/workload_policy.py#L512-L529)
classifies these lanes' research as productive. The [`evaluate_assignment`
admission call site, workload_policy.py:993–1002](../src/director_orchestrator/workload_policy.py#L993-L1002),
applies that classification before the capacity check; the [zero-target check,
lines 617–618](../src/director_orchestrator/workload_policy.py#L617-L618), returns
`allocation_profile_lane_ineligible`. [allocation_profiles.py:17–18](../src/director_orchestrator/allocation_profiles.py#L17-L18)
defines the two target sets. Admission and concurrency rules are unchanged;
this wording grants no dispatch authority and activates no profile.

---

## What these rules do not do

They do not reduce the thirty provider-activation evidence rows, the nine
activation gates, or the attended minutes those require. Those are fixed by the
system's design and by its safety properties. What the rules change is the
*order* in which unknowns are resolved, the amount of work that is repeated
unnecessarily, and whether an unknown can be mistaken for a pass.

No percentage reduction is claimed. The largest risk to this delivery is not
scheduling but whatever the blocker audit surfaces that nobody has looked at
yet, and no decision rule surfaces that.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 02:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:25 UTC | END]] -->
