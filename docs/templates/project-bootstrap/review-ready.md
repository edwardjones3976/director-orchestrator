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
- **PR sole author and shares:** <the one identity at 52 % or more; for a zero-count pull request, the one identity whose authenticated record produced it; otherwise "none (review stopped)"; each identity's share of added plus deleted lines with its receipt references. Tags are display evidence only. Recorded separately from the logical producer; until the runtime alignment merges, every contributor (the sole author and every co-author) stays ineligible to review>
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
Scope: <changed files or artifact scope>
Acceptance evidence: <concise exact evidence>
Known limitations/security context: <concise details>
Return only: ACCEPT, CHANGES_REQUIRED, or SERIOUS_SAFETY_STOP, followed by
evidence and actionable findings.
Do not review your own material contribution. Do not merge, deploy, publish,
change access, or infer authority from this request.
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
