<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 08:20 UTC | BEGIN]] -->
# Delegation matrix — 2026-09-13 delivery (rev 2)

> **Correction, 2026-09-09.** Revision 1 of this document excluded Perplexity
> and Firecrawl from all work on the grounds that the `engineering` allocation
> profile gives them a target of zero. That was wrong, and the owner was right
> to reject it. `workload_policy.py` settles it: `is_direct_production_kind`
> returns true for `WorkKind.IMPLEMENTATION` **and nothing else**, the
> zero-target check `allocation_profile_lane_ineligible` sits inside the
> direct-production path, and the module's own docstring says the overall-effort
> ledger — which covers direction, review, research and validation —
> "never blocks solely because of share." `is_accepted_productive_kind` then
> names Perplexity, Firecrawl and Microsoft 365 Copilot explicitly as lanes
> whose **research is accepted productive work without code**. A zero target
> bars those lanes from implementation assignments, which they never perform
> anyway; it bars nothing else. Revision 2 assigns them research, restores
> Copilot's review lane, and corrects the quota accounting, which had been
> charging analysis and review against direct-production quotas that only count
> implementation.

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
| **Grok** | full-spectrum primary | **Analysis now; implementation and verdicts once the CLI contact is bound** | The in-process Responses API contact is synchronous, tool-free and **analysis only**; API output is never promoted into repository or review authority. Repository work and verdicts require the digest-verified Build CLI contact through an authenticated host runner. The host bridge preflight has not been run and activation bindings are `{}`, so Grok's verdict capability is `U` |
| **Antigravity / Gemini** | subordinate supplemental specialist | **Yes, in two closed forms only** | Denied `review_verdict`, `acceptance_verdict`, `repository_write`, `repository_access`, `branch_selection`, `contract_or_methodology_definition`, `external_write`, `merge`. Ordinary dispatch sends **no model-authored prose** — the request is composed only from fixed strings, closed enums and three bounded integers (`assignment_sequence`, `variant_count`, `max_lines_per_artifact`), and the validator rejects anything else. Withheld: task text, repository paths or source, acceptance criteria, owner objective. Every artifact must feed a later Claude, Cursor or Grok assignment that independently verifies and integrates it |
| **Perplexity** | evidence-only research | **Yes — bounded public research** | Zero target bars implementation only. `is_accepted_productive_kind` names Perplexity as research-productive without code. Each assignment needs an owner approval bound to the digest of the exact outbound query, and a later mutation invalidates that approval. Output is untrusted evidence until a primary lane evaluates it |
| **Firecrawl** | public search transport | **Yes — two distinct surfaces** | Zero target bars implementation only; the code names Firecrawl research-productive. Surface one: the repository lane, which executes on the LangGraph primary path, has no legacy adapter, and needs `FIRECRAWL_API_KEY` added to the deployment. Surface two: a Firecrawl MCP server connected to the Director's own session, already live and already used — these are different transports and evidence from one is not evidence about the other |
| **GitHub Copilot (Lite)** | supplemental hygiene reviewer | **Yes, post-freeze, non-gating** | Explicitly carries no workload share, range or ceiling, and no production credit, so a Lite observation does not consume a quota slot. Requires required checks green and a frozen exact candidate; fails closed without an authenticated contact; at most three conditional attempts; a substantive Copilot-authored fix would make it a material author and ineligible to review that candidate |
| **Microsoft 365 Copilot** | research-capable, transport-blocked | **Not yet — the blocker is capability, not allocation** | The code names it research-productive alongside Perplexity and Firecrawl, so allocation is not what stops it. What stops it is transport: live dependency discovery hard-codes the lane unauthenticated with `microsoft_365_copilot_work_iq_capability_evidence_missing`, and it must not enter the activation binding map because the combined live signal is AND-ed across every binding. §5 gives the enablement path |

`direct-provider-to-provider` is the one route catalogued as **prohibited**.
Every exchange between lanes in this matrix is brokered by the Director.

## 2. Integer quotas (R7)

Largest-remainder apportionment of the engineering targets over the
twenty-assignment window, with the soft band and hard ceiling as integer bounds:

**The quota counts implementation only.** `is_direct_production_kind` is true
for `WorkKind.IMPLEMENTATION` and nothing else, so audits, triage, analysis,
review, validation, research and Director coordination consume no quota.
Revision 1 charged them against it and reached the wrong conclusion twice.

