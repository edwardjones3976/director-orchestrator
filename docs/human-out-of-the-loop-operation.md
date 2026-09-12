<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 17:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 03:44 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | BEGIN]] -->
# Human-out-of-the-loop operation

**Status:** Documented authority only. Default off. No delegated decision is
executable until the implementation phases below are separately built, gated,
and accepted by the owner. The presence of this document grants nothing.

**Schema authority:** `director-delegated-authority/v1`

**Frozen starting point:** `70217f258430843b936df7c13030aff957cfb782`

## Purpose

The Director stops in `OWNER_DECISION_REQUIRED` whenever an action touches
merge, deployment, publication, deletion, purchases, external representation,
sensitive data, credentials, or expanded access. That is correct as a default
and stays the default. It also means that when the owner is away, work halts
even where every deterministic gate has already passed and independent review
is available.

Human-out-of-the-loop operation is the bounded, revocable delegation that lets
an enumerated class of those decisions be resolved without the owner present.

It is defined by one rule, and everything else in this document follows from it:

> Delegation changes **who supplies a verdict**.
> It never changes **whether a gate runs**.

No deterministic gate is removed, relaxed, skipped, or made advisory by any
grant described here. A delegated decision must clear strictly more evidence
than an attended one, because the owner's judgment is no longer present to
catch what the gates do not model.

## Operating states

Exactly one state is in force per deployment profile at any time.

| State | Meaning |
| --- | --- |
| `ATTENDED` | Default. Every `OWNER_DECISION_REQUIRED` waits for the owner. No delegation exists. |
| `DELEGATED` | A live grant may resolve an enumerated tier-1 decision class when the full evidence bar holds. Everything else still waits. |
| `SUSPENDED` | Entered automatically on any trip condition. Behaves exactly as `ATTENDED` until the owner clears it explicitly. |

A deployment starts in `ATTENDED` and returns to it whenever configuration is
absent, invalid, expired, or unreadable. There is no path from `SUSPENDED` to
`DELEGATED` that does not pass through an explicit owner action.

## Decision classes

### Tier 1 - delegable, only under a live grant that names the class

- Executing an eligible merge of a pull request into a non-protected branch.
- Marking a pull request ready for review.
- Posting to the project's own designated Slack channel.

Tier 1 is exhaustive and every entry names a specific act. There is no
residual or catch-all delegable class, and none may be added by describing a
decision as routine: a category whose membership is a judgement call is not a
closed enumeration, and it would silently reopen the fail-closed rule below.

### Tier 0 - never delegable, under any configuration

- Production deployment, activation, or runtime cutover.
- Publication, external representation, or any message to a party outside the
  project's own designated channel.
- Deletion of data, force-push, or history rewrite.
- Purchases, billing changes, or raising a budget ceiling.
- Credential, secret, token, or access-scope changes.
- Merging into a protected branch.
- Editing this document, a grant, an allowlist, or any deterministic gate.
- Anything AGENTS.md rule 10 names that is not explicitly listed in tier 1.

Tier 0 is enumerated, and the enumeration fails closed: a decision that is not
plainly a listed tier-1 class is tier 0. A novel decision class is tier 0 until
the owner adds it deliberately, which is itself a tier-0 act.

## The evidence bar

A tier-1 decision may be resolved without the owner only when **every** item
below holds for the exact candidate commit. These are conjunctive. There is no
weighting, no majority, and no discretion.

1. Every required CI check is green at the exact candidate head.
2. Independent security evidence exists at that same head.
3. An independent exact-SHA review returns `ACCEPT` from a provider that is
   neither the producer nor a material author of the candidate.
4. That `ACCEPT` is the whole verdict requirement. Owner ruling 2026-09-12,
   verbatim: "it's a one-non-author-reviewer project-wide". A second verdict
   is never required and never substitutes for any other item.
5. Producer, reviewer, and the agent executing the decision are three distinct
   identities.
6. The change touches only paths inside the assignment's `owned_paths` and none
   of its `prohibited_paths`.
7. No tier-0 class is implicated, directly or as a consequence.
8. Budget, attempt, and hop ledgers are all within their configured bounds.
9. A grant is live, unexpired, unrevoked, and scoped to this repository, this
   branch, this decision class, and this assignment.
10. The audit record for the decision has been durably written before the
    decision is executed.

Any element that is missing, stale, ambiguous, unavailable, or unreadable sends
the decision to `OWNER_DECISION_REQUIRED`. **Absence of evidence is never
consent.** A provider that is out of quota, unauthenticated, or silent has not
approved anything, and its silence may not be counted, inferred, or waited out.

## Why one independent verdict

A single agent verdict is a single point of failure, and this repository's own
record shows it is a fragile one. Every delegation attempt recorded to date has
failed to produce a verdict at all: providers have returned ambiguous results,
failed authentication, hit usage limits, or been absent from the environment.
The owner has ruled that one non-author `ACCEPT` is the bar, project-wide;
what this section protects is that the one verdict is genuinely independent.

The reviewer must be distinct from the producer and every material author.
A reviewer and a producer that share a backing
model are not distinct for this purpose, and neither are two runs of the same
provider. Independence is a property of the failure mode, not of the label.

## Grants

A grant is the exact form the charter's "owner may explicitly authorize a
designated agent" takes. It is data, written by the owner, and it is bounded on
every axis:

- exact repository, and the branch pattern it applies to;
- the enumerated tier-1 decision classes it covers, and no others;
- the named providers eligible to supply verdicts;
- a wall-clock expiry;
- a maximum number of exercises before it must be renewed;
- a revocation switch that takes effect before the next decision, not after.

A grant that is expired, exhausted, revoked, malformed, or silent on the class
being decided does not apply. Grants do not compose, inherit, or extend by
analogy: authority for one repository, class, or assignment is never authority
for another.

