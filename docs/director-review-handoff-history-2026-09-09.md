<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:25 UTC | BEGIN]] -->
> **Historical record restored on 2026-09-12.** The observations and corrections below
> describe the author's 2026-09-09 session. They are not a current health check,
> route qualification, allocation decision, authorization, or activation receipt.
> Retained Microsoft 365 references describe the retired historical deployment;
> PR #161 retired that integration. Current authority is the
> [team operating charter](team-operating-charter.md),
> [provider allocation profiles](provider-allocation-profiles.md), and
> [LangGraph primary operations](langgraph-primary-operations.md).
> Verify current state through the [Witness Ledger](state-witness.md) rather
> than treating a historical readiness claim as evidence of today's deployment.

# Historical review-handoff correction, 2026-09-09

Preserved from PR #167, commit `4324744701c867791f7d9ea7bf0523273c8aaa5d`,
Claude Cowork / Claude Opus 5 material. The excerpt is retained as history.
Its owner-only push/merge language and two-artifact request are superseded by
owner-authorized execution and the current one-author/one-non-author-reviewer
protocol (merged PRs #178 and #179). A Slack identity or relay post does not
prove that an autonomous provider received or completed an assignment. Use the
current contribution checker and real CI result, not the historical diagnosis
below, when assessing attribution. The ordinary review-candidate and activation
acceptance artifacts remain distinct.

## 8. Review handoff — the Director's job, corrected

**Correction, 2026-09-09.** Earlier revisions told the owner to "request the two
ACCEPT artifacts." That was wrong: `docs/slack-collaboration-bus.md` and
`review_handoff.py` assign review orchestration to the **Director**. The owner
merges; the Director seeks the reviewer.

### The ordinary flow

```
producer authors  →  Director builds director_review_candidate/v1
                  →  Director binds a reviewer assignment and relays it on the bus
                  →  independent provider returns exactly one verdict
                        ACCEPT              → merge eligibility evaluated → owner merges
                        CHANGES_REQUIRED    → returns to author with correction notes → revise
                        SERIOUS_SAFETY_STOP → stops
```

Two artifacts are **not** interchangeable: `director_review_candidate/v1` is the
ordinary review handoff; `director-activation-review/v1` is the control script's
activation acceptance. Conflating them was a separate Director error.

`ReviewCandidateArtifact` requires the logical producer to appear in
`material_authors`, a `pull_number ≥ 1`, exact 40-hex base and head SHAs, changed
files, tests, limitations, acceptance checks, security context, and a digest over
the canonical contents. **A pull request must therefore exist before a valid
handoff can be built.** The freshness rule is absolute: review only that exact
base/head pair and digest; any new commit invalidates the review and every
downstream security, CI and merge evidence item.

Bus constraints: at most three delivery attempts with a stable client message ID;
no raw provider output, credentials, secret-shaped values or raw private source
in the relay; ordinary review-to-merge closes in at most five major orchestration
actions; revision loops are capped.

### The route, which does exist

`#project-director` carries four provider identities — Claude, Cursor, ChatGPT
and Codex — plus Linear and GitHub. An earlier Director session ran this exact
loop there on 2026-09-08. An earlier revision of this matrix said the Director
held no way to reach a fellow provider; that was wrong, and the authorized
`slack-collaboration-relay` route is the way.

### A systemic CI defect that will hit any branch behind `main`

Recorded on the bus 2026-09-08 and confirmed as applying to this delivery's own
candidate. The contribution-attribution job diffs `pull_request.base.sha`..`head`,
so once `main` moves ahead the range includes files the branch never touched,
which carry no fresh tag pair from this author. **Rebase onto current `main`
before opening the pull request.** Two traps: `gh run rerun` replays the stored
webhook payload, so re-running never sees a corrected body; and a body edit does
not re-trigger CI, because `on: pull_request` carries no `types:` filter. The PR
body needs **bold** contribution tags as its first and last visible lines — plain
`[[...]]` is not recognized and reports the misleading "envelope lacks one
matching BEGIN/END pair."

### Handoff issued for this delivery

`DIR-2026-09-09-DOCS-01`, posted to `#project-director` 2026-09-09. Producer
Claude, sole material author, therefore ineligible to review or accept. Reviewer
Cursor; ChatGPT/Codex alternate; Claude explicitly unassigned. Blocked on the
pull request existing, which needs an owner push.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:25 UTC | END]] -->