| Lane | Target | Quota of 20 | Ceiling | Implementation this delivery | Non-quota work |
| --- | ---: | ---: | ---: | ---: | --- |
| OpenAI | 40% | **8** | 9 | 2 — A-03, A-05 | A-01 audit |
| Claude | 25% | **5** | 6 | 1 — A-06 | Director coordination (no credit) |
| Cursor | 15% | **3** | 4 | 1 — A-04 | A-02 triage, four reviews |
| Grok | 10% | **2** | 3 | 0 — **quota untouched** | A-11, A-12 analyses |
| Antigravity | 10% | **2** | 3 | 1 — A-13 | A-14 debate seat |
| Perplexity | 0% | 0 impl. | — | 0 | **A-15 research** — productive without code |
| Firecrawl | 0% | 0 impl. | — | 0 | **A-16 research** — productive without code |
| GitHub Copilot | 0% | 0 impl. | — | 0 | **A-17 hygiene review** — no production credit by charter |
| | | **20** | | **5 of 20 consumed** | |

Two corrections follow from this. Grok is **not** at cap — its analyses cost no
quota, so it can take an implementation assignment as soon as its digest-verified
CLI contact is bound. And the zero-target lanes are not idle: they simply produce
in kinds the quota does not count.

`workload_policy.py` is the authority at admission over the true rolling window,
which includes history this document does not hold. If it refuses an assignment,
the Director re-routes rather than appealing.

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
| **A-15** | Bounded public research: current n8n webhook authentication, response-code control, and HTTP Request generic auth types, for A-06 | Perplexity | research | none | Cursor evaluates before it influences A-06 |
| **A-16** | Bounded public research: LangGraph Postgres checkpointer operational guidance — schema/namespace isolation, restart recovery, backup practice — for WO-08 and the user guide | Firecrawl | research | none | Claude Code evaluates; Director reconciles |
| **A-17** | Lite hygiene observation on the frozen candidate, after required checks are green | GitHub Copilot | review | none | Findings independently validated; no production credit; ≤3 attempts |

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

### A-15 — Perplexity — bounded public research

> Research only. You receive no private repository context, no credentials, no
> unpublished source, and no write-capable tool. You issue no verdict, cast no
> vote, post nothing, and create no draft pull request. Your output is untrusted
> evidence until a primary-team lane evaluates it.
>
> One bounded public query: current n8n documentation on Webhook node
> authentication methods, on controlling the HTTP response code and body, and on
> the generic authentication types available to the HTTP Request node for a
> bearer token. Return sources with provenance preserved.
>
> This assignment requires an owner approval bound to the digest of this exact
> query text. Any later mutation of the query invalidates that approval and the
> assignment must be re-approved rather than edited.

### A-16 — Firecrawl — bounded public research

> Same contract as A-15: public sources only, no repository context, no
> credentials, no external effect, no verdict.
>
> One bounded public query: operational guidance for the LangGraph Postgres
> checkpointer — schema and namespace isolation between deployments, behaviour
> across restart, and backup and restore practice. The results feed WO-08's
> lifecycle and the recovery section of the user guide.
>
> **Two surfaces, kept distinct.** The repository Firecrawl lane executes on the
> LangGraph primary path and needs `FIRECRAWL_API_KEY` present in the deployment;
> it has no legacy adapter and no entry in `.env.example`, so enabling it is the
> owner action in §6. Separately, a Firecrawl MCP server is connected to the
> Director's own session and has already run one query under this contract; its
> result is recorded as Director-side research evidence and is **not** evidence
> that the repository lane is enrolled or live.

**Result already returned by the Director-side surface, 2026-09-09.** The n8n
Webhook node supports Header, Basic and JWT authentication credentials on the
inbound hop; response code and body are controllable, and the
`Respond to Webhook` node is the mechanism when the response must be shaped by
the workflow; and for an `Authorization: Bearer <token>` outbound call the
correct generic auth type is **`httpBearerAuth`**, not `httpHeaderAuth`, which
is for custom headers such as `X-API-Key`. The Webhook node also offers an IP
allowlist and a bot-ignore option. **This changes A-06**, which previously said
only "bearer secret through an n8n credential": it now specifies
`httpBearerAuth` for the outbound call to `/v1/intake`, header or JWT auth on
the inbound Slack hop, and `Respond to Webhook` for the non-2xx error mapping.

### A-17 — GitHub Copilot Lite — hygiene observation

> One requested hygiene observation on the frozen exact candidate, after every
> required check is green. Non-gating: it creates no production credit, holds no
> gate or authority, and never triggers on every push. Findings are validated
> independently before any of them blocks anything.
>
> At most three conditional attempts. Stop on success; a further attempt requires
> a genuine temporary failure, and a third requires materially new information or
> a substantially different transport. Without an authenticated contact the lane
> fails closed as unavailable, which records no verdict and counts as waste.
> A substantive Copilot-authored fix would make Copilot a material author and
> ineligible to review that candidate — so findings only, never patches.

