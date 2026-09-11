<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:30 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 12:00 UTC | BEGIN]] -->
# Delegation round — 2026-09-09, ASAP scope

**Issued by:** Claude, acting Director-Orchestrator

**Where this is referenced:** `docs/close-out-report-2026-09-13.md` and `docs/activation-plan-2026-09-13.md` should link here (they have not yet been updated to reflect this supersession).

**Supersedes** the assignment table in `docs/delegation-matrix-2026-09-13.md` for
this round. The matrix's lane eligibility, quota arithmetic and independence
rules are unchanged and still govern.

The scope changed twice since that matrix was written: Gate 8 was determined `F`,
and the owner reduced the objective to earliest completion. Several assignments
were sized for a live path that no longer exists. Re-cutting rather than letting
them stand, because dispatching work nobody needs is exactly the waste R3 exists
to prevent.

---

## What is cut, and why

| Was | Now | Reason |
| --- | --- | --- |
| **A-03, A-04, A-05** — three fix-PR partitions | **Cut from this round** | The five blockers live in the `LIVE_OK` and live-dispatch path. That path is not traversed by the floor deliverable, so the fixes are follow-on, not delivery work |
| **A-15** — Perplexity research on n8n auth | **Cut under R3** | The question it would answer has been answered: the A-16 Director-side research established `httpBearerAuth` for the bearer call, Header/JWT auth on the inbound hop, and `Respond to Webhook` for non-2xx mapping. No outcome of A-15 now changes any action, so its value of information is zero. Perplexity is admissible only under a profile with a non-zero target (public_research gives it 25%); under engineering it is refused; it simply has no live question this round |
| **A-13, A-14** — Antigravity fixture artifact and debate seat | **Cut from this round** | A-13's retry-backoff variants feed reconciliation work that is now follow-on. A-14's debate question — three-profile topology versus in-place repair — was settled when profile L left the plan. Antigravity's credential is provisioned and its readiness passes all eleven checks; it has no question this round, which is a scoping fact and not a capability judgement |
| **A-01** — full evidence blocker audit | **Narrowed** | Now one question about the `Accept`/`Migrate`/Stage A path only |

Cutting four assignments is the point of R3, not a failure of planning. Each was
correct for the scope it was written against.

## Assignments this round

| ID | Work | Lane | Blocked on |
| --- | --- | --- | --- |
| **A-01′** | Narrowed audit: does any of the five evidence blockers affect `PrepareAcceptance` → `Accept` → `Migrate` → `Backup` → `RestoreDrill`, or the Stage A switch set and its health verification? Live-dispatch and `LIVE_OK` paths explicitly out of scope | Codex | nothing — launchable now |
| **A-02** | Open pull request triage, including whether `main` has moved past `c81e388` | Cursor | nothing — launchable now |
| **A-06** | n8n v2: intake hardening and the status workflow | Claude Code session | nothing — launchable now |
| **A-11** | Adversarial negative-path analysis of the activation switch coupling | Grok | nothing — analysis contact suffices |
| **A-18** | Independent exact-SHA review of the documentation candidate `DIR-2026-09-09-DOCS-01` | Cursor | the pull request existing |
| **A-19** | Independent exact-SHA review of the A-06 n8n candidate | Cursor, or Codex if Cursor is loaded | A-06 landing |
| **A-17** | Copilot Lite hygiene observation | GitHub Copilot | the frozen candidate, checks green |

Implementation quota consumed this round: Claude 1 of 6 (A-06). Everything else
is analysis, triage or review, which consumes none.

## Independence

A-06 is produced by a Claude Code session and reviewed by Cursor. A-18 reviews
Director-authored documents, and the Director is ineligible to review or accept
them. No lane reviews its own material authorship in this round. Merge stays
owner-only and is not delegable.

## A-06 specification, pre-staged

The implementer should need no further questions. Findings already established,
so they are not re-derived:

**Intake** (`n8n/workflows/director-intake.json`)
- Outbound call to `/v1/intake`: generic auth type **`httpBearerAuth`**, not
  `httpHeaderAuth`. `httpHeaderAuth` is for custom headers such as `X-API-Key`;
  the bearer credential type takes a single token field and is the correct one
  for `Authorization: Bearer`.
- Inbound hop: enable Webhook node authentication — Header or JWT credential.
  Never an inline secret in a node parameter.
- Forward the Slack intake receipt fields required since PR #118, intact.
- Idempotency key: the stable Slack event ID, unmodified.
- Non-2xx from `/v1/intake`: set `Respond` to **Using 'Respond to Webhook' Node**
  and map the failure to an explicit error response with a real status code.
  Do not let a failure return 200.
- Consider the Webhook node's IP allowlist and bot-ignore options.
- Never echo raw provider output into the response body.

**Status** (`n8n/workflows/director-status.json`, new)
- Schedule trigger polling `GET /health` on the profiles in play, and
  `/v1/staging/status` only while a staging app is running.
- Diff the readiness booleans against the previous run using workflow static
  data; post **only on change**.
- Post immediately, regardless of change, when `ambiguous_effect_count > 0`, the
  parallel kill switch is set, or health is unreachable.
- Target the configured Director channel variable. Never a literal channel ID in
  the workflow.
- Never post secret, DSN or allowlist values.

**Both**: validate with `python -m json.tool`. Workflow JSON carries **no inline
contribution tag** — it is a syntax-sensitive contract and is attributed through
the pull request envelope instead. Document in `docs/n8n-operations.md`,
including how the webhook is repointed between profiles.

## Standing constraints, unchanged

`ALLOW_PAID_RUNS`, live provider dispatch and the provider activation bindings
stay closed. Nothing in this round opens them. A-11 is a paid provider call; it
is blocked while `ALLOW_PAID_RUNS` stays closed, and requires explicit owner
authorization of the paid gate before it can run. Every submission carries a fresh
outer contribution pair per changed commentable file. Merge current `main` into
the branch (a merge commit, never a rebase or force-push) before opening any pull
request — the attribution job diffs `pull_request.base.sha`..`head`, so a branch
behind `main` fails on files it never touched.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 12:00 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:30 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:25 UTC | END]] -->
