<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | BEGIN]] -->
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

So the evidence run is a set of controlled native canaries rather than
task-shaped work with external effects. That is a statement about *effects*, not
about *cost*. Validation calls the configured native executor and inspects the
result afterwards; the empty `side_effects` array is a property of the returned
result packet, and it says nothing about what the provider charged to produce
it. Each canary is a real inference call and can consume money or subscription
allowance. The cost concern that motivated this work is not misplaced; it is
answered under "Money" below, not dismissed. The setup is still most of the
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

## Five blockers, corrected against the source

Three of the five as first written overstated their case. The corrected form is
below; where a claim shrank, what replaced it is still a blocker, not a
clearance.

**1. The generation identifier does not exist on this deployment yet.**
`DIRECTOR_PROVIDER_VALIDATION_GENERATION` must be the primary generation manifest
SHA256. `LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256` and `_PATH` are both absent
from `.env`, so there is nothing to derive it from today. This is not a value to
invent. There is a sanctioned way to establish it: the control script's
`SelectGeneration` action takes an owner-supplied manifest and its exact digest,
reserves them, and writes both names. Until that has been run against a real
manifest, this stays blocked.

**2. The openai lane needs the native route configured and qualified — the
default is not a contradiction.**
`langgraph_primary_openai_transport` defaults to `"responses-api"`
(`config.py:664`). On that default the lane is not skipped: `1216-1225`
constructs the Responses API adapter whenever `OPENAI_API_KEY` is set and openai
authentication is observed. The line that reads

```python
if provider_id == "openai" and openai_transport != "native-codex-host":
    continue
```

is inside the host-bridge loop (`primary_transports.py:1249-1250`); its effect is
to stop a native host route from overriding the transport that was actually
selected. Reading it as a blanket skip of the openai lane was wrong.

What is true is narrower and still blocking for *evidence*: capture
normalization for openai is routed to `normalize_codex_jsonl`
(`provider_validation.py:309-311`), so a `LIVE_OK` capture for this lane has to
come from the native Codex host route. That route must be explicitly configured
(`LANGGRAPH_PRIMARY_OPENAI_TRANSPORT=native-codex-host`, host bridge lane, host
ChatGPT login) and then qualified against the strict capture checks. Neither
configuration nor an authenticated host is qualification, and dispatch working
over the Responses API is not evidence that the native capture path works.

**3. The bridge contradicts itself about cursor.**
`host_provider_bridge.py:41-44` allowlists `cursor` in `SUPPORTED_HOST_LANES`.
`scripts/windows-host-provider-bridge.ps1:65-67` throws
*"cursor cannot be enabled through the Windows host bridge"* if the command map
contains it, and its allowlist omits the lane. The Python half admits the lane
the PowerShell launcher refuses to start. Both checks have to agree, and the
parity correction is assigned to a separate worktree; it is not made here.
Resolving the contradiction in either direction only makes the lane launchable —
it does not qualify the installed route.

**4. grok has direct CLI paths; the installed envelope is unproved.**
The repository does contain finite, single-shot direct paths for grok:
`primary_transports.py:1186` pins `--output-format json --max-turns 1
--permission-mode plan` and passes the packet through `--prompt-file`
(`:979-997`), and `providers.py:1317-1345` carries the matching direct
invocation. Saying no wrapper exists was wrong.

The unproved part is compatibility, on two separate surfaces. Strict capture
validation (`provider_validation.py:301-328`) requires a raw native terminal
envelope with a successful terminal status, a string session or conversation
identifier, no `schema_version` key, and no tool or effect keys; the host bridge
instead feeds raw stdin (`:449-458`) and demands terminal JSON (`:466-474`).
Whether the *installed* Grok CLI emits an envelope that satisfies those checks
has not been demonstrated. The exhausted native probe must not be repeated for
this document; its existing owner retains any reviewed correction.

**5. antigravity has an installed CLI; its capture route is unbound.**
It is not IDE-only. An Antigravity CLI is installed on this host, reports
version 1.1.27 and carries a valid Authenticode signature, and
`primary_transports.py:1187` already resolves `agy.exe` as a native lane. The
version and signature checks made no inference call.

Host authentication, protected-worker availability and enrollment are separate
questions and remain unestablished; an access-denied protected path is
indeterminate, not evidence of absence, and existing authentic component reviews
and installation receipts must be reconciled rather than described as
nonexistent. The actual capture blocker is narrower: `normalize_capture` routes
antigravity to a pinned-SSH team route (`provider_validation.py:303-306`) and
raises *"Antigravity team SSH capture verifier is unbound"* when that route is
absent, which is the state on this deployment.

Taken together: the floor needs openai plus two of {claude, cursor, grok}. No
lane is currently qualified. openai needs the native Codex route configured and
qualified (2); cursor needs the launcher/bridge parity resolved and then
qualified (3); grok needs its installed envelope proved against the capture
checks, without repeating the exhausted probe (4). claude is the closest, but
"unconfigured" is not the same as "ready": it too must be configured and then
produce a real capture. Nothing here establishes that any lane will pass.

## A topology problem the variable names hide

`DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN` is read by three processes in two
different network positions: the orchestrator container (verifier,
`provider_validation.py:271`), the Windows host bridge (writer,
`host_provider_bridge_server.py:249`), and whichever host runs the CLI
(`provider_validation_cli.py:23`).

In the **base** `docker-compose.yml`, with no override applied, the
`langgraph-primary-db` service publishes **no ports** — contrast the
orchestrator's `127.0.0.1:${DIRECTOR_HOST_PORT}` mapping. Reading that as
"reachable only on the compose network" was an extrapolation from the base file
to actual reachability, and it was wrong to state it as a fact about the
deployment.

