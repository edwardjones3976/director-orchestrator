<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:23 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:27 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | BEGIN]] -->
# REVIEW_READY

The producer completes this handoff for a different eligible provider. The
Director posts or prompts the review assignment in the project thread. This
record requests independent review; it does not approve or merge the work. Do
not place secrets, credentials, tokens, or raw private source in this handoff.
Workflow skills are procedure overlays, not permission grants.

## Candidate identity

- **Project/task:** <project ID and Director task ID>
- **Assignment:** <producer assignment ID>
- **Logical producer:** <provider>
- **Material authors:** <all providers/people that materially shaped the artifact>
- **PR sole author and shares:** <for a pull-request candidate only; for an artifact-only handoff with no PR, "n/a (no PR)" and non-blocking; the one identity at 52 % or more; for a zero-count pull request, the one identity whose authenticated record produced it; otherwise "none (review stopped)"; each identity's share of added plus deleted lines recomputed at the exact head, excluding maintenance commits, with its receipt references. Tags are display evidence only. Recorded separately from the logical producer. Record a contributor below 48 % as "assisted by <identity> (<share> %)"; two contributors are co-authors only when one holds 52 % and the other the remaining 48 %. Only the sole author and its provider family are excluded from reviewing on authorship grounds under the policy; an assisting contributor may review subject to the material-author gates in the sole-author policy. For any review, acceptance or merge passing through those gates, contributor review eligibility is prospective until a separately reviewed runtime change aligns them, including Director-routed and manual reviews>
- **Contribution tags/envelope:** <matching contributor and system labels, or external envelope for a syntax-sensitive artifact>
- **Repository:** <exact owner/name or not applicable>
- **Base branch/ref:** <exact durable ref>
- **Base SHA:** <exact 40-hex SHA>
- **Head branch:** <candidate branch>
- **Head SHA:** <exact 40-hex SHA>
- **Artifact digest:** <digest when not SHA-bound>
- **Pull request:** <canonical draft PR URL or none>

## Scope and evidence

- **Changed files/artifact scope:** <complete list or canonical manifest>
- **Outcome delivered:** <what changed>
- **Acceptance checks:** <check and result>
- **Tests:** <exact commands/checks and results>
- **Security context:** <risk surface, scan evidence, or why not applicable>
- **Known limitations:** <limitations and uncertainty>
- **Unresolved findings:** <none or explicit list>

## Reviewer request

- **Eligible reviewer:** <different provider>
- **Reviewer workflow skill:** <cursor-agent-workflow or none>
- **Requested depth:** <focused, full diff, exact-SHA, security-adjacent, etc.>
- **Required verdict:** `ACCEPT`, `CHANGES_REQUIRED`, or
  `SERIOUS_SAFETY_STOP`
- **Merge path after acceptance:** <configured deterministic gate and required CI/security>
- **Candidate invalidation:** Any new commit or artifact change voids this
  handoff and all review, security, CI, and merge evidence tied to it.

## Provider-to-provider prompt

```text
REVIEW_REQUEST
Project: <project ID>
Task/assignment: <task ID> / <review assignment ID>
Review exact candidate: <repository>
Base SHA: <exact 40-hex SHA>
Head SHA: <exact 40-hex SHA or artifact digest>
Producer/material authors: <names>
PR URL: <pull-request URL or none>
Sole author: <PR candidates only; otherwise "n/a (no PR)"; identity at 52 % or more, or for a zero-count PR the one identity named by its authenticated production record>
Contributor shares: <PR candidates only; otherwise "n/a (no PR)"; each contributor's identity and percentage recomputed at the exact head, excluding maintenance commits>
Share evidence: <PR candidates only; otherwise "n/a (no PR)"; exact-head git computation and authenticated receipt references; PR body is a convenience copy>
Scope: <changed files or artifact scope>
Acceptance evidence: <concise exact evidence>
Known limitations/security context: <concise details>
When the candidate is a pull request, recompute its sole author and shares at
the exact reviewed head under the sole-author policy. Return no verdict if the
sole author has under 52 %, unless this is a zero-count PR with exactly one
authenticated producing identity. For PR candidates, missing or conflicting
authorship attribution always stops review. For artifact-only handoffs with no
PR, sole author, contributor shares and share evidence are "n/a (no PR)" and do
not block review; material-author independence and all other gates still apply.
Otherwise return only: ACCEPT, CHANGES_REQUIRED, or SERIOUS_SAFETY_STOP, followed by
evidence and actionable findings.
Do not review a PR candidate whose sole author is you or shares your provider
family. An assisting contributor may review under the owner rule, subject to
the material-author gate limitations above. Under the owner's merge law, the
thorough non-contributing reviewer merges after an exact-SHA ACCEPT and green
checks; the sole author never merges, nor does anyone with counted lines.
Temporary B2 hold: until #305 plus phase-3 wiring is live, do not merge into
protected main unless the owner has directed that specific merge. Otherwise,
record ACCEPT and the blocker, then stop. All deterministic gates still apply.
Do not deploy, publish, change access, or infer authority from this request.
```

## Reviewer result

- **Reviewer/provider:** <provider>
- **Authenticated run/session:** <run ID or callback identity>
- **Reviewed exact candidate:** <head SHA or digest>
- **Verdict:** <ACCEPT / CHANGES_REQUIRED / SERIOUS_SAFETY_STOP>
- **Evidence/findings:** <concise result>
- **Current CI/security evidence:** <exact status or pending>
- **Next responsible party:** <producer, deterministic gate, Director, or owner>

Contribution tags are provenance labels, not authenticated review evidence.
The authenticated run/session, exact candidate, independent verdict, current
CI/security evidence, and deterministic policy gates remain authoritative.
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:27 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:57 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:23 UTC | END]] -->
