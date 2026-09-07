<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:20 UTC | BEGIN]] -->
# Stage A: LangGraph primary as intake owner, with simulated providers

**Status:** Runbook. Not executed by merging this document.

Stage A moves new-intake authority from the legacy Director engine to the
LangGraph primary control plane, and runs the real primary graph, while every
provider call is answered by an in-process double. No money is spent, no
provider is contacted, and no acceptance gate is bypassed.

It exists because the sanctioned `-Action Activate` path is Stage A and Stage B
together: it sets `ALLOW_PAID_RUNS=true` and
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=true` in the same switch write
and then asserts `combined_live_ready`
(`scripts/windows-langgraph-primary-control.ps1`). There is no sanctioned way to
get primary ownership without also authorising paid dispatch. Offline simulation
is the rung between them.

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

Verify before touching switches. All were true at the time of writing.

| Check | Where | Expected |
| --- | --- | --- |
| `configuration_valid` | `/health` `langgraph_primary` | true |
| Snapshot mounted and clean **as the container sees it** | `git -c safe.directory=/repository status --porcelain=v2` inside the container | empty |
| Snapshot at the intended authority | same, `rev-parse HEAD` and `HEAD^{tree}` | the pinned commit and tree |
| No in-flight legacy work you are unwilling to strand | legacy task inventory | your call |

The snapshot check must be run **inside the container**, not on the host. A
snapshot created on Windows can be clean to host git and entirely dirty to the
container; see `docs/` history for the line-ending defect this caused.

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

1. Back up `.env` to a timestamped copy. This is the whole file-level rollback.
2. Write the three switches. Change nothing else.
3. Recreate the orchestrator **with the compose file that matches the running
   deployment**. Check which branch the repo root is on first: a stale
   `docker-compose.yml` will drop the primary DSN and the `/repository` bind
   without saying so beyond an orphan-container warning.
4. Wait for health, then verify.

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

## Rollback, and the two things that block it

`-Action Rollback` performs the inverse fenced generation change. It is **not
unconditional**. Before it will move the authority row it requires both:

- `ambiguous_effect_count == 0` — otherwise it raises
  *"ambiguous effects block rollback"*
- `primary_quiescence_metrics().rollback_safe` — otherwise it raises
  *"active provider dispatches, assignments, writer claims, reconciliation, or
  claims block rollback"*

So rollback is available while primary is quiet and has produced no ambiguous
effects, and is refused while it is mid-flight. Under Stage A, with providers
doubled, ambiguous effects should not arise — but the refusal is real and you
should not plan on rollback being always-available once live dispatch is on.

The sequence it runs: stop all intake, write the switches false, wait 65 seconds
for the lease to lapse, run the guarded inverse compare-and-swap in a throwaway
container, set `LANGGRAPH_PRIMARY_ENABLED=false`, recreate, wait for health,
then restart n8n and slackbot.

**What rollback does not do:** it never transfers ownership of tasks that already
exist (`langgraph_postgres.py:1825-1827`). Work created under primary stays with
primary. Rollback returns *new* intake to legacy; it does not rewind.

## Distance from Stage A to live

Stage A leaves these outstanding, and none are configuration:

- **Provider activation evidence.** `combined_live_ready` requires LIVE_OK
  evidence across the roster on two surfaces. `-Action Activate` asserts it.
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
