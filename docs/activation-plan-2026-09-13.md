<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 22:36 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 09:55 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 02:30 UTC | BEGIN]] -->
# LangGraph-primary + n8n activation plan (v4 — ASAP)

**Objective:** earliest completion. **Outer bound:** Sunday 2026-09-13 07:00 PDT — a *no later than*, not a due date.
**Target:** Wednesday 2026-09-09 evening PDT; conservative Thursday midday.
**Issued:** 2026-09-08 19:00 PDT · **Revised:** 2026-09-09 02:10 PDT
**Issued by:** Claude, acting Director-Orchestrator
**Owner:** Ed Jones (`edwardjones3976`)

v3 supersedes v2. It adopts the seven Director decision rules in
[`docs/director-decision-rules.md`](director-decision-rules.md) as governing for
this delivery, and applies them: the schedule, the gate ledger and the work
orders below are the output of those rules, not merely compatible with them.
It retains v2's five owner decisions and the deployment topology revision.

Everything here is a plan. Nothing in it is activation evidence, and no
control action in it has been run.

---

## 0. Operating rules (governing)

Adopted by owner direction on 2026-09-08 and recorded in full in
[`docs/director-decision-rules.md`](director-decision-rules.md). Every lane
applies them without asking. They remove no deterministic gate, relax no
approval and create no authority; where a rule and `AGENTS.md` or
`src/director_orchestrator/policy.py` disagree, the gate wins.

| Rule | In one line | What it already changed in this plan |
| --- | --- | --- |
| **R1** Sequence by `p_fail / cost_to_determine` | Check the most-likely-to-fail, cheapest-to-check condition first | **Gate 8 is determined Wednesday, not at the Saturday go/no-go.** Every cheaply-determined gate is resolved before any expensively-satisfied one |
| **R2** Three-valued ledger | Gates are `T`/`F`/`U`; completion is computed, never asserted | §7 is now a `T`/`F`/`U` ledger with a derived completion line and a separate no-go disjunction |
| **R3** Name the decision it flips | An optional action with no flippable decision has zero value and is not taken | Added to the stop rules; every work order states the decision its investigation serves |
| **R4** Temporal network, not critical path | Min lags *and* max lags; feasible iff no negative cycle | New **WO-14** runs the check before Thursday. `ValidateProviders` is deliberately scheduled late, not early |
| **R5** Operator-minutes is the binding resource | `Σ attended durations ≤ Σ window minutes`, capacity one | WO-14 sums the attended estimates against ~14.5 h of windows. Over budget means scope moves, never "add an agent" |
| **R6** Evidence carries a binding key | Reuse on key match; exact-candidate verdicts stay commit-keyed | WO-02, WO-03 and WO-05 record the files and digests each finding depends on, so a candidate change re-runs only what moved |
| **R7** Integer allocation quotas | Largest-remainder apportionment over the rolling window | Routing uses integer quotas and integer band bounds; admission and concurrency rules unchanged |

**The two-cost distinction that drives R1.** The cost of *determining* whether a
gate is satisfied is usually far below the cost of *satisfying* it. Gate 8 is
the clearest case: the Long Table canary bundle either exists or it does not,
and looking takes minutes, while producing it takes days. Determining it on
Wednesday converts the largest known risk from a Saturday-noon surprise with
eighteen hours left into a Wednesday decision with ninety.

**What these rules do not do.** They do not reduce the thirty evidence rows, the
nine gates, or the attended minutes those require. They change the order in
which unknowns are resolved, the amount of work repeated unnecessarily, and
whether an unknown can be mistaken for a pass. No percentage reduction is
claimed.

## 1. What changed in v2, and why

The Director read the deployment's snapshot checkout and environment file
directly. Three facts reshape the plan.

### 1.1 The running deployment is eleven merges behind the candidate

