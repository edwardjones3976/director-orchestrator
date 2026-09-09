<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 08:20 UTC | BEGIN]] -->
# Delegation matrix — 2026-09-13 delivery

**Issued by:** Claude, acting Director-Orchestrator · 2026-09-08
**Authority:** `docs/team-operating-charter.md`, `docs/provider-allocation-profiles.md`,
`docs/provider-profiles/`, `src/director_orchestrator/allocation_profiles.py`,
`workload_policy.py`, `AGENTS.md`. Where this document and any of those
disagree, they win. This document grants no authority and attests no route.

Read with [`docs/director-decision-rules.md`](director-decision-rules.md) and
[`docs/activation-plan-2026-09-13.md`](activation-plan-2026-09-13.md).

---

## 1. Lane eligibility, established from the profiles

Read from `docs/provider-profiles/*.yaml`, `routes.yaml`, and
`allocation_profiles.py` on the candidate. The deployment runs the **engineering**
allocation profile (`DEFAULT_ALLOCATION_PROFILE = "engineering"`), whose targets
are `openai 40, claude 25, cursor 15, grok 10, antigravity 10, perplexity 0,
firecrawl 0, github-copilot 0, microsoft-365-copilot 0` over a rolling window of
twenty admitted assignments, soft tolerance ±3 points, hard ceiling target +5.
**Zero target means ineligible.**

| Lane | Class | Assignable now | Governing constraint |
| --- | --- | --- | --- |
| **OpenAI / Codex** | full-spectrum primary | **Yes** — analysis, implementation, independent review | The profile records no repository execution lane for orchestrator-dispatched OpenAI production; the legacy registry has no OpenAI adapter. Repository work therefore lands through owner-launched Codex sessions, which is the surface that produced PRs #115–#120. Cannot accept or merge a candidate it materially authored |
| **Claude** | director control plane | **Yes** — implementation via a Claude Code session; independent review of candidates it did not produce | Cowork is not a synchronous adapter; the programmable bridge is a Claude Code Routine. Director coordination creates **no production credit**, so this session's work consumes none of Claude's quota |
| **Cursor** | full-spectrum primary | **Yes** — implementation, research, QA, independent exact-SHA review | Every run needs an allowlisted project, repository and durable starting ref. Auto-generated `cursor/...` branches are not durable starting points |
| **Grok** | full-spectrum primary | **Analysis yes; repository work and review verdicts conditional** | The in-process Responses API contact is synchronous, tool-free and **analysis only**; API output is never promoted into repository or review authority. Repository work and verdicts require the digest-verified Build CLI contact through an authenticated host runner. The host bridge preflight has not been run and activation bindings are `{}`, so Grok's verdict capability is `U` |
| **Antigravity / Gemini** | subordinate supplemental specialist | **Yes, in two closed forms only** | Denied `review_verdict`, `acceptance_verdict`, `repository_write`, `repository_access`, `branch_selection`, `contract_or_methodology_definition`, `external_write`, `merge`. Ordinary dispatch sends **no model-authored prose** — the request is composed only from fixed strings, closed enums and three bounded integers (`assignment_sequence`, `variant_count`, `max_lines_per_artifact`), and the validator rejects anything else. Withheld: task text, repository paths or source, acceptance criteria, owner objective. Every artifact must feed a later Claude, Cursor or Grok assignment that independently verifies and integrates it |
| **Perplexity** | evidence-only research | **No — target 0 under `engineering`** | Zero-target exclusion. Also requires an owner approval bound to the digest of the exact query. See the owner decision in §5 |
| **Firecrawl** | public search transport | **No** | Target 0; no legacy adapter; executes only on the LangGraph primary path; no variable in `.env.example` |
| **GitHub Copilot (Lite)** | supplemental hygiene reviewer | **Conditionally, post-freeze, non-gating** | Explicitly carries no workload share, range or ceiling, and no production credit, so a Lite observation does not consume a quota slot. Requires required checks green and a frozen exact candidate; fails closed without an authenticated contact; at most three conditional attempts; a substantive Copilot-authored fix would make it a material author and ineligible to review that candidate |
| **Microsoft 365 Copilot** | blocked | **No** | Live dependency discovery hard-codes the lane unauthenticated with `microsoft_365_copilot_work_iq_capability_evidence_missing`. It **must not** appear in the activation binding map, because the combined live signal is AND-ed across every configured binding |

