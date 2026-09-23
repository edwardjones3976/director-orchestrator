<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | BEGIN]] -->
<!-- [[OpenAI Codex mechanical recovery | GPT-6 | 2026-09-10 | 18:21 UTC | BEGIN]] -->
<!-- [[Grok Bot | serving model unreported | 2026-09-10 | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:20 UTC | BEGIN]] -->
# Stage A: LangGraph primary as intake owner, with simulated providers

**Status:** Runbook. Not executed by merging this document, and no command in it
has been run. Every command below is contingent on which control-script version
the deployment has adopted; see "Which control version this describes".

Stage A moves new-intake authority from the legacy Director engine to the
LangGraph primary control plane, and runs the real primary graph, while every
provider call is answered by an in-process double. No money is spent, no
provider is contacted, and no acceptance gate is bypassed.

It exists because `-Action Activate` is Stage A and Stage B together: it sets
`ALLOW_PAID_RUNS=true` and
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=true` in the same switch write
and then asserts `combined_live_ready`
(`scripts/windows-langgraph-primary-control.ps1`). `PrepareOfflineHandoff`
provides the missing immutable inventory prerequisite while leaving intake
stopped and paid execution off. It does not transfer ownership or start Stage A;
the separately authorized mode switch and runtime startup below do that.

## Which control version this describes

This runbook requires the control script with `PrepareOfflineHandoff` in this
checkout. That version's `-Action` set is `PrepareAcceptance`,
`BootstrapDatabaseIdentity`, `SelectGeneration`, `StageRuntime`, `PrepareOfflineHandoff`,
`ValidateProviders`, `Challenge`, `Preflight`, `Accept`, `Migrate`, `Backup`,
`RestoreDrill`, `Health`, `Activate`, `Rollback`. Confirm the ValidateSet of the
script the deployment actually holds before running anything here; an earlier
version has neither the staged rungs nor the generation and image verification
that everything below assumes.

A correction to the rollback flag and lifecycle is pending in a separate OpenAI
implementation at head `6fb8ec8cf09e5802a7db2c3c0a193a314185c3fc` (PR 115). It
is not merged, is not present in this checkout, and was not reviewed for this
document. The rollback section below therefore describes the version in this
checkout only, and its commands stay contingent on the corrected control version
actually being the adopted one.

## The staged evidence rung is not Stage A

The control script has a second, lower rung that this runbook is not about.
`StageRuntime` starts `director_orchestrator.primary_staging:app` with
`ALLOW_PAID_RUNS`, `LANGGRAPH_PRIMARY_ENABLED`,
`LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED`,
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` and
`LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED` written false and
`LANGGRAPH_PARALLEL_KILL_SWITCH` written true, then requires health to report
`staged_ready` and `evidence_only` true with `primary_runtime_active` false.
`ValidateProviders` runs only against exactly that service.

**Stage A is not a prerequisite for `ValidateProviders`, and it is not a step
towards it.** The two rungs are incompatible by configuration: Stage A runs the
ordinary application with `LANGGRAPH_PRIMARY_ENABLED=true` and offline
simulation on, while the evidence path requires the staging ASGI app with every
execution switch false — the resolved-deployment digest rejects a staged command
that carries an enabled execution switch. Running Stage A first collects no
activation evidence and brings the evidence run no closer: its provider doubles
cannot produce a capture, and a capture is the only thing that advances
`LIVE_OK`.

## What Stage A is, and is not

| | Stage A | Live activation |
| --- | --- | --- |
| New intake owner | **langgraph** | langgraph |
| Legacy new intake | **disabled** | disabled |
| Primary graph, coordinator, checkpointer | **real** | real |
| Provider calls | **in-process doubles** | real, paid |
| `ALLOW_PAID_RUNS` | false | true |
| Provider activation evidence | not required | `combined_live_ready` required |
| Acceptance gates | none bypassed | all nine required |

**Stage A does not do real work.** `DefaultOfflineSimulationServices`
substitutes `provider_doubles` that return a digest
(`src/director_orchestrator/primary_default.py:1364`). Anything that looks like a
provider result under Stage A was produced locally. Do not read Stage A output as
evidence that a provider performed anything.

## Why this is safe to run

`config.py:1465-1470` makes offline simulation and live provider dispatch
**mutually exclusive** — the configuration refuses to start if both are set, so
Stage A cannot silently become Stage B.