## Trip conditions

Any of these moves the deployment to `SUSPENDED` immediately, before the
decision in flight completes:

- a gate result changes after a verdict was recorded;
- a new commit appears on the candidate, which invalidates every prior review,
  security, CI, and merge evidence item;
- any reviewer returns `SERIOUS_SAFETY_STOP`;
- the owner reverses two consecutive delegated decisions;
- a provider authentication or quota failure occurs mid-decision;
- a tier-0 class is implicated at any point;
- the audit ledger cannot be written.

The last one is not a formality. A decision that cannot be recorded cannot be
reviewed afterward, and an unreviewable autonomous action is exactly what this
framework exists to prevent.

## Audit record

Every delegated decision, and every refusal to make one, writes an immutable
record containing the grant identifier, the decision class, the exact candidate
commit, each verdict with its provider and run identifier, every gate result
consulted, the acting agent identity, and timestamps. The record is written
before the action, not after it.

## Relationship to the existing rules

- **AGENTS.md rule 3 is unchanged.** No agent merges its own pull request.
  Delegation never makes a producer its own merger; it supplies a different
  agent, and evidence-bar item 5 enforces the separation.
- **AGENTS.md rule 10 is unchanged as the default.** It still stops for every
  tier-0 class, and for every tier-1 class where no live grant applies.
- **The deterministic gates in `policy.py` are unchanged and remain
  authoritative.** Model output may add approval requirements and may never
  remove them. That includes output from a delegated reviewer.
- **The charter's review-to-merge handoff is unchanged.** Delegation supplies
  the reviewer identity and a distinct executing identity; it does not alter what
  a review must contain or when evidence is invalidated.
- **The charter's Director and provider authorship boundary supplies the
  identity terms used here.** "Producer" and "material author" mean whoever
  actually produced or materially authored the candidate. Delegation, routing,
  coordination, QA, review orchestration, relay, merge eligibility evaluation,
  and merge execution create no authorship, so performing them neither makes an
  identity the producer for evidence-bar items 3, 4, and 5 nor makes an actual
  author eligible under them.

## How the existing gates are satisfied, not substituted

Two objections have to be answered explicitly, because on a fast reading the
existing rules look like they forbid this framework outright. They do not, and
the reasons matter more than the conclusion.

### The independent exact-SHA review gate

The charter says owner authorization "never substitutes for applicable
deterministic approval, exact-SHA review, security, CI, budget, allowlist, or
rollback gates." That sentence stays exactly as written, and this framework does
not weaken it. It governs what an authorization may *skip*, and the answer is
nothing.

It is not in tension with a delegated decision, because the exact-SHA review
gate has never consumed an owner verdict. The charter's own review-to-merge
handoff assigns review to "a rotating different-provider review assignment,
choosing the least-recent eligible non-author," and that reviewer returns
`ACCEPT`, `CHANGES_REQUIRED`, or `SERIOUS_SAFETY_STOP`. A named provider's
exact-SHA `ACCEPT` is therefore the gate being **satisfied in its ordinary
form**, not an authorization substituting for it.

What delegation changes is narrower than it first appears. Previously the gate
could be satisfied while the *merge decision that follows it* still waited for
the owner. Under a live grant, that following decision may proceed, and only
then, and only when every other item on the evidence bar also holds. The
framework requires the same one non-author provider verdict the charter
requires, so a delegated decision clears the same review bar as an attended
one, never a lower one.

### Rule 3's no-self-merge bar

AGENTS.md rule 3 forbids an agent from merging its own pull request. That bar
never lifts, and this framework does not create an exception to it.

"Your own" is a property of identity, not of species. Two agents are not one
agent. Evidence-bar item 5 requires the producer, the reviewer, and the agent
executing the decision to be three distinct identities, so a delegated merge is
by construction never a self-merge. An agent that produced or materially
authored a candidate is permanently ineligible to review or merge it, and no
grant can make it eligible: delegation names who *may* act, and never widens
*what* an ineligible actor may do.

This has a consequence the owner should expect rather than be surprised by.
When only one agent is available, a delegated merge cannot proceed at all,
because three distinct identities cannot be found. That is the framework
working, not failing. It is also the present condition of this repository.

## Implementation phases

Each phase is separately built, reviewed, and accepted. No phase begins before
its predecessor is accepted, and acceptance of one grants nothing to the next.

| Phase | Content | Executes a decision? |
| --- | --- | --- |
| 0 | This document. Authority definition only. | No |
| 1 | Grant schema, settings, validation, `/health` readiness booleans, all default off. | No |
| 2 | Audit ledger and record format. | No |
| 3 | Shadow mode: record `would_delegate` and `would_stop` over a bounded set of decisions, changing nothing. | No |
| 4 | Enable tier-1 merge only, one repository, owner observing. | Yes |
| 5 | Broaden tier-1 classes only after a clean shadow record and a clean live record. | Yes |

## Known limitations

These are stated plainly because a framework that hides them is worse than none.

- An agent verdict is not equivalent to human judgment on novel risk. The
  evidence bar is good at catching what it models and blind to what it does not.
- Providers that share a base model can fail in correlated ways. The non-author
  rule reduces independent error, not common-mode error.
- Reviews read untrusted material. A diff, an issue, or a CI log can carry
  instructions aimed at the reviewer, and a delegated reviewer has no owner
  looking over its shoulder.
- Delegation has not yet produced a verdict in this project. Every attempt so
  far ended in quota, authentication, or availability failure, which is why
  phases 3 and 4 exist before any broadening.
- This document was drafted by an agent. An agent writing the rules that govern
  agents is a conflict of interest, and the owner should read it as a proposal
  to be checked rather than as settled policy.

<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 03:44 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 17:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | END]] -->