`direct-provider-to-provider` is the one route catalogued as **prohibited**.
Every exchange between lanes in this matrix is brokered by the Director.

## 2. Integer quotas (R7)

Largest-remainder apportionment of the engineering targets over the
twenty-assignment window, with the soft band and hard ceiling as integer bounds:

| Lane | Target | Quota of 20 | Soft band | Ceiling | Consumed by this delivery |
| --- | ---: | ---: | ---: | ---: | ---: |
| OpenAI | 40% | **8** | 8 | 9 | 3 |
| Claude | 25% | **5** | 5 | 6 | 1 |
| Cursor | 15% | **3** | 3 | 4 | 2 |
| Grok | 10% | **2** | 2 | 3 | 2 |
| Antigravity | 10% | **2** | 2 | 3 | 2 |
| | | **20** | | | **10** |

Grok and Antigravity reach their exact target quota at two assignments each;
neither receives more work in this delivery without an explicit ceiling
decision. `workload_policy.py` is the authority at admission over the true
rolling window, which includes history this document does not hold. If it
refuses an assignment on band or cap, the Director re-routes rather than
appealing. Reviews and Director coordination are accounted separately from
direct production.

## 3. Assignment matrix — disjoint writers

One active writer per file. No lane reviews a candidate it materially authored.
No two assignments share a file.

| ID | Work | Lane | Kind | Files owned | Independent reviewer |
| --- | --- | --- | --- | --- | --- |
| **A-01** | WO-02 evidence blocker audit | OpenAI | analysis | none | Cursor |
| **A-02** | WO-03 open pull request triage | Cursor | analysis | none | Grok (A-12) |
| **A-03** | Fix partition **A** — evidence chain | OpenAI | implementation | `provider_activation.py`, `primary_provider_activation.py`, `provider_validation.py`, `tests/test_provider_activation.py`, `tests/test_provider_validation.py` | Cursor |
| **A-04** | Fix partition **B** — switch coupling | Cursor | implementation | `config.py`, `primary_default.py`, `tests/test_langgraph_configuration.py`, `tests/test_primary_default.py` | OpenAI |
| **A-05** | Fix partition **C** — control script and staging | OpenAI | implementation | `scripts/windows-langgraph-primary-control.ps1`, `primary_staging.py`, `tests/test_primary_staging_controls.py` | Claude Code |
| **A-06** | WO-06 n8n v2 — partition **D** | Claude Code | implementation | `n8n/workflows/director-intake.json`, `n8n/workflows/director-status.json`, `docs/n8n-operations.md` | Cursor |
| **A-11** | Adversarial negative-path analysis of the activation switch coupling | Grok | analysis | none | Director reconciles |
| **A-12** | Cross-check A-01 against A-02 for contradictions | Grok | analysis | none | Director reconciles |
| **A-13** | Closed-vocabulary fixture artifact — `RETRY_BACKOFF_VARIANTS`, `variant_count = 3`, `max_lines_per_artifact` bounded | Antigravity | closed dispatch | none — artifact is returned, never written to the repository | Cursor verifies and integrates or discards |
| **A-14** | Invited debate seat, one position on the profile-topology question | Antigravity | debate | none | Director synthesises; the seat carries no vote weight and no verdict |
| — | Lite hygiene observation on the frozen candidate | GitHub Copilot | non-gating | none | Findings independently validated; no production credit |

Partitions **A**, **B**, **C** and **D** are disjoint by construction. Partition
**E** (`docs/close-out-report-2026-09-13.md`, `docs/director-user-guide.md`) and
partition **F** (this document, the plan, the decision rules) are Director-owned
and reviewed by Cursor; the Director does not accept its own documents.