`config.py:1476-1485` places every provider-activation and paid-dispatch
requirement inside `if self.langgraph_primary_live_provider_dispatch_enabled:`.
With live dispatch off, none of them apply, and none are being skipped: they are
not reached.

`primary_default.py:2538-2540` gates the authority handover on
`langgraph_primary_new_intake_enabled` alone, so Stage A reaches the same
compare-and-swap the live path uses. The ownership move is real, not simulated.

## Preconditions

Verify before touching switches; this document is not evidence of current
deployment readiness.

| Check | Where | Expected |
| --- | --- | --- |
| `configuration_valid` | `/health` `langgraph_primary` | true |
| Snapshot mounted and clean **as the container sees it** | `git -c safe.directory=/repository status --porcelain=v2` inside the container | empty |
| Snapshot at the intended authority | same, `rev-parse HEAD` and `HEAD^{tree}` | the pinned commit and tree |
| No in-flight legacy work you are unwilling to strand | legacy task inventory | your call |

The snapshot check must be run **inside the container**, not on the host. A
snapshot created on Windows can be clean to host git and entirely dirty to the
container; see `docs/` history for the line-ending defect this caused.

Stage A's mode switch remains an operator edit. First use
`PrepareOfflineHandoff` on a deployment that has reached, and is held at, the
accepted lifecycle state:

- Authenticated acceptance, migration, backup and matching restore proof recorded
  through the sanctioned actions for the selected signed-provider mode (`provider-v2` or the exact literal `provider-v3-two-party`).
  Runtime acceptance checks the bound legacy inventory before selecting offline
  doubles; a successful staging health check alone cannot satisfy it.
- The initial generation-bound profile explicitly sets `ALLOW_PAID_RUNS=false`,
  `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=false` and
  `DIRECTOR_PROVIDER_VALIDATION_ENABLED=false`. Prepare these before generation
  selection; validation mode is not an operational digest exclusion.
- Default Director (D) and any other deployment separately quarantined from
  admitting the same intake or mutating the inventoried legacy database. The
  action stops and proves only the selected Compose project's intake services;
  it does not stop sibling services or establish host-wide fencing. Maintain
  this operator-established quarantine through the handoff.
- `LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` and `LANGGRAPH_PRIMARY_PINNED_TREE_SHA`
  set to that accepted candidate, equal to the snapshot's commit and tree, and
  equal to the checkout the operator will run any later control action from.
  Blank or mismatched pins mean stop, not proceed.
- The primary plane quiescent: `ambiguous_effect_count` 0 and no active provider
  dispatch, assignment, writer claim, claim or reconciliation outstanding. This
  is the same state the fenced return path requires, and entering Stage A
  without it means entering a state you may not be able to leave.
- The operator holding the deployment, present for the whole pass, with the
  authority to stop it.

`PrepareOfflineHandoff` enforces the paid-off profile, exact candidate/image,
database/generation, acceptance, restore and immutable inventory checks. It
returns with selected-project intake stopped, without writing switches or an
`ACTIVATED` stage. The subsequent Stage A edit, cross-deployment quarantine and
quiescence checks remain operator responsibilities; preparation does not
authorize runtime startup or paid execution.

## The switch set

```
LANGGRAPH_PRIMARY_ENABLED=true
LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED=true
LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED=true
```

Left untouched, and expected to stay false:

```
LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=false
ALLOW_PAID_RUNS=false
```

**The first two must move together.** `LANGGRAPH_PRIMARY_ENABLED=true` with
`NEW_INTAKE_ENABLED=false` does not fall back to legacy — `main.py:637-641`
returns HTTP 503 on every intake. There is no per-request fallback and no
in-process A/B: `main.py:157-163` reads the flag once at import and decides for
the life of the process.

## Execution

No control action writes the Stage A switch set. `StageRuntime` and `Activate`
write their own fixed sets, and neither of them ever writes
`LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED=true`. Stage A is an operator edit
of `.env`, performed outside the script, and the generation binding will not
catch a mistake in it: operational switches are deliberately normalised out of
both the deployment-configuration digest and the resolved-Compose digest, so
generation verification neither authorises nor detects this edit.