An independent review reports that a protected `database-port.override.yml`
exists, and prior owner evidence identifies host port 55439 as a publish for
this database. That protected configuration was not opened here and no secret
was inspected, so this document records only that the override exists. Whether
it is applied to the running deployment, and therefore whether the database is
in fact reachable from a host process, is **unknown from what may be read here**
— unknown, not absent. Do not treat either the base file or this paragraph as
evidence that the port is closed.

The topology decision is unchanged by that: a single value still cannot serve
both a container-network consumer and a host-process consumer, and the identical
variable name in three places still conceals which one is meant. Whoever writes
these values must establish the applied override and the actual reachability
first, from the protected configuration itself.

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

## Money

The owner-approved budget audit (2026-09-07 07:13 UTC) and launch budget
decision (2026-09-07 07:20 UTC) are authoritative over this document. Nothing
below invents a limit; where the two disagree with anything here, they win.

The selected ceilings:

| Limit | Ceiling | Scope |
| --- | ---: | --- |
| Initial launch validation campaign | USD 10 total | Every included lane and every validation invocation in the campaign, counting failed and ambiguous attempts. A new generation, candidate, transport, task identifier, process or UTC day does not reset it. |
| Primary project daily admissions | USD 50 per UTC day | Aggregate funded primary provider admissions for this Director project, across its tasks and providers. Project-scoped, not an account-wide or vendor-side limit. |
| Primary task admission | USD 14 per task | The existing task ceiling as reported; this document neither changes nor independently verifies the live value. |

Each invocation must satisfy every applicable ceiling. The campaign and
production scopes are distinct: the daily limit does not replace the campaign
limit, and the task limit does not multiply it. Selecting a ceiling is not
authorization to start paid execution or to activate anything.

These three ceilings are selections. None of them has been provisioned on this
deployment or verified as enforced end to end. That is not the same as saying
the source enforces no money at all — it does enforce some, and the distinction
matters when deciding what still has to be built.

Monetary admission that genuinely exists:

- **Per-task and per-assignment admission.** Primary planning divides
  `MAX_TASK_BUDGET_USD` into assignment allowances and then into per-attempt
  allowances, and admission is refused beyond them. The deployment was observed
  with that value at 14, matching the retained task ceiling above. This is a
  real gate inside the orchestrator, not a decoration — but it is a
  planning/admission control, not a demonstrated provider-side billing cap, and
  it says nothing about what a vendor actually charges.
- **Team-mode project and provider budgets.** Primary cross-task monetary
  admission is available through enrolled team routing, with limits supplied by
  signed project and provider registrations. It is conditional: team mode must
  be enabled and the entitlement and artifact services actually enrolled.
  Setting a daily environment variable does not wire that consumer, so the
  USD 50 daily ceiling has to be bound into that signed authority before it may
  be called enforced.

What is absent, and is the gap the campaign ceiling depends on:

- **No cross-generation USD campaign cap exists in the audited source.**
  Validation
  `budget_units` and per-lane `cost_units` are positive abstract integers
  (`provider_validation.py:105-164`). One transaction locks the generation,
  checks remaining units, and admits at most one attempt per generation and
  provider; the reservation persists through success, failure and ambiguity.
  There is no dollar or token conversion in that path, and the budget is scoped
  to a single generation, so a fresh generation starts a fresh unit allowance.
  Ten units are not ten dollars. Nothing accumulates money across generations,
  providers, processes or UTC days, which is precisely what the USD 10 ceiling
  requires. Until one durable shared ledger does that, the campaign cap is
  enforced only by whoever is running the campaign.
- `MAX_DAILY_PROVIDER_BUDGET_USD` has a default of 1000, but its ledger consumer
  is the legacy engine. It is not the primary daily monetary gate and must not
  be cited as one.
- Native subscription authentication — OpenAI Pro, Claude Max, an authenticated
  CLI — establishes neither remaining entitlement nor zero incremental cost.
  Where a route exposes no enforceable dollar interface, record that limitation
  and bound calls, turns, time and output instead of inventing a currency
  conversion.

Admission rules for the campaign: at most one canary per required lane; an
ambiguous paid invocation is never replayed; one durable reservation ledger
shared by all included routes and generations, reserving a conservative upper
bound before each potentially metered invocation and atomically refusing any sum
above USD 10; the whole reservation is retained for unknown or ambiguous
charges, counting at least the greater of the reservation and the authenticated
reported charge; absent telemetry is not zero. A lane whose dollar exposure
cannot be bounded stays unadmitted rather than being given an invented
allowance.

Canary bounds worth keeping as they are: fixed packets with `allowed_tools=[]`,
`allowed_external_effects=[]`, `max_hops=1`, `max_revisions=0`, and exactly one
captured native execution. The proposed 120-second per-invocation timeout counts
only once the configured executor is confirmed to enforce it for that lane. The
1 MiB capture ceiling is a post-execution evidence-size bound, not an output or
billing bound. A 512-token output ceiling applies only where the actual route
exposes and enforces that control, and a native CLI invocation may issue several
requests internally, so its turn and request limits need checking separately.

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
4. Prove one lane end to end — claude, as the nearest candidate rather than a
   qualified one — and only then generalise. Its first capture is a real,
   possibly metered invocation and needs a reservation under the campaign
   ceiling below.
5. Treat (2), (4) and (5) as separate scoped decisions. Each changes what the
   roster can contain. (4) needs the installed envelope proved without repeating
   the exhausted probe, and (5) needs the team capture route bound plus host
   authentication and enrollment settled; either may still require a code
   change.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 07:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:45 UTC | END]] -->