**Review independence check.** A-03 produced by OpenAI, reviewed by Cursor.
A-04 produced by Cursor, reviewed by OpenAI. A-05 produced by OpenAI, reviewed
by Claude Code — not by Cursor, which is already reviewing A-03, and not by the
Director session, which is not the producer but is also not the Claude Code
session that would review. A-06 produced by Claude Code, reviewed by Cursor.
No lane reviews its own material authorship. Antigravity never reviews.

**A note on A-05's reviewer.** If the Claude Code session that implements A-06
is the same session that reviews A-05, that is still independent — different
candidates, different authorship. If a single Claude session ends up authoring
both A-05 and A-06, A-05's review moves to Grok, and Grok's verdict then
requires the digest-verified CLI contact rather than the API contact.

## 4. Launch packets

Each packet is the complete task boundary for its lane. All of them require the
lane to read `AGENTS.md`, `docs/project-leadership.md` and
`docs/director-decision-rules.md` first, to carry contribution tags, to run
`PYTHONPATH=src python scripts/check_contribution_tags.py --base <base> --head <head>`
before handoff, and to stop in `OWNER_DECISION_REQUIRED` rather than expanding
scope. None of them may merge, deploy, publish, delete a protected ref, or
change a credential.

### A-03 — OpenAI / Codex — evidence-chain fixes

> Own only these files: `src/director_orchestrator/provider_activation.py`,
> `primary_provider_activation.py`, `provider_validation.py`, and their tests.
> Another lane owns `config.py` and `primary_default.py` concurrently — do not
> edit them; if a fix requires a change there, stop and report the dependency
> rather than crossing the partition.
>
> Implement the minimal bounded fixes your A-01 audit identified as open in the
> evidence chain, one pull request per blocker, tests added for each. Report the
> exact commands run and their results. Remove or relax no deterministic gate.
> Under R6, record for each change the files and digests it depends on.
> Cursor reviews at the exact head; you do not merge your own pull request.

### A-04 — Cursor — switch-coupling fixes

> Own only `src/director_orchestrator/config.py`, `primary_default.py` and their
> tests. OpenAI owns the evidence-chain modules concurrently — do not edit them.
>
> Start from the durable candidate ref, not an auto-generated `cursor/...`
> branch. Implement the minimal bounded fixes for the offline-simulation and
> live-dispatch coupling questions A-01 raised, with tests. Postgres-backed
> suites run through `scripts/windows-postgres-test-lane.ps1` or are reported as
> a named coverage gap. A draft pull request is permitted. OpenAI reviews at the
> exact head.

### A-05 — OpenAI / Codex — control script and staging

> Own only `scripts/windows-langgraph-primary-control.ps1`,
> `src/director_orchestrator/primary_staging.py` and
> `tests/test_primary_staging_controls.py`.
>
> Bring the deployed control-script behaviour into line with what PR #115
> corrected, and confirm from the source whether `Activate` tolerates
> `LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED=true` at start and a
> pre-existing `intake_owner = langgraph` row. Changing behaviour requires a
> test that fails before and passes after. A Claude Code session reviews.

### A-06 — Claude Code — n8n v2

> Own only `n8n/workflows/director-intake.json`,
> `n8n/workflows/director-status.json` and `docs/n8n-operations.md`. Touch no
> Python source; three other lanes hold those partitions.
>
> Harden intake: bearer secret through an n8n credential and never inline, the
> Slack receipt fields required since PR #118 forwarded intact, a stable
> idempotency key from the Slack event ID, non-2xx mapped to an explicit webhook
> error response, no raw provider output echoed. Add the status workflow polling
> `/health` on both new profiles, diffing readiness booleans, posting to
> `SLACK_DIRECTOR_CHANNEL_ID` only on change and immediately on ambiguous
> effects, kill switch, or unreachable health. Validate both with
> `python -m json.tool`. Workflow JSON carries no inline contribution tag; it is
> attributed through the pull request. Cursor reviews.