First run `scripts/windows-langgraph-primary-control.ps1
-Action PrepareOfflineHandoff -EnvironmentFile <protected-profile>
-ComposeProject <selected-project> -HostPort <selected-port>` from the clean,
accepted candidate. It requires staged prerequisites, stops and proves intake,
captures and binds the real legacy inventory, requires readiness, and proves
intake remains stopped. Stop on any failure. An identical inventory retry is
allowed, but a changed inventory cannot replace the bound one. Leave intake
closed until the separately authorized steps below; do not use `Activate` to
prepare an offline run.

1. Capture a **protected configuration backup**: for each of the three switch
   names, whether it was present and what its prior value was, written through
   the operator's protected-payload procedure for the deployment identity, in
   the same form the staging worksheet requires. Do not write a plaintext
   timestamped copy of the whole `.env`: that file holds the primary Postgres
   password, the owner token, the callback, evidence, reconciliation and host
   bridge secrets, and copying it spreads all of them to an unprotected path.
   Nothing in the repository implements this payload today; it is an
   operator-performed procedure.
2. Write the three switches by hand. Change nothing else.
3. Recreate the orchestrator **with the compose file that matches the running
   deployment**. Check which branch the repo root is on first: a stale
   `docker-compose.yml` will drop the primary DSN and the `/repository` bind
   without saying so beyond an orphan-container warning.
4. Wait for health, then verify.

That backup restores **configuration only**. It is not a rollback of the
authority move. Once Stage A has run, `intake_owner` is a fenced row in the
primary database, and putting the three switches back does not touch it: it
leaves the authority row saying `langgraph` while the process that was serving
it is gone, which is a worse state than either end. Returning intake ownership
to legacy is a separate durable database change — the fenced inverse
compare-and-swap described under "Rollback" — with its own closed-intake, pin,
ambiguity and quiescence requirements. Restoring a file is never a substitute
for it, and neither one reverses an effect that already left the deployment.

## Verification

Nine fields must be true. The tenth, `primary_runtime_active`, must be **false**,
and expecting otherwise is the easiest way to misread Stage A as a failure.

```
primary_graph_constructed      checkpoint_schema_current    writer_lease_ready
primary_router_wired           claim_store_ready            reconciliation_worker_ready
checkpoint_backend_postgres    effect_ledger_ready          legacy_new_intake_disabled
```

`primary_runtime_active` takes
`langgraph_primary_live_provider_dispatch_enabled` as its first conjunct
(`primary_default.py:2634-2646`). Live dispatch is mutually exclusive with
offline simulation, so under Stage A this field *cannot* be true. It is
measuring the live path, not this one.

The field that measures Stage A is **`paid_off_primary_intake_active`**
(`primary_default.py:2623-2631`): the same component checks, plus
`legacy_new_intake_disabled`, plus `not allow_paid_runs`. It exists precisely to
describe primary owning intake with the money switched off. It must be **true**.

The ten-field list belongs to `-Action Activate`, which asserts it only on the
live path where dispatch is on.

Then the ownership proof:

- `langgraph_primary.new_intake_owner` is `langgraph`
- `langgraph_primary.intake_fencing_generation` has incremented from its previous
  value
- `langgraph_primary.ambiguous_effect_count` is `0`

And the switches that must **not** have moved:

- `paid_runs_enabled` false
- `langgraph_primary.live_provider_dispatch_requested` false
- `langgraph_primary.offline_simulation_requested` **true** — this is how you
  confirm providers are doubled rather than live

`legacy_new_intake_disabled` is not an independent switch. It is a
database-enforced mirror of `intake_owner = 'langgraph'`, held by a CHECK
constraint (`langgraph_postgres.py:540-542`) and a trigger
(`:1191-1195`). If it is true, the authority row genuinely moved.

## Rollback, what it requires, and what it is contingent on

`-Action Rollback` performs the inverse fenced generation change. It is **not
unconditional**, and it is **not an undo**. It requires all of the following.

- **A valid prior-image record, or none.** It reads
  `<profile>.prior-images.<generation>.<image>.json`, written once before the
  first candidate recreate. An invalid record refuses rollback **before**
  intake is stopped. A missing record does not refuse: the fenced return to
  legacy runs on the candidate image and the tool reports that no image was
  restored.
- **Closed intake, proved.** It stops `slackbot`, `n8n` and `orchestrator`, then
  re-reads each service's running-container list and throws *"Authenticated
  intake surface remains active"* if any is still up. This is a hard stop, not a
  drain, and it happens before anything else changes.