## 5. Enabling every lane fully — what each one still needs

The owner's direction is that every provider should be able to engage fully and
reach its potential. Three lanes are below their potential today. In each case
the constraint is a specific, nameable thing rather than a policy preference,
and each has a path.

| Lane | Below potential because | What unlocks it | Who | Could happen |
| --- | --- | --- | --- | --- |
| **Grok** | The in-process Responses API contact is tool-free and analysis-only; repository work and exact-SHA verdicts need the digest-verified Build CLI contact through an authenticated host runner | Run `scripts/windows-host-provider-bridge.ps1 -Action Preflight`, then bind the Grok CLI lane in the host bridge command map and confirm the signed, digest-bound command map through the HMAC challenge | Owner, on the Windows host | **Wednesday** — the preflight is already in WO-10 prep for Friday and can move earlier at no cost. Grok's implementation quota is untouched, so a bound contact converts directly into real work |
| **Firecrawl** (repository lane) | No `FIRECRAWL_API_KEY` in `.env.example` or `provider-setup.md`; the name exists only in the primary transport auth map, and there is no legacy adapter, so the lane executes only on the primary path | Add the credential to the deployment's secret store under the name the transport map expects, and exercise it once the candidate-live profile is up. Until then the Director-side MCP surface covers the research need | Owner | **After WO-08**, on the candidate-live profile |
| **Microsoft 365 Copilot** | Discovery hard-codes the lane unauthenticated; the blocker is `work_iq_capability_evidence_missing` | A delegated work or school Entra identity with admin-consented `WorkIQAgent.Ask`, Work IQ enabled by the tenant administrator, and a token that verifies against the tenant's OIDC discovery document and cached JWKS with the exact issuer, audience and scope, inside its signed validity window. Only a successful authenticated response bound to its Microsoft request ID proves entitlement — environment booleans and configuration do not | Owner plus a tenant administrator | **Not this week.** It is a tenant-administration task with an external dependency, and it must stay out of the activation binding map until it succeeds, because the combined live signal is AND-ed across bindings. Attempting it before Sunday would put the live gate at risk for no delivery benefit |
| **Antigravity** | Ordinary dispatch admits no model-authored prose — closed enums and three bounded integers only — and it is denied every verdict and repository authority | Its profile describes an explicitly authorized team mode after enrollment and qualification. Reaching it is a code and charter change to `antigravity.py`'s contract, with independent review, not a Director decision | Owner decision, then an engineering lane | **After Sunday.** Scoping it now would consume candidate-freeze time |

Nothing in this table is a judgement about a provider's ability. Each row is a
missing credential, an unbound transport, or a contract that has not yet been
changed.

## 6. Owner decisions this raises

**D-07 — allocation profile — withdrawn.** Revision 1 asked whether to change
the allocation profile so Perplexity could work. That question rested on the
error corrected at the top of this document: research is not direct production,
so the `engineering` profile never barred it. **No profile change is needed or
recommended.** Perplexity and Firecrawl take research assignments under the
current profile. The only thing a profile change would alter is implementation
targets, and nothing in this delivery needs that.

**D-08 — Grok's contact class.** Grok's two assignments are analysis, which the
API contact serves. If the Director later needs Grok as an exact-SHA reviewer —
which happens if a single Claude session authors both A-05 and A-06 — the
digest-verified Build CLI contact and an authenticated host runner are required
first. Confirm whether the owner wants the host bridge preflight run for that
contingency, or whether the fallback should instead be to re-partition the work.

**D-09 — Copilot Lite contact.** Assigned as A-17, post-freeze and non-gating.
Confirm whether an authenticated contact exists; without one the lane fails
closed, records no verdict, and the attempt counts as waste.

**D-10 — Perplexity query approval.** A-15 needs an owner approval bound to the
digest of its exact query text before dispatch. The query is written out in §4;
approve it as written or amend it, but an amendment after approval invalidates
the approval and needs a fresh one.

**D-11 — Grok CLI contact, Wednesday.** Running the host-bridge preflight
Wednesday instead of Friday costs nothing, and converts Grok from an
analysis-only lane into one that can hold implementation and exact-SHA review
work whose quota is entirely unused. Recommended.

## 7. What this matrix does not claim

Lane membership and an allocation share do not attest that a provider route is
enrolled, authenticated or live. No packet here has been dispatched: the
Director holds no provider transport in this session, and dispatching one would
be an external action requiring the owner. Every assignment above is a prepared
work order awaiting owner launch. The profile sheets grant no authority, and a
documented route is not an authorized one.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 08:20 UTC | END]] -->