| Field | Value | Source |
| --- | --- | --- |
| Snapshot path | `C:/Users/edwar/director-orchestrator-primary-snapshot` | `REPOSITORY_SNAPSHOT_HOST_PATH` |
| Snapshot HEAD | `a05237776bf4aac0f8224947cf44dea356ed8f56` (Merge PR #109, 2026-09-06 21:23 PDT) | `git log` in the snapshot |
| Snapshot tree | `4e1d578ccbd2db1e92b81de1659884061a87d160` | `git rev-parse HEAD^{tree}` |
| Working tree | clean, as this reader sees it | `git status --porcelain` |
| GitHub `main` | `c81e388ff0a9bc97a59df007589ef4c7810ba429` (Merge PR #120) | `FETCH_HEAD`, fetched 2026-09-09 00:52 |

The deployment therefore predates PRs #110, #111, #112, #113, #114, #102, #79,
#116, #117, #118, #115 and #120 — including the paid-gate binding (#116), the
rollback and launcher correction (#115), the Stage A runbook (#113) and the
provider evidence-run spec (#114).

The snapshot's `git status` was read from outside the container. The
authoritative cleanliness check is still the one run **inside** the container
(`git -c safe.directory=/repository status --porcelain=v2`), and it remains a
precondition of every runtime action.

### 1.2 The deployed control script is the version the Stage A runbook targets

The snapshot's `scripts/windows-langgraph-primary-control.ps1` exposes exactly
`PrepareAcceptance, BootstrapDatabaseIdentity, SelectGeneration, StageRuntime,
ValidateProviders, Challenge, Preflight, Accept, Migrate, Backup, RestoreDrill,
Health, Activate, Rollback`, and its latest contribution record is
`OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC` — the version
`docs/langgraph-primary-stage-a-runbook.md` says it was written against. The
runbook's commands match the deployed script.

The corollary is that **PR #115's rollback correction is not in the deployed
script.** The deployment holds the uncorrected rollback lifecycle the runbook
warns about.

### 1.3 The Stage A switch set is already written into the deployment

`.env` in the deployment checkout currently reads:

| Switch | Value |
| --- | --- |
| `LANGGRAPH_PRIMARY_ENABLED` | `true` |
| `LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED` | `true` |
| `LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED` | `true` |
| `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` | `false` |
| `ALLOW_PAID_RUNS` | `false` |
| `LANGGRAPH_PARALLEL_KILL_SWITCH` | `true` |
| `LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` | empty |
| `LANGGRAPH_PRIMARY_PINNED_TREE_SHA` | empty |
| `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON` | `{}` |
| `TRUSTED_PROVIDER_COST_RANKS` | set |

Those first three are the Stage A switch set. A file named
`.env.backup-stageA-20260907T062414Z` holds the same three switches at `false`,
so a Stage A edit was performed against **Default Director** on 2026-09-07 and
the switches are still in place.

Two things follow. The pins are empty, and the Stage A runbook says blank pins
mean stop, not proceed. And the activation bindings are `{}`, so
`combined_live_ready` cannot currently be true on any profile.

**What this does not tell us.** An environment file records configuration
intent, not runtime state. Whether the orchestrator was ever recreated with
those switches, and whether the durable `intake_owner` row actually moved to
`langgraph`, is unknown to the Director and is not inferable from the file.
That single unknown is what WO-01 now exists to close.

### 1.4 Consequence: build the candidate deployment beside the current one

Repairing Default Director in place would mean returning a possibly-moved
authority row with an uncorrected `Rollback`, whose refusal path leaves intake
stopped and primary still owning the row. Under a Sunday deadline that is the
wrong risk to take, and the deployment has to be rebuilt onto the frozen
candidate regardless.

The plan therefore builds two **new** deployments from the frozen candidate and
leaves the existing one quarantined.

## 2. Owner decisions (delegated to the Director, 2026-09-08)

The owner delegated all five open decisions. They are recorded here as the plan
of record; the owner may overturn any of them by saying so.

**D-01 — Owner windows.** Adopted as proposed: Wednesday a short slot for
WO-01 (about ten minutes), Thursday 19:00–21:30, Friday 18:00–20:00, Saturday
09:00–17:00, Sunday 05:00–07:00, all PDT. Each control-action block needs the
operator present for its whole pass.

**D-02 — Plan in the repository.** Authorized. The Director has prepared the
commit and the local branch `claude/activation-plan-2026-09-13` on top of
`c81e388`. It is **not pushed**: the session's shell has no GitHub credential,
no `gh`, and no credential helper, so the push and the pull request are one
owner command plus one click. Nothing was merged and nothing on a protected
branch was touched.

**D-03 — Topology.** Three profiles, not two. Full definition in §3.

**D-04 — Canary task.** Defined in §6, WO-11.

**D-05 — Status channel.** The status workflow posts to the existing
`SLACK_DIRECTOR_CHANNEL_ID`; no new channel is created. Status messages are
prefixed `STATUS —`, are emitted only on a readiness change, and never carry
secret, DSN, or allowlist values. The simulation profile does not post to Slack at all.

## 3. Profiles — reduced to one

**Under the ASAP objective, profile L is not built.** It existed only for the
evidence rung and `Activate`, both of which have left the delivery path. Profile
**S** is the only new deployment, and the existing Default Director deployment
stays quarantined exactly as before. The three-profile table below is retained
because the follow-on will need L, and because the hard rule about a single
intake surface still governs.

| | **S — simulation** | **L — candidate live** | **D — existing deployment** |
| --- | --- | --- | --- |
| Purpose | Stage A, provider doubles | evidence rung, then `Activate` | quarantined |
| Source | frozen candidate | frozen candidate | `a052377` (stale) |
| Snapshot dir | `C:/Users/edwar/director-orchestrator-sim-snapshot` | `C:/Users/edwar/director-orchestrator-candidate-snapshot` | existing |
| Compose project | `dirsim` | `dirlive` | existing default |
| Env file | `.env.simulation` | `.env.candidate` | `.env` |
| Host port | `8091` | `8092` | existing |
| Checkpoint namespace | `sim-director-primary-v1` | `candidate-director-primary-v1` | `default-director-primary-v1` |
| Paid runs | `false` throughout | `true` only from `Activate` | `false` |
| Slack | none | existing Director channel | stopped during the window |
| n8n | direct HTTP tests, no Slack | production webhook, repointed to L | stopped during the window |

Names, ports and paths are the Director's choice and are non-secret; the owner
may rename any of them. Ports must not collide with the running deployment —
confirm during WO-01.

**Hard rule — one intake surface at a time.** Slack and n8n are single
instances. Before L is recreated with new intake enabled, D's `slackbot` and
`n8n` containers must be stopped, and the n8n webhook repointed at L. Two
deployments accepting the same Slack events is the dual-writer no-go, whatever
their databases say. S never touches Slack, so it can run alongside either.

**Quarantine of D.** No new work is dispatched to D. Whether D is rolled back,
left as-is, or retired is a separate decision after Sunday, recorded in the
close-out report either way. If WO-01 shows D's authority row already moved to
`langgraph`, that fact is recorded and D's Slack and n8n stay stopped for the
rest of the window; no rollback is attempted under deadline with the
uncorrected script.

## 4. Definition of done

1. **Accepted candidate.** One exact SHA on `main` with trusted CI green, a
   Codex Security ACCEPT and an independent different-provider ACCEPT, both as
   canonical `director-activation-review/v1` artifacts naming that head; the
   lifecycle recorded on **S**, the only profile now built.
2. **Simulated phase passed.** Stage A on S: nine readiness fields true,
   `paid_off_primary_intake_active` true, `new_intake_owner = langgraph`,
   fencing generation incremented, `ambiguous_effect_count` 0,
   `offline_simulation_requested` true, `paid_runs_enabled` false; n8n intake
   exercised against S with doubles; evidence bundle captured.
3. ~~**Live phase executed.**~~ **Moved to the follow-on.** Gate 8 is false and
   the earliest-completion objective makes the live path strictly slower. The
   report records the gate as unmet with its evidence and the dependency chain
   that keeps it unmet, and states plainly that no live provider execution
   occurred.
4. **n8n delivered.** Intake hardened and the status workflow deployed and
   documented. Usage-summary and evidence-export only if the Friday scope cut
   passes.
5. **Documents delivered.** `docs/close-out-report-2026-09-13.md` and
   `docs/director-user-guide.md` in a reviewed pull request, plus PDF copies,
   before 07:00 Sunday.

**Floor if a gate blocks activation:** items 1, 2, 4 (must-set) and 5 still
ship, and the report names the exact gate, the evidence, and the next action.
The report will never present a leadership handoff, green CI, a staged adapter,
a simulation, or merged code as proof of live provider execution.

## 5. Schedule — earliest completion (PDT)

The owner reduced the objective from "fits before Sunday" to **as soon as
possible**, with Sunday 07:00 as an outer bound rather than a target. Combined
with Gate 8 reading `F`, that resolves **D-12 to option A by arithmetic**:
option B requires `Activate`, which requires the evidence rung, which adds about
three attended hours and starts a twenty-four-hour `LIVE_OK` clock. Under an
earliest-completion objective option B is strictly slower as well as not
permitted. The floor deliverable is now the plan of record; the live phase
becomes a follow-on with its own schedule.

### What the compression rests on

`docs/langgraph-primary-stage-a-runbook.md` states it directly. For Stage A,
provider activation evidence is **not required** and no acceptance gate is
bypassed; the nine activation gates and `combined_live_ready` belong to live
activation. "Distance from Stage A to live" then lists what Stage A leaves
outstanding: provider activation evidence, `ALLOW_PAID_RUNS`, and eight of the
nine acceptance gates. Stage A needs the **acceptance lifecycle**, not the
activation gates.

**Gate 8 being false therefore does not block the floor deliverable at all.**

### What leaves the critical path

| Dropped | Why | Attended saved |
| --- | --- | ---: |
| Profile **L** entirely | It exists only for the evidence rung and `Activate`, both dropped. Profile **S** becomes the only new deployment | −4.00 h |
| WO-10 prep, `StageRuntime`, `ValidateProviders` | Provider activation evidence is not required by Stage A | −3.00 h |
| WO-11 `Activate`, canary, keep/rollback | Gate 8 false; not permitted | −2.50 h |
| WO-05 fix PRs | The five blockers sit in the `LIVE_OK` and live-dispatch path, which is no longer traversed. They move to the follow-on | off critical path |
| WO-12 n8n stretch | Already cuttable | optional |

### The remaining critical path

| Hours | Lane | Step |
| ---: | --- | --- |
| 0.10 | owner | Push the branch; freeze `c81e388` as the candidate |
| 1.50 | Codex | **Narrowed WO-02**: does any of the five blockers touch `Accept`, `Migrate`, or the Stage A path? Not the full live-path audit |
| 2.50 | Cursor + CI | CI green on `c81e388`, Codex Security ACCEPT, independent ACCEPT artifact — canonical `director-activation-review/v1` JSON on the exact head |
| 0.75 | owner | Snapshot at the candidate, build with the candidate commit and tree args, `BootstrapDatabaseIdentity`, `SelectGeneration` |
| 1.25 | owner | `PrepareAcceptance` → `Accept` → `Migrate` → `Backup` → `RestoreDrill` → `Health` |
| 1.00 | owner | Stage A switch set, recreate, verify the nine fields, `paid_off_primary_intake_active`, ownership proof |
| 0.50 | owner | n8n intake and status exercised against S with provider doubles |
| 2.00 | Director | Close-out report, user guide, PDFs |
| 1.00 | Cursor | Review of the documents |

**Serial path 10.60 h, of which owner-attended is 3.60 h** — all of it fits
inside a single Wednesday window with over four hours to spare. WO-06's n8n
build and the document drafts run in parallel with the review cycle and are not
additive.

**The binding constraint is no longer owner minutes.** It is review latency: the
two ACCEPT artifacts on the exact head, which gate `Accept`. Everything else has
slack.

### Wednesday, as early as the owner is available

| When | What | Lane |
| --- | --- | --- |
| first thing | Push branch; freeze `c81e388`; launch narrowed WO-02 and WO-03; request Codex Security and independent review on that exact head; WO-01 health read (10 min) | Owner |
| +3 h | Reviews and CI land; ACCEPT artifacts in hand | Cursor, CI |
| then, 0.75 h | Snapshot, build, bootstrap, select generation on **S** | Owner + Director |
| then, 1.25 h | Acceptance lifecycle through `RestoreDrill` and `Health` | Owner + Director |
| then, 1.00 h | Stage A pass and verification | Owner + Director |
| then, 0.50 h | n8n exercised against S | Owner + Director |
| evening | Report and guide drafted, reviewed, PDFs delivered | Director, Cursor |

**Target completion: Wednesday evening. Conservative: Thursday midday.**
Roughly three and a half to four days earlier than the previous plan.

### The follow-on, scheduled separately

The live phase is not cancelled, only decoupled: the five evidence blockers
(WO-05), the provider activation evidence campaign, Grok's CLI contact,
Antigravity team mode, Firecrawl's repository credential, and the Long Table
canary that Gate 8 actually requires. None of it is on the delivery path now,
and each can proceed at its own pace with the gate honestly recorded as unmet.

## 6. Work orders

Each work order is the task boundary for its lane; do not expand permissions,
repositories, data sources or external actions beyond it. Every submission
carries contribution tags per `docs/contribution-attribution-policy.md` (JSON
workflows are attributed through the pull request, never inline), verified with
`PYTHONPATH=src python scripts/check_contribution_tags.py --base <base> --head <head>`.

**WO-01 — the one remaining runtime question (Owner, ~10 min, Wednesday).**
Everything else in the original WO-01 is answered in §1. What remains is
whether the running deployment actually took the Stage A configuration. In the
deployment's PowerShell, with the exact env file, Compose project and host
port, run `docker compose --env-file .env ps`, then the control script's
`-Action Health`, then `GET /health`. Report, redacted: whether the
orchestrator is up; `paid_runs_enabled`; `langgraph_primary.new_intake_owner`;
`intake_fencing_generation`; `ambiguous_effect_count`;
`paid_off_primary_intake_active`; `legacy_new_intake_disabled`; and the host
port in use, so §3's 8091/8092 are confirmed free. Acceptance: the Director can
state whether D owns intake as `langgraph`.

**WO-02 — evidence blocker audit against the freeze candidate (Codex, Wed 14:00).**
For each of the five blockers in `docs/provider-evidence-run-spec.md`: status
on `c81e388` (open, closed by PR number, or partial), file and line evidence,
the minimal bounded fix if open, and the test that proves it. Also answer with
file and line: does `Activate` tolerate `OFFLINE_SIMULATION_ENABLED=true` at
start, or must the operator clear it first; does `Activate` tolerate a
pre-existing `intake_owner = langgraph`; is `Rollback` available from the Stage
A state, both in the deployed `2026-09-06 20:37 UTC` script and after PR #115.
No code changes. Unknowns are labelled unknown. Under R6, record for each
finding the files and digests it depends on, so a candidate change re-runs only
the findings whose inputs moved; under R3, any further investigation this audit
proposes must name the decision it would flip.

**WO-03 — open pull request triage (Cursor, Wed 14:00).** Every open PR:
number, head SHA, author lane, touches activation-path files, CI state,
reviews, and a recommendation of merge-before-freeze, hold, or close. Flag any
PR whose merge would move `main` after the freeze. Recommendations only. Under
R6, record the head SHA each recommendation was computed against so it can be
re-validated rather than redone.

**WO-14 — schedule feasibility (Director, Wed 16:00).** Two checks, both before
any window is committed. First (R4): build the constraint graph — precedence
edges as minimum lags, `t(Activate) − t(finish ValidateProviders) ≤ 24 h` as a
maximum lag, owner windows as release times, attended tasks non-overlapping at
capacity one — and test for a negative cycle. Report either that the plan is
consistent, or the exact constraint pair that cannot both hold, plus the latest
feasible start for `ValidateProviders`. Second (R5): sum the attended duration
estimates for WO-08 through WO-11 against the available window minutes. If the
sum exceeds the budget, propose what leaves the critical set, in the order R1
gives. Deliverable: one page appended to the staging worksheet, and a plan
revision if either check fails.

**WO-15 — cheap-gate sweep (Director + Owner, Wed 11:00).** Apply R1 to the nine
activation gates. For each, record the cost of *determining* it separately from
the cost of *satisfying* it, then determine every cheap one now and record
`T`/`F`/`U` with its evidence reference. Gate 8 is first: locate the Native Long
Table canary evidence bundle, establish whether it is complete against the
accepted source baseline `4d26680f` / tree `1e0db56f`, and record the result.
Gates 3 and 5 are also cheaply determined from the worksheet and task inventory.
The output is the initial state of the §7 ledger and, if Gate 8 reads `F` or
`U`, a Wednesday recommendation on the live scope rather than a Saturday one.

**WO-04 — worksheet, manifest, gate ledger (Director, Wed).** Non-secret
portions of the staging worksheet for S and L, the generation manifest fields,
the nine-gate ledger with evidence slots, and the evidence-bundle template the
close-out report consumes. Names and readiness booleans only; values stay in
the owner's secret store.

**WO-05 — bounded fix PRs (Codex, open Wed 21:00, merged Thu 12:00).** One PR
per blocker, one writer per file, tests added, exact commands and results in
the PR body, Postgres-backed suites run through
`scripts/windows-postgres-test-lane.ps1` or reported as a named coverage gap.
No deterministic gate removed or relaxed. Reviewed by Cursor or Grok at the
exact head; Codex Security runs; the owner merges. Codex never merges its own
pull request.

**WO-06 — n8n v2 (Codex, PR open Thu 18:00).** Intake: bearer secret through an
n8n credential and never inline; the Slack intake receipt fields required since
PR #118 forwarded intact; a stable idempotency key from the Slack event ID;
non-2xx from `/v1/intake` mapped to an explicit webhook error response; no raw
provider output echoed. Status (`n8n/workflows/director-status.json`, new): a
schedule trigger polling `GET /health` on S and L, and `/v1/staging/status`
while a staging app is running, diffing readiness booleans against the previous
run and posting to `SLACK_DIRECTOR_CHANNEL_ID` only on change. It speaks anyway — regardless of change — on three conditions: the health endpoint is unreachable, `ambiguous_effect_count` is above zero, or the parallel dispatch kill switch is engaged while parallel dispatch is enabled. Both validated with `python -m json.tool`. Documented in
`docs/n8n-operations.md`, including how the webhook is repointed between
profiles. Cursor reviews.

**WO-07 — independent review (Cursor; Grok second where available).**
Exact-head review of WO-05 and WO-06. For the activation candidate, an
`APPROVED` GitHub review whose body is exactly the canonical
`director-activation-review/v1` JSON artifact naming the frozen head. Prose does
not qualify. The reviewer must not be a material author.

**WO-08 — candidate deployments (Owner + Director, Thu 19:00–21:30).** Create
both snapshots at the frozen candidate with
`scripts/windows-primary-snapshot.ps1` (it creates only when the target does not
exist; the existing primary snapshot is at `a052377` and must not be reused for
the candidate). Then per `docs/primary-staging-controls.md`, for L and then S:
`docker compose build orchestrator` with `DIRECTOR_CANDIDATE_COMMIT` and
`DIRECTOR_CANDIDATE_TREE` build args — building is not acceptance — start the
primary Postgres service, `BootstrapDatabaseIdentity`, `SelectGeneration`,
`PrepareAcceptance`, issuer attestations, `Preflight`,
`Accept -AcceptanceArtifactPath`, `Migrate`, `Backup`, `RestoreDrill`, `Health`.
Set each profile's pins to the frozen candidate's commit and tree; blank pins
are a stop. Record every command, exit state and redacted health output. Any
refusal is `OWNER_DECISION_REQUIRED`, not a retry.

**WO-09 — Stage A on S (Owner + Director, Fri 18:00–20:00).** Follow
`docs/langgraph-primary-stage-a-runbook.md` exactly, including the preconditions
table and a protected configuration backup of the three switch names — not a
plaintext copy of the environment file. Write the three switches, recreate with
the matching compose file, then verify the nine fields,
`paid_off_primary_intake_active` true, the ownership proof, and the switches
that must not have moved. Send one intake through n8n to S and confirm a task
record and a doubled provider result labelled
`SIMULATION — NO PROVIDER INTERACTION`. Hold S in Stage A for the report.

**WO-10 — provider activation evidence on L (Owner + Director, Sat 09:00).**
Prerequisites from Friday: `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON`
for openai plus two of claude, cursor, grok as identity and version tuples only
— it is `{}` today; `TRUSTED_PROVIDER_COST_RANKS`, already set; the generation
manifest; a clean host-bridge `Preflight`; and a campaign reservation under
USD 10. Run `StageRuntime`, expecting `staged_ready` and `evidence_only` true
with `primary_runtime_active` false, then `ValidateProviders`. Record rows per
provider, surface and stage, spend per invocation, and the `LIVE_OK` timestamps.
Never repeat an ambiguous capture. Stop at the first refusal.

**WO-11 — activate and canary on L (Owner + Director, Sat 13:00).** Only after
the ledger shows nine of nine. Stop D's `slackbot` and `n8n`, repoint the n8n
webhook at L, then `Activate`, which writes `ALLOW_PAID_RUNS=true` and live
dispatch together and asserts `combined_live_ready`.

> **Canary task (D-04).** One owner-posted message in the Director channel:
> `DIRECTOR Produce a one-paragraph plain-language summary of the "What Stage A is, and is not" section of docs/langgraph-primary-stage-a-runbook.md, for the operator user guide. Post the paragraph back to this thread.`
> Bounds: one assignment, one provider selected by the cost ranks, at most one
> revision, no repository or GitHub effect of any kind, and the Slack completion
> post as the only external effect. Ceiling USD 14; expected well under USD 1.
> Success: terminal state reached, provider run ID and cost recorded, zero
> duplicate effects, `ambiguous_effect_count` 0. The paragraph it returns is
> real content for WO-13's user guide, so a successful canary is not throwaway
> work.

At 16:00 the owner decides keep-live or `Rollback`. A refused rollback is
recorded as-is and never forced.

**WO-12 — n8n stretch (Codex; cuttable Friday 18:00).** A usage-summary
workflow calling `POST /v1/usage-checks/run` no more often than the configured
interval and posting a Slack summary, and an evidence-export workflow
collecting `/health`, `/v1/staging/status` and the gate-ledger JSON into a
timestamped folder, never including secrets, DSNs or allowlist values.

**WO-13 — close-out report and user guide (Director; Cursor review; Owner merge).**
`docs/close-out-report-2026-09-13.md` in the milestone-record shape: project and
assignment identifiers; starting and final SHAs and tree digests; categorized
status per phase; authenticated participation per lane; workload deviation;
tests, CI, security and review; risks; cost, tokens and time; scope or authority
changes; recovery and rollback; next gate; owner decisions; and explicit
not-done and unverified items. `docs/director-user-guide.md` covers daily
operation: posting a task, approving, reading health and the status posts, the
n8n workflows, backups, the kill switch, rollback, and where each piece of
evidence lives. Both exported to PDF.

## 7. Activation gate ledger

Values are `T`, `F` or `U` under R2. `U` is a real value and is never rounded
to `T`. "Determine" and "satisfy" are separate columns under R1: the ledger is
ordered by determination cost so the cheap answers arrive first.

| # | Gate | Evidence slot | Cost to determine | Determined | Value |
| --- | --- | --- | --- | --- | --- |
| 8 | Long Table canary evidence bundle complete | **determined 2026-09-09** — canary registered, not run: `langgraph-canary.yaml` declares `registration_only: true`, runtime `disabled`, all effects false; no run evidence in either tree. See `findings-2026-09-09-gate8-and-holds.md` | minutes | **done — Tue night** | **`F`** |
| 3 | Distinct deployment, graph, checkpoint and effect-ledger namespaces | worksheet | minutes | **Wed (WO-15)** | `U` |
| 5 | Pre-cutover tasks bound or explicitly migrated | task inventory | minutes | **Wed (WO-15)** | `U` |
| 4 | Legacy intake disabled before the writer lease is acquired | `legacy_new_intake_disabled` | one health read | Wed (WO-01) for D; WO-08 for L | `U` |
| 7 | Kill switch blocks new graph tasks and reconciles in-flight effects | drill note | one drill | Thu (WO-08) | `U` |
| 2 | Postgres checkpointer live; backup and restore drill recorded | `Backup` / `RestoreDrill` output | one lifecycle pass | Thu (WO-08) | `U` |
| 1 | Independent review, trusted CI and security on the exact candidate SHA | PR number, head, artifact digests | a review cycle | Thu (WO-05, WO-07) | `U` |
| 6 | `/health` shows checkpoint, writer and reconciliation readiness | health JSON | after WO-08 | Thu–Fri | `U` |
| 9 | Owner acceptance with exact commits, profile IDs, store digests, run IDs, costs, rollback drill | acceptance record | after 1–8 | Sat (WO-11) | `U` |

```
Done      = AND over gates 1..9            F   (gate 8 determined false 2026-09-09)
Permitted = Done AND NOT (OR over no-go)   F   — not a pass, and not an unknown
```

Gate 8 is false, so the conjunction is false regardless of the other eight.
`Activate` is not permitted under the gates as written. The owner's scope
decision is **D-12** in `findings-2026-09-09-gate8-and-holds.md`; the Director
recommends the floor deliverable.

No status message, report or handoff states that this delivery is complete. Each
states the computed value and names the gates still `U`.

Gate 8 remains the most likely no-go: the Native Long Table canary is a separate
lane with its own worktree and branch. If it cannot be completed by Saturday
noon, the Director recommends recording it unmet and not activating; the floor
deliverable then applies. The owner may decide otherwise, and that decision is
recorded verbatim.

## 8. Stop rules

- **R2.** A gate that is `U` is not a pass. Completion is computed from the
  ledger and never asserted; a triggered no-go is never overridden by a
  satisfied conjunction.
- **R3.** Before any optional investigation, status check, review pass or
  provider call, name the decision it could flip. If no outcome changes any
  action, do not take it. Required security, CI, approval and exact-candidate
  checks are mandatory and outside this rule.
- Deployment, publishing, merge, deletion, purchases, credential or access
  changes stop in `OWNER_DECISION_REQUIRED`.
- No lane accepts or merges its own material work; the Director does not accept
  code it authored.
- Every spend is reserved against the USD 10 campaign, USD 50 daily and USD 14
  task ceilings before it happens. Missing cost telemetry is unknown, never zero.
- Ambiguous provider or effect outcomes go to reconciliation, never a blind retry.
- No plaintext environment-file copies; protected-payload backups only.
- Snapshot cleanliness is checked inside the container.
- `LIVE_OK` older than twenty-four hours is stale: redo the chain.
- Rollback is not a safety net. It refuses on ambiguous effects or
  non-quiescence, it never undoes an external effect, and the deployed script
  does not yet carry PR #115's correction.
- One intake surface at a time: never two deployments on the same Slack channel
  and n8n instance.

## 9. Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Evidence blockers still open and non-trivial | High | WO-02 by Wednesday 14:00; freeze decision at 17:00; floor deliverable defined |
| Gate 8 unmet | High | Recorded unmet; owner decision; the report is honest either way |
| D already owns intake as `langgraph` | Medium–High | D quarantined; no rollback attempted under deadline with the uncorrected script |
| Building two new deployments overruns Thursday's window | Medium | L first; S may slip to Friday morning without moving the Saturday gate |
| Codex or Cursor quota or outage | Medium | Grok as second reviewer; Claude Code sessions as fallback implementer with Cursor review |
| Port or Compose-project collision with D | Low | Confirmed in WO-01 before WO-08 |
| `LIVE_OK` lapses before `Activate` | Low | `ValidateProviders` Saturday 09:00 for a 13:00 `Activate` |
| Canary produces an ambiguous effect | Low | Single Slack-only effect; keep-live is an acceptable outcome |

*Correction note, 2026-09-21: The historical "Grok as second reviewer" wording is preserved above. Current exact-head acceptance policy requires a single independently assigned non-author reviewer. In this historical plan, "second reviewer" meant an alternate or fallback eligible reviewer in case Cursor was unavailable, not an additive second required opinion.*

## 10. Open hygiene item

The deployment checkout holds twelve plaintext environment-file copies
(`.env`, `.env.bak-*`, `.env.backup-*`, and per-project variants). All are
covered by `.gitignore`, so there is no commit risk, but each is a full copy of
a file carrying the primary Postgres password and the owner, callback, evidence,
reconciliation and host-bridge secrets. The Stage A runbook warns against
exactly this. Recommend consolidating to one protected payload after Sunday;
not urgent enough to spend deadline time on now.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 02:30 UTC | END]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 09:55 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 22:36 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | END]] -->
