<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | BEGIN]] -->
# Project bootstrap template kit

These templates turn the
[modular project bootstrap protocol](../../project-bootstrap-protocol.md) into
copyable project-local artifacts. Use the smallest useful subset. A short
investigation may need only the intake; a repository project may use the full
kit.

| Template | Purpose |
| --- | --- |
| [`intake.md`](intake.md) | Paste into the new project's Slack thread or conversation to shape the project from a source thread. |
| [`project-profile.yaml`](project-profile.yaml) | Record the living project identity, surfaces, modules, team, controls, and unknowns. |
| [`workstream-plan.md`](workstream-plan.md) | Split the current phase into bounded, non-conflicting assignments and select provider workflow skills. |
| [`review-ready.md`](review-ready.md) | Prompt a different eligible provider to review an exact candidate and carry it toward the configured merge gate. |
| [`project-state-report.md`](project-state-report.md) | Report a bootstrap, reshaped plan, milestone, pause, or completion without implying unperformed actions. |
| [`minimal-fix.md`](minimal-fix.md) | Record one eligible low-cost mechanical correction and its focused proof. |

## Use

1. Copy the template into the project-local channel, issue, document, or
   repository.
2. Replace every angle-bracket placeholder that is material now.
3. Delete optional sections that add no value.
4. Label uncertain values `provisional` or `unknown`; do not invent them.
5. Keep unconfirmed repositories, refs, spending, data access, and external
   actions analysis-only.
6. Select shared skills by catalog name only after the active session discovers
   them:
   - `cursor-agent-workflow`
   - `antigravity-sandbox-workflow`
   - `perplexity-research-workflow`
7. Treat skill selection as procedure, not permission. The project profile,
   provider contract, and deterministic gates remain authoritative. Canonical
   provider roles: [`docs/team-operating-charter.md`](../../team-operating-charter.md).
8. Use the event vocabulary from
   [`project-bootstrap-protocol.md`](../../project-bootstrap-protocol.md):
   `PROJECT_BOOTSTRAP` (intake record), `BOOTSTRAP`, `PLAN`, `WORKING`,
   `EVIDENCE`, `REVIEW_READY`, `DECISION`, `OWNER_DECISION_REQUIRED`,
   `RESHAPED`, `PAUSED`, `COMPLETE`.
9. Use `minimal-fix.md` only for the charter's one-attempt minimal-fix lane;
   changed candidates still require fresh exact-candidate evidence.
10. Preserve existing valid contribution tags and enclose each completed,
    human-readable template instance in a new matching UTC pair from
    [`contribution-attribution-policy.md`](../../contribution-attribution-policy.md).
    Keep tags outside syntax-sensitive or machine-readable payloads and record
    their authorship in the surrounding submission envelope.

Do not place secrets, credentials, tokens, raw private source, or unnecessary
source-thread content in any template. Name Slack destinations only from the
project profile or runtime configuration—never hardcode workspace channel names.
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | END]] -->
