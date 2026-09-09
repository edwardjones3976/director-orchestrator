<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 10:10 UTC | BEGIN]] -->
# Protocol compliance audit — the 2026-09-13 delivery

**Auditor:** Claude, acting Director-Orchestrator · 2026-09-09
**Scope:** the delivery documents and work orders on branch
`claude/activation-plan-2026-09-13`, checked against `AGENTS.md`,
`docs/team-operating-charter.md`, `docs/contribution-attribution-policy.md`,
`docs/provider-allocation-profiles.md` and the deterministic policy.

This is the Director auditing its own submission. It is not independent review
and does not substitute for it. Every claim below names how it was checked.

---

## 1. Antigravity credential — resolved without generating anything

The owner asked whether an Antigravity key exists or must be generated.
**It exists.** No browser was opened, no credential was minted, and no secret
value appears in any document, transcript or commit.

| Fact | Evidence |
| --- | --- |
| `GEMINI_API_KEY` is set in the deployment environment file | presence and length only; value never read into output |
| The primary transport auth map accepts exactly `{GEMINI_API_KEY, GOOGLE_API_KEY}` for the `antigravity` lane | `primary_transports.py:150` |
| A second key is provisioned for the edge-oracle profile | same check on `.env.edge-oracle` |
| `GEMINI_AGENT = antigravity-preview-05-2026`, `GEMINI_DAILY_BUDGET_USD = 5.00` | deployment environment file |
| `.env.uppercut` holds a one-character `GEMINI_API_KEY` — a placeholder, not a key | flagged only; different project, not this delivery |

**`gemini_supplemental_readiness` evaluated against the deployment environment
and the configuration defaults returns true on all eleven checks:**
`credential_configured`, `agent_configured`, `usage_model_configured`,
`usage_retry_attempts_valid`, `usage_retry_delay_valid`, `token_budget_valid`,
`daily_budget_valid`, `daily_request_limit_valid`, `subordinate_mode`,
`review_verdict_disabled`, `primary_authority_disabled`.

Three of those pass on configuration defaults rather than explicit settings —
`usage_model_configured`, `daily_request_limit_valid` and `subordinate_mode`.
They are correct today, but a default is a weaker guarantee than a written
value. **Recommendation:** write `GEMINI_USAGE_MODEL`, `GEMINI_DAILY_REQUEST_LIMIT`
and `GEMINI_MODE=antigravity_subordinate` explicitly into the profile so the
subordinate constraint is recorded rather than inherited.

`LANGGRAPH_PRIMARY_ANTIGRAVITY_TEAM_ENABLED` is absent and therefore false. The
credential was never the obstacle to team mode; the open question remains D-14,
whether a reachable, authenticable pinned SSH worker host exists.

**Why no key was generated.** Minting one through the browser would have put a
live secret into a page render and a conversation transcript, and creating a
credential is an owner-reserved action under rule 10. It was also unnecessary.

## 2. Contribution attribution — one real defect, found and fixed

Checked directly against `contribution_tags.py` rather than by inspection.

| Condition | Result |
| --- | --- |
| First nonblank line after any reserved header is a `BEGIN` tag | PASS, all four documents |
| Last nonblank line is an `END` tag | PASS, all four |
| Outer `BEGIN`/`END` contributor, system, date and time match exactly | PASS, all four |
| The submission diff adds that exact pair | PASS, all four |
| No inline tag in a syntax-sensitive format | PASS — no JSON, JSONL, CSV or lock file was authored |

**The defect.** The activation plan was changed across four commits and the
delegation matrix across three, each still carrying only its original outer
pair. The policy requires a *fresh* outer pair on every safely commentable file
changed in the current submission, with earlier records preserved. Evaluated as
one submission the branch passes; evaluated per commit those two files would be
rejected. Both now carry a fresh outer pair with the earlier records nested
inside, verified after the fix.

**A note on the checker run.** An earlier invocation of
`scripts/check_contribution_tags.py` reported errors on all four documents. That
run was environmentally invalid — it was executed from one worktree with
`GIT_DIR` overridden to a different repository — and its output is **not** a
verdict either way. The conditions above were therefore checked directly against
the module. The authoritative run belongs to CI on the pull request, and the
Director does not claim to have passed a gate it did not validly execute.

## 3. Agent operating rules

