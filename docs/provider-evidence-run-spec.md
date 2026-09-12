<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:20 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:57 UTC | BEGIN]] -->
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

**3. Cursor launcher parity is resolved; Windows containment remains unavailable.**
The Python and PowerShell allowlists were aligned by `eb2d1b7` and remain
covered by the Windows cutover regression tests. That does not establish
launchability or qualification. The September 8 inspection found a resolvable,
authenticated CLI and supported flags, but the subsequent canary below showed
that its required `--sandbox enabled` invocation cannot execute on Windows.
Discovery now withholds this native lane on unsupported platforms and rejects
Windows launchers reached through WSL. It never downgrades containment.
Qualification requires a supported, independently verified host route.

**4. Native Grok's observed envelope needs the corrected shared normalizer.**
The September 8 observation below established an actual `sessionId` / `text` /
`stopReason` shape, rather than an unknown envelope. The integrated normalizer
decodes structured text, requires terminal `end_turn` and exactly one native
turn, and retains side-effect evidence for strict downstream validation.
The capture and dispatch paths share this terminal check. Fixture conformance
does not qualify an installed runtime or authorize another paid probe.
This is the native Grok CLI lane, not Grok Bot and not a Cursor-hosted Grok model.

Capability inspection is distinct from an exhausted paid probe. Reusing the
dated capture avoids repurchasing the same evidence; any further paid action
still needs its own admitted authority, bounds and new information gain.

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

The original qualification floor required OpenAI plus two of {Claude, Cursor,
native Grok}. This document does not establish current enrollment or LIVE_OK.
Cursor needs a supported host; native Grok's recorded dialect is now covered by
shared normalization tests; OpenAI and the other lanes retain their separate
binding and acceptance requirements. No new provider call was made for this change.

## Capture conformance is testable without another provider call

`tests/test_provider_validation.py` tests the recorded Grok dialect, malformed
and over-turn results, supported Cursor success envelopes, and prohibited effect
evidence. Native Grok must meet its own terminal criteria even if it also carries
a generic success marker. Unknown side-effect keys are not projected away.
Passing these fixtures proves code behavior, not runtime qualification.

## Canary results, 2026-09-08

Two canaries were run under owner authorization, one attempt each, using the
exact pinned invocations. Neither is repeated here.

**cursor — blocked before any model call, no spend.** The lane pins
`--sandbox enabled` (`primary_transports.py:1185`). The installed CLI rejects it:
*"Sandbox mode is enabled but not available on this system. Sandbox requires
macOS or Linux."* The only other accepted value is `disabled`, which drops the
operating-system containment the lane deliberately requires, and which the
harness notes metadata cannot substitute for. **As pinned, this lane cannot
execute on the Windows deployment target at all.** Resolving it is an owner
decision between a non-Windows host for this lane, an approved change to the
pinned containment, or a different containment mechanism. Note also that the
first attempt hit a workspace-trust prompt; any automated capture must expect it.

**grok — ran, and answered the open question. Cost USD 0.0065.** The pinned
invocation executed cleanly and returned an authenticated envelope. At that date it was **not**
capture-conformant, for three concrete reasons rather than an ambiguous failure:

| Capture rule | Observed |
| --- | --- |
| JSON object, no `schema_version` | passes |
| no truthy `tool_calls`/`tool_uses`/`side_effects` | passes |
| `status == "SUCCESS"` or `type`/`subtype`/`is_error` | fails — grok emits `stopReason: "end_turn"` |
| string `session_id` or `conversation_id` | fails — grok emits `sessionId` |
| (then) an `outcome` via `_structured_native_output` | would fail — the answer is in `text`, with no `structured_output` or `result` |

The then-current `normalize_capture("grok", ...)` rejected it with *"native unique successful
terminal/session is missing"*. The observed shape is pinned in
`tests/test_provider_validation.py` so this does not have to be repurchased.

The integrated normalizer addresses those dated naming and terminal-shape
failures with fixtures. The observation remains historical and has not been
repeated or promoted to a current installed-route acceptance record.

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

The topology decision is unchanged by that: consumers MAY require different DSN
endpoints, so resolution and reachability must be verified in each host and
container context, and the identical variable name in three places still
conceals which one is meant. Whoever writes
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

1. For blocker (3), establish a supported Cursor host route with the required
   containment. The old launcher/bridge contradiction is already resolved.
2. Decide the topology question, since it constrains every DSN that follows.
3. Establish the generation manifest, which blocker (1) depends on.
4. Prove one lane end to end — claude, as the nearest candidate rather than a
   qualified one — and only then generalise. Its first capture is a real,
   possibly metered invocation and needs a reservation under the campaign
   ceiling below.
5. Treat (2), (4) and (5) as separate scoped decisions. Each changes what the
   roster can contain. (4) needs current installed-route qualification using the
   reviewed normalizer, without replaying an ambiguous paid probe; (5) needs the team capture route bound plus host
   authentication and enrollment settled; either may still require a code
   change.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 07:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:57 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 10:20 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | END]] -->