### A-11 — Grok — adversarial negative-path analysis

> Analysis only. This assignment issues no verdict, writes no repository file,
> and its output is not promoted into review authority. The in-process API
> contact is sufficient for it.
>
> Attack the activation switch coupling from the failure side. Given
> `config.py`'s mutual exclusion of offline simulation and live dispatch, the
> bidirectional coupling of `ALLOW_PAID_RUNS` with live dispatch, and the
> `Rollback` refusal paths that fire after intake has been stopped and the
> switches written false: enumerate the states a deployment can enter from which
> neither `Activate` nor `Rollback` will proceed. For each, name the exact
> source predicate that traps it and what an operator would have to do. Rank by
> how reachable each state is from the plan's Thursday–Saturday sequence.

### A-12 — Grok — cross-check

> Analysis only, same contact and same limits as A-11. Read A-01's blocker
> findings and A-02's pull request triage as data. Report every contradiction
> between them, every claim in either that is asserted without a file, line or
> SHA reference, and every place where they disagree about whether a PR closed a
> blocker. Do not resolve the contradictions — name them.

### A-13 — Antigravity — closed dispatch

> Not free text. Dispatch through the ordinary subordinate contract with
> capability `LOW_LEVEL_MECHANICAL_IMPLEMENTATION`, fixture lane
> `RETRY_BACKOFF_VARIANTS`, `variant_count = 3`, and `max_lines_per_artifact`
> set within its approved integer range. The closed-vocabulary validator rejects
> any other outbound string, and the contract withholds the task text,
> repository paths, source, acceptance criteria and owner objective.
>
> The returned artifact is non-authoritative evidence. Cursor independently
> verifies it and either integrates the useful part into the reconciliation
> backoff work under its own authorship, or discards it. Antigravity receives no
> production credit for the integration and issues no verdict.

### A-14 — Antigravity — invited debate seat

> One position under the debate contract, phase `position`, on whether the
> three-profile topology or an in-place repair of the existing deployment
> carries less risk. The seat carries no vote weight, no verdict, and no
> acceptance authority. The Director synthesises it with the primary positions
> and preserves dissent. A predicted position is never recorded as
> participation.

## 5. Owner decisions this raises

**D-07 — Perplexity and public research.** Under the `engineering` profile
Perplexity's target is zero, which makes it ineligible; a research assignment to
it would violate zero-target exclusion. Switching to `mixed` or
`public_research` would make it eligible but would simultaneously change every
other lane's target — `mixed` moves OpenAI to 25 and Claude to 30, and
`public_research` moves Cursor and Grok to 5 each. The Director's recommendation
is **not** to change the allocation profile mid-delivery, and to leave Perplexity
unassigned until after Sunday. If the owner wants public research inside this
delivery, that is an explicit profile change with a recorded digest, not a
one-off exception.

**D-08 — Grok's contact class.** Grok's two assignments are analysis, which the
API contact serves. If the Director later needs Grok as an exact-SHA reviewer —
which happens if a single Claude session authors both A-05 and A-06 — the
digest-verified Build CLI contact and an authenticated host runner are required
first. Confirm whether the owner wants the host bridge preflight run for that
contingency, or whether the fallback should instead be to re-partition the work.

**D-09 — Copilot Lite.** Permitted post-freeze and non-gating. Confirm whether
an authenticated contact exists; without one the lane fails closed, records no
verdict, and the attempt counts as waste.

## 6. What this matrix does not claim

Lane membership and an allocation share do not attest that a provider route is
enrolled, authenticated or live. No packet here has been dispatched: the
Director holds no provider transport in this session, and dispatching one would
be an external action requiring the owner. Every assignment above is a prepared
work order awaiting owner launch. The profile sheets grant no authority, and a
documented route is not an authorized one.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 08:20 UTC | END]] -->