- **The same accepted pins.** Like every runtime action it verifies the snapshot
  against `LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` and
  `LANGGRAPH_PRIMARY_PINNED_TREE_SHA`, requires a clean checkout whose own
  commit and tree equal those pins, and requires an already-built
  exact-candidate image whose labels match. You cannot roll back from a
  different checkout, a dirty tree, or an image built from anything else. In
  provider-v2 acceptance mode it also re-verifies the selected generation.
- `ambiguous_effect_count == 0`, or it raises *"ambiguous effects block
  rollback"*.
- `primary_quiescence_metrics().rollback_safe`, or it raises *"active provider
  dispatches, assignments, writer claims, reconciliation, or claims block
  rollback"*.

So rollback is available while primary is quiet and has produced no ambiguous
effects, and is refused while it is mid-flight. Under Stage A, with providers
doubled, ambiguous effects should not arise — but the refusal is real and
rollback must not be planned as an always-available exit once live dispatch is
on.

The ambiguous-effect and quiescence refusals fire **after** intake has been
stopped and after the switches have been written false. A refused rollback is
therefore not a no-op: it leaves the
deployment with intake stopped, `ALLOW_PAID_RUNS`,
`LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED` and
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` false, the kill switch true,
`LANGGRAPH_PRIMARY_ENABLED` still true, and the authority row still owned by
primary. **The pending PR 115 correction does not fix that.** Per the
independent review of it, PR 115 clears the simulation switch only *after* a
successful inverse compare-and-swap; the refusal path is unchanged, so a refused
rollback still leaves the deployment stopped with primary still owning the
authority row under that version too. Adopting the corrected control script is
not a reason to enter Stage A expecting an exit. The commands in this section
stay contingent on which version is actually adopted, so re-read the rollback
body of the script held before running any of them.

The sequence it runs in this checkout: stop and prove all intake stopped, write
the switches false, wait 65 seconds for the lease to lapse, run the guarded
inverse compare-and-swap in a throwaway container, set
`LANGGRAPH_PRIMARY_ENABLED=false` and restore the live ASGI app, recreate the
orchestrator on the recorded prior orchestrator image and read its image ID
back, wait for health, create slackbot stopped on the recorded prior gateway
image and read it back, then start n8n and slackbot. If a readback or the
health wait fails after the compare-and-swap, it stops and proves Slack, n8n
and API intake closed again and reports the failure.

**What rollback does not do.** It changes who owns *new* intake and nothing
else. It never transfers ownership of tasks that already exist
(`langgraph_postgres.py:1825-1827`); work created under primary stays with
primary. It does not undo, reverse or compensate any external effect primary
already executed: a merge, a Slack message, a GitHub write, a provider
invocation and any charge it incurred all survive it untouched. It does not
clear `ambiguous_effect_count` — it refuses while that count is nonzero, so the
one situation in which an undo would be most wanted is the situation in which
rollback will not run at all. Do not describe it, or rely on it, as a safety
net.

## Distance from Stage A to live

Stage A leaves these outstanding, and none are configuration:

- **Provider activation evidence.** `combined_live_ready` requires LIVE_OK
  evidence across the bound providers on two surfaces. `-Action Activate`
  asserts it. That evidence is produced on the staged evidence-only rung
  (`StageRuntime` then `ValidateProviders`), which is a separate path from this
  one and does not run through Stage A. Each capture is a real native provider
  invocation, so it may be billable or consume subscription allowance even
  though it must complete with an empty effects list.
- **`ALLOW_PAID_RUNS`.** Coupled bidirectionally with live dispatch
  (`config.py:1476-1480`, `primary_default.py:1979-1983`); the two move as one
  unit and the check is a startup refusal.
- **Eight of the nine acceptance gates.** Only Gate 3 (distinct namespaces) is
  satisfied. The operations document states these require owner acceptance of
  evidence, and that merged code or documentation satisfies none of them.

Stage A is a rung, not a shortcut. It proves the primary plane owns intake and
runs its own lifecycle, with the money and the authorisation questions still
closed.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:20 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:45 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | END]] -->
<!-- [[Grok Bot | serving model unreported | 2026-09-10 | END]] -->
<!-- [[OpenAI Codex mechanical recovery | GPT-6 | 2026-09-10 | 18:21 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]] -->