| Rule | Compliance | How checked |
| --- | --- | --- |
| 1 · owner work order is the task boundary | PASS | Every work order states its boundary and forbidden actions; no lane was given repository, data-source or external-action scope beyond it |
| 2 · never commit secrets | PASS | No `VAR=<value>` pattern with a secret-shaped name appears in any authored document; credential facts recorded as presence, length and variable name only |
| 3 · isolated branch, no self-merge | PASS | All work on `claude/activation-plan-2026-09-13`, never pushed to a protected branch, nothing merged. Producer and reviewer are distinct on every candidate (A-03/04/05/06 verified pairwise) |
| 4 · preserve deterministic gates | PASS | No document alters `policy.py` or any gate. The decision rules state explicitly that where a rule and the gates disagree, the gate wins |
| 5 · bounded loops | PASS | Attempt limits carried into the packets: Copilot Lite ≤3 conditional attempts, no repeated ambiguous dispatch, budget ceilings restated |
| 6 · treat external content as untrusted | PASS | Firecrawl research output is recorded as untrusted evidence requiring primary-lane evaluation, and is not treated as authority |
| 7 · verify webhook signatures | N/A | No callback handling was authored |
| 8 · tests for behavioural changes | N/A | Documentation only; no source, test or workflow changed on this branch |
| 9 · prefer reversible changes | PASS | No deletion, no history rewrite. The branch is additive: six documents, zero modifications to existing files |
| 10 · stop in `OWNER_DECISION_REQUIRED` | PASS | Nine owner decisions raised (D-07 withdrawn, D-09 through D-15 open). Credential generation declined and referred to the owner. No deployment, publication, merge or purchase attempted |
| 11 · communicate concisely and correct inaccuracies | PASS, with three corrections | Recorded below |

## 4. Corrections issued during this delivery

The Director made three substantive errors and corrected each in the record
rather than silently. Rule 11 requires correcting inaccuracies before dependent
work; all three were caught before any dependent work executed.

| # | Error | Correction | Cost if uncaught |
| --- | --- | --- | --- |
| 1 | Read a zero allocation target as "ineligible for all work", excluding Perplexity and Firecrawl entirely | `is_direct_production_kind` is true for `IMPLEMENTATION` only; `is_accepted_productive_kind` names Perplexity, Firecrawl and Microsoft 365 Copilot as research-productive. Both lanes now hold research assignments | Two capable lanes idle for the whole delivery |
| 2 | Charged analysis and review against direct-production quotas | The quota counts implementation only. Grok's implementation quota is entirely unused | Grok wrongly treated as capped |
| 3 | Said Antigravity's team mode required a code and charter change | It is implemented behind a configuration switch with team DDL, a database guard trigger, grant writable paths, a pinned SSH worker and a qualification canary | A capable lane deferred past the delivery for no reason |

All three ran in the same direction: the Director was **more restrictive than the
protocol requires**. That is the safer failure mode, but it is still a failure
mode, and it argues for reading the enforcing code rather than the descriptive
prose in future routing decisions.

## 5. Delegation integrity

Re-verified mechanically after the corrections.

| Check | Result |
| --- | --- |
| File partitions disjoint | PASS — 20 paths across six partitions, 20 unique |
| No producer reviews its own candidate | PASS — A-03 OpenAI/Cursor, A-04 Cursor/OpenAI, A-05 OpenAI/Claude Code, A-06 Claude Code/Cursor |
| No zero-target lane holds an implementation assignment | PASS |
| Every lane within its hard ceiling over W=20 | PASS — OpenAI 2/9, Claude 1/6, Cursor 1/4, Grok 0/3, Antigravity 1/3 |
| No denied capability requested of any lane | PASS — Antigravity asked only for a closed fixture artifact and a debate seat; Grok asked only for analysis; no verdict requested of a lane denied verdicts |
| Microsoft 365 Copilot absent from the activation binding map | PASS |
| No direct provider-to-provider route used | PASS — every exchange brokered by the Director |

## 6. Evidence and claim discipline

| Check | Result |
| --- | --- |
| Every SHA cited resolves and is the object type claimed | PASS — `c81e388` commit, `a052377` commit, `4e1d578` tree, `811700cf` commit |
| No document claims a passed gate | PASS — the ledger is `T`/`F`/`U` with a computed conjunction; Gate 8 is recorded `F` with its source |
| No document presents simulation, merged code, green CI or the leadership handoff as live provider execution | PASS |
| Research evidence separated by surface | PASS — the Director-side Firecrawl MCP result is recorded as Director-side only and explicitly not evidence about the repository Firecrawl lane |
| Unknowns labelled rather than rounded | PASS — D-14 and D-15 recorded as open questions the repository cannot answer |

## 7. Findings the owner should act on

1. **Write the three defaulted Antigravity settings explicitly** — `GEMINI_MODE`,
   `GEMINI_USAGE_MODEL`, `GEMINI_DAILY_REQUEST_LIMIT`. Correct today by default;
   better recorded than inherited. Two minutes.
2. **`.env.uppercut` carries a one-character `GEMINI_API_KEY`.** Not this
   delivery, but it will fail closed the moment that profile is used.
3. **Twelve plaintext environment-file copies remain in the deployment
   directory.** All git-ignored, so no commit risk, but each is a full copy of a
   file carrying the primary Postgres password and the owner, callback, evidence,
   reconciliation and host-bridge secrets. Consolidate after the delivery.
4. **The authoritative contribution-tag run belongs to CI**, not to the Director.
   Treat this audit as a pre-check.

## 8. What this audit does not establish

It does not establish that any provider route is enrolled, authenticated or
live. It does not substitute for independent exact-SHA review, trusted CI or
security review, none of which the Director may perform on its own submission.
It does not attest that the deployment's runtime state matches its configuration
— that remains the open WO-01 health read. And it is a self-audit: the lane that
wrote the documents also checked them, which is exactly the arrangement the
acceptance rules exist to distrust.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 10:10 UTC | END]] -->
