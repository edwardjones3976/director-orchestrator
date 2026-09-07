<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 07:05 UTC | BEGIN]] -->
# Provider activation evidence: mechanism and blockers

**Status:** Specification. Deliberately **not** a runbook, and it supplies no
values to paste.

Fifteen candidate operator instructions were drafted for this document and
independently checked against the code. **Thirteen failed.** The quoted evidence
was almost always exact; the *instructions* were what broke. One would have
produced a `DIRECTOR_HOST_PROVIDER_COMMANDS_JSON` that passes the bridge
preflight and then kills the server at startup. One would have silently degraded
a live deployment. One stated two credential-store paths that nothing in this
repository determines.

That failure rate is the finding. This area is unusually hostile to plausible
instructions, so this document records the **mechanism and the blockers** and
stops short of prescribing configuration. Values come later, from someone
holding the deployment, with each one derived rather than pattern-matched.

## The ordering constraint

Live primary startup **requires current LIVE_OK evidence** and raises without it
(`primary_default.py:2172-2176`). Live dispatch therefore cannot be used to
produce the evidence that live dispatch requires.

Evidence is not produced by dispatching work at all. `make_capture` states it:
*"Only the bridge calls this after configured native execution returns"*
(`provider_validation.py:277-278`). A capture must normalize to the same
provider, `status == "SUCCEEDED"`, and an **empty** `side_effects` array
(`:287-288`, which raises *"native canary did not complete without effects"*).

So the evidence run is a set of controlled native canaries, not billable task
runs. The cost concern that motivated this work was misplaced. The setup is the
work.

## The arithmetic

Five stages — `INSTALLED`, `AUTHENTICATED`, `WORKSPACE_BOUND`, `DRY_RUN_OK`,
`LIVE_OK` (`provider_activation.py:25-30`) — across two surfaces, per provider.
Ten rows per provider, at revisions 1..5 per (binding, surface), with no gaps and
no substitutions (`:398-413`).

The provider multiplier is `len(bindings)`, **not** the nine-lane roster:
`health()` ANDs over `self._registries`, one per key in
`LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON`
(`primary_provider_activation.py:167, :186`). `required_providers` is only an
admissibility filter (`:44-46`). Configuration forces the bound set to satisfy
`core_inventory_ready` (`config.py:1384-1385`), whose floor is openai plus two of
{claude, cursor, grok} (`primary_roster.py:27-29`).

**Minimum 3 x 2 x 5 = 30 rows.** The full nine-lane roster would be 90.

Every stage must be **current**, not merely historical. One lapsed row anywhere —
including `INSTALLED` — returns `stage=None` with both `dry_run_ready` and
`live_ready` false, forcing `combined_live_ready` false
(`provider_activation.py:372-381`). Stages 1-4 carry no code-imposed TTL;
`LIVE_OK` is capped by the validation generation at <= 86400 seconds and is
re-checked on every `provider_ready` call. **There is no renewal path**: a lapse
means redoing the chain.

## Five blockers, each verified

**1. The generation identifier is not derivable today.**
`DIRECTOR_PROVIDER_VALIDATION_GENERATION` must be the primary generation manifest
SHA256. `LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256` and `_PATH` are both absent
from `.env`, so there is nothing to derive it from. This is not a value to invent.

**2. The mandatory openai lane is skipped by default.**
`langgraph_primary_openai_transport` defaults to `"responses-api"`
(`config.py:664`), and `primary_transports.py:1249-1250` reads:

```python
if provider_id == "openai" and openai_transport != "native-codex-host":
    continue
```

openai is mandatory for `core_inventory_ready`, so with the default transport the
gate cannot be satisfied no matter what the other lanes do.

**3. The bridge contradicts itself about cursor.**
`host_provider_bridge.py:41-43` allowlists `cursor` in `SUPPORTED_HOST_LANES`.
`scripts/windows-host-provider-bridge.ps1:65-67` throws
*"cursor cannot be enabled through the Windows host bridge"* if the command map
contains it. The Python half admits the lane the PowerShell launcher refuses to
start. One of the two is wrong and this must be resolved before cursor can be
counted toward the floor.

**4. grok has no compatible surface.**
Its non-interactive mode is a persistent stdio/WebSocket agent protocol, not a
single-shot JSON envelope, and no packet wrapper for it exists in this
repository. A capture requires a raw native terminal envelope on stdout.

**5. antigravity cannot produce a capture here.**
It has no CLI at all — it is IDE-only — and its capture path is hard-routed to a
pinned-SSH team route that is unbound on this deployment.

Taken together: the floor needs openai plus two of {claude, cursor, grok}. openai
is blocked by (2), cursor by (3), grok by (4). **claude is the only lane that is
merely unconfigured rather than blocked.**

## A topology problem the variable names hide

`DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN` is read by three processes in two
different network positions: the orchestrator container (verifier,
`provider_validation.py:271`), the Windows host bridge (writer,
`host_provider_bridge_server.py:249`), and whichever host runs the CLI
(`provider_validation_cli.py:23`).

The existing `langgraph-primary-db` service publishes **no ports**
(`docker-compose.yml`, contrast the orchestrator's `127.0.0.1:${DIRECTOR_HOST_PORT}`
mapping), so it is reachable only on the compose network. A single value cannot
satisfy both a container-network consumer and a host-process consumer. Whoever
writes these values must decide the topology first; the identical variable name
in three places conceals that decision.

## Schema creation is an explicit act

Unlike the primary control plane, which creates and verifies its versioned schema
during startup, the validation store performs **no DDL** when constructed or read
(`provider_validation.py:5-6`). `bootstrap()` is the only DDL path and its only
caller is the CLI (`provider_validation_cli.py:22-24`).

That single command performs both the DDL **and** the generation insert, so the
generation manifest must already be complete and valid at DDL time. There is no
DDL-only entry point, no version row, no drift check, and no advisory lock — the
primary plane takes `pg_advisory_xact_lock`; this store takes none.

The schema name `director_provider_validation_v1` is a module constant with no
environment override, and the checkpoints table hard-codes its namespace in a
CHECK constraint, so neither can be renamed.

## What this specification deliberately does not supply

No DSN, no command map, no capture key, no generation identifier, no candidate or
tree value. Thirteen of fifteen drafted instructions in this area were wrong on
review, several in ways that pass a preflight and fail later. Each value needs
deriving against the deployment that will hold it, and checking before it is
written.

## Recommended order

1. Resolve blocker (3). It is a contradiction inside one component and is
   answerable from the repository alone, without touching the deployment.
2. Decide the topology question, since it constrains every DSN that follows.
3. Establish the generation manifest, which blocker (1) depends on.
4. Prove one lane end to end — claude, as the only unblocked candidate — and only
   then generalise.
5. Treat (2), (4) and (5) as separate scoped decisions. Each changes what the
   roster can contain, and (4) and (5) may mean the floor is unreachable without
   a code change.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 07:05 UTC | END]] -->
