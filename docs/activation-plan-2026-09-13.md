<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 02:30 UTC | BEGIN]] -->
# LangGraph-primary + n8n activation plan (v2)

**Delivery deadline:** Sunday 2026-09-13 07:00 PDT (14:00 UTC)
**Issued:** 2026-09-08 19:00 PDT · **Revised:** 2026-09-08 19:30 PDT
**Issued by:** Claude, acting Director-Orchestrator
**Owner:** Ed Jones (`edwardjones3976`)

v2 supersedes v1. It records five owner decisions the owner delegated to the
Director on 2026-09-08, and revises the deployment topology after reading the
actual state of the Default Director deployment.

Everything here is a plan. Nothing in it is activation evidence, and no
control action in it has been run.

---

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

## 3. Three profiles

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
   lifecycle recorded on L.
2. **Simulated phase passed.** Stage A on S: nine readiness fields true,
   `paid_off_primary_intake_active` true, `new_intake_owner = langgraph`,
   fencing generation incremented, `ambiguous_effect_count` 0,
   `offline_simulation_requested` true, `paid_runs_enabled` false; n8n intake
   exercised against S with doubles; evidence bundle captured.
3. **Live phase executed.** `StageRuntime` then `ValidateProviders` on L
   producing at least thirty current `LIVE_OK`-chain rows under the USD 10
   campaign ceiling; the nine gates reviewed; `Activate`; one bounded canary
   task under USD 14 with real provider dispatch; zero duplicate provider,
   Slack or GitHub effects; keep-live or rollback decision recorded.
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

## 5. Timeline (PDT)

| When | What | Lane |
| --- | --- | --- |
| Wed 09-09, 10 min | **WO-01** the one remaining runtime question on D | Owner |
| Wed 14:00 | **WO-02** blocker audit · **WO-03** PR triage returned | Codex · Cursor |
| Wed 17:00 | **Candidate freeze** — `c81e388` or `main` plus fix PRs | Director → Owner |
| Wed 21:00 | **WO-05** fix PRs open with tests and tags | Codex |
| Thu 12:00 | WO-05 reviewed, CI and security green, owner merges, SHA frozen | Cursor · Owner |
| Thu 18:00 | **WO-06** n8n intake hardening and status workflow PR open | Codex |
| Thu 19:00–21:30 | **WO-08** create both snapshots at the frozen candidate; build; bootstrap; select generation; `PrepareAcceptance` → `Accept` → `Migrate` → `Backup` → `RestoreDrill` on L, then on S | Owner + Director |
| Fri 10:00 | **WO-10 prep** on L: activation bindings, cost ranks, generation manifest, host-bridge `Preflight`, campaign reservation | Owner + Director |
| Fri 18:00–20:00 | **WO-09** Stage A on S; n8n exercised with doubles; evidence bundle. **Scope cut** on WO-12 | Owner + Director |
| Sat 09:00 | **WO-10** `StageRuntime` → `ValidateProviders` on L | Owner + Director |
| Sat 12:00 | **GO / NO-GO** against the nine gates | Owner + Director |
| Sat 13:00 | **WO-11** stop D's Slack and n8n, repoint n8n to L, `Activate`, canary | Owner + Director |
| Sat 16:00 | Keep-live or rollback decision | Owner |
| Sat 18:00 → Sun 05:00 | **WO-13** report and guide, review, corrections, PDFs | Director · Cursor |
| Sun 05:30 | Merge or leave open with reviews; deliver PDFs | Owner + Director |
| **Sun 07:00** | **Deadline** | |

`LIVE_OK` expires after 86,400 seconds with no renewal path, so
`ValidateProviders` runs Saturday morning for a Saturday afternoon `Activate`.
Buffer: roughly five hours Saturday night, plus the Friday scope cut.

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
No code changes. Unknowns are labelled unknown.

**WO-03 — open pull request triage (Cursor, Wed 14:00).** Every open PR:
number, head SHA, author lane, touches activation-path files, CI state,
reviews, and a recommendation of merge-before-freeze, hold, or close. Flag any
PR whose merge would move `main` after the freeze. Recommendations only.

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
run and posting to `SLACK_DIRECTOR_CHANNEL_ID` only on change, and immediately
when `ambiguous_effect_count` exceeds zero, the kill switch is set, or health is
unreachable. Both validated with `python -m json.tool`. Documented in
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

| # | Gate | Evidence slot | Accepted |
| --- | --- | --- | --- |
| 1 | Independent review, trusted CI and security on the exact candidate SHA | PR number, head, artifact digests | ☐ |
| 2 | Postgres checkpointer live; backup and restore drill recorded | `Backup` / `RestoreDrill` output | ☐ |
| 3 | Distinct deployment, graph, checkpoint and effect-ledger namespaces | worksheet | ☐ |
| 4 | Legacy intake disabled before the writer lease is acquired | `legacy_new_intake_disabled` | ☐ |
| 5 | Pre-cutover tasks bound or explicitly migrated | task inventory | ☐ |
| 6 | `/health` shows checkpoint, writer and reconciliation readiness | health JSON | ☐ |
| 7 | Kill switch blocks new graph tasks and reconciles in-flight effects | drill note | ☐ |
| 8 | Long Table canary evidence bundle complete | bundle reference, or recorded as not satisfied | ☐ |
| 9 | Owner acceptance with exact commits, profile IDs, store digests, run IDs, costs, rollback drill | acceptance record | ☐ |

Gate 8 remains the most likely no-go: the Native Long Table canary is a separate
lane with its own worktree and branch. If it cannot be completed by Saturday
noon, the Director recommends recording it unmet and not activating; the floor
deliverable then applies. The owner may decide otherwise, and that decision is
recorded verbatim.

## 8. Stop rules

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

## 10. Open hygiene item

The deployment checkout holds twelve plaintext environment-file copies
(`.env`, `.env.bak-*`, `.env.backup-*`, and per-project variants). All are
covered by `.gitignore`, so there is no commit risk, but each is a full copy of
a file carrying the primary Postgres password and the owner, callback, evidence,
reconciliation and host-bridge secrets. The Stage A runbook warns against
exactly this. Recommend consolidating to one protected payload after Sunday;
not urgent enough to spend deadline time on now.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 02:30 UTC | END]] -->
