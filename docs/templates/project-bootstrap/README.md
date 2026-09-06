<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 22:30 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | BEGIN]] -->
# Project bootstrap template kit

These templates turn the
[modular project bootstrap protocol](../../project-bootstrap-protocol.md) into
copyable project-local artifacts. Use the smallest useful subset. A short
investigation still receives the mandatory rollout packet; only the remaining
templates are optional and should be selected when they add value.

| Template | Purpose |
| --- | --- |
| [`intake.md`](intake.md) | Paste into the new project's Slack thread or conversation to shape the project from a source thread. |
| [`project-profile.yaml`](project-profile.yaml) | Record the living project identity, surfaces, modules, team, controls, and unknowns. |
| [`rollout-packet.md`](rollout-packet.md) | Record the deployment profile, allowlists, dedicated Slack app/channel isolation, activation evidence, fallback, and rollback for every presented project. |
| [`provider-roles-contract.yaml`](provider-roles-contract.yaml) | Record the project-local eight responsibility areas and the Cursor, Antigravity, Perplexity, and OpenAI boundaries. |
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
6. Create [`rollout-packet.md`](rollout-packet.md) for every project presented
   to the Director. Concurrently active deployments require a distinct Slack
   app, project channel, and profile/state namespace. Pending identities block
   Slack activation but not analysis-only shaping. Record secret references and
   readiness only—never token values. The rollout packet is mandatory; only the
   other bootstrap templates may be omitted.
7. Select shared skills by catalog name only after the active session discovers
   them:
   - `cursor-agent-workflow`
   - `antigravity-sandbox-workflow`
   - `perplexity-research-workflow`
   - `pplx-cli`
     remains a Perplexity research-only terminal transport.
8. Treat skill selection as procedure, not permission. The project profile,
   provider contract, and deterministic gates remain authoritative. Canonical
   provider roles: [`docs/team-operating-charter.md`](../../team-operating-charter.md).
   Copy [`provider-roles-contract.yaml`](provider-roles-contract.yaml) whenever you
   use [`project-profile.yaml`](project-profile.yaml), including single-provider
   projects. The profile always sets `team.provider_roles_contract`, so omitting the
   companion file leaves that reference dangling. It mirrors the charter and grants
   no authority of its own; a one-provider project simply records that provider's
   boundaries and leaves the rest unstaffed.
9. Use the event vocabulary from
   [`project-bootstrap-protocol.md`](../../project-bootstrap-protocol.md):
   `PROJECT_BOOTSTRAP` (intake record), `BOOTSTRAP`, `PLAN`, `WORKING`,
   `EVIDENCE`, `REVIEW_READY`, `DECISION`, `OWNER_DECISION_REQUIRED`,
   `RESHAPED`, `PAUSED`, `COMPLETE`.
10. Use `minimal-fix.md` only for the charter's up-to-three-attempt minimal-fix lane;
   changed candidates still require fresh exact-candidate evidence.
11. Preserve existing valid contribution tags and enclose each completed,
    human-readable template instance in a new matching UTC pair from
    [`contribution-attribution-policy.md`](../../contribution-attribution-policy.md).
    Keep tags outside syntax-sensitive or machine-readable payloads and record
    their authorship in the surrounding submission envelope.

Do not place secrets, credentials, tokens, raw private source, or unnecessary
source-thread content in any template. Name Slack destinations only from the
project profile or runtime configuration—never hardcode workspace channel names.
<!-- [[Codex | GPT-5.6 | 2026-08-22 | 00:01 UTC | END]] -->
<!-- [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 21:35 UTC | END]] -->
<!-- [[Claude Code | claude-code/chief-programmer | 2026-08-24 | 22:30 UTC | END]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 22:10 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->
