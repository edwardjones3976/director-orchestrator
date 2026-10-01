<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:43 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:30 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 11:30 UTC | BEGIN]] -->
# Stage A evidence worksheet — profile S

**WO-04 deliverable.** Fill this in **as you run**, not afterwards. Every field
here is consumed directly by `docs/close-out-report-2026-09-13.md`, so a
completed worksheet closes the report with no reconstruction.

**Record names and readiness booleans only.** No secret, DSN, allowlist value,
token, password or channel ID belongs in this file. It is a repository document.

---

## 0. Profile parameters (non-secret; rename freely)

| Field | Value | Confirmed |
| --- | --- | --- |
| Compose project | `dirsim` | ☐ |
| Environment file | `.env.simulation` | ☐ |
| Host port | `8091` | ☐ free (WO-01) |
| Snapshot directory | `<snapshot-dir>` | ☐ |
| Checkpoint namespace | `sim-director-primary-v1` | ☐ |
| Postgres schema | `director_primary_v1` | ☐ |
| Slack | **none** — S never posts | ☐ |
| Secret values | in the owner's store, **not here** | ☐ |

## 1. Owner-provided generation manifest

Recorded before `SelectGeneration`. Names and digests only.

| Field | Value |
| --- | --- |
| Candidate commit (40 hex) | ______ |
| Candidate tree (40 hex) | ______ |
| Image content-addressed ID | ______ |
| Build args supplied | `DIRECTOR_CANDIDATE_COMMIT`, `DIRECTOR_CANDIDATE_TREE` |
| Generation identifier | ______ |
| Selected at (UTC) | ______ |

`LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` and `..._PINNED_TREE_SHA` set to the
candidate: ☐. **Blank or mismatched pins are a stop, not a proceed.**

## 2. Preconditions

| Check | Where | Expected | Result |
| --- | --- | --- | --- |
| `configuration_valid` | `/health` → `langgraph_primary` | true | ______ |
| Snapshot clean **inside the container** | `git -c safe.directory=/repository status --porcelain=v2` | empty | ______ |
| Snapshot at the intended authority | same, `rev-parse HEAD` and `HEAD^{tree}` | equals the pins | ______ |
| Primary-plane quiescence | provider dispatches, assignments, writer claims and reconciliation | zero / none | ______ |
| No in-flight legacy work you will strand | legacy task inventory | owner's call | ______ |
| Protected configuration backup taken | three switch names, prior values, via the protected-payload procedure | done | ______ |

The host-side `git status` is **not** the authoritative check. A checkout clean
to Windows can be entirely dirty to the container.

## 3. Lifecycle actions

Record the exit state and the UTC timestamp for each. Any refusal is
`OWNER_DECISION_REQUIRED` — record the exact failing fact and stop; do not retry.

| # | Action | Exit | UTC | Note |
| ---: | --- | --- | --- | --- |
| 1 | `docker compose build orchestrator` (explicit; building is not acceptance) | ______ | ______ | |
| 2 | start the primary Postgres service | ______ | ______ | |
| 3 | `BootstrapDatabaseIdentity` | ______ | ______ | identity UUID: ______ |
| 4 | `SelectGeneration` | ______ | ______ | |
| 5 | `PrepareAcceptance` | ______ | ______ | emits unsigned JSON to stdout |
| 6 | `Preflight` | ______ | ______ | |
| 7 | obtain acceptance artifact from independent issuers | ______ | ______ | artifact path: ______, issuer identities: ______ |
| 8 | `Accept -AcceptanceArtifactPath` | ______ | ______ | |
| 9 | `Migrate` | ______ | ______ | creates control-plane + team DDL |
| 10 | `Backup` | ______ | ______ | file name: ______ |
| 11 | `RestoreDrill` | ______ | ______ | |
| 12 | `Health` | ______ | ______ | |

## 4. Stage A switch write

Backup of prior values recorded (§2) ☐. Then write **exactly** these three, and
nothing else:

```
LANGGRAPH_PRIMARY_ENABLED=true
LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED=true
LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED=true
```

Left untouched and expected to stay false:
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED`, `ALLOW_PAID_RUNS`.

Recreate with **the compose file that matches the running deployment**. A stale
`docker-compose.yml` drops the primary DSN and the `/repository` bind with no
warning beyond an orphan-container notice.

The first two switches must move together: `ENABLED=true` with
`NEW_INTAKE_ENABLED=false` returns HTTP 503 on every intake, with no per-request
fallback — the flag is read once at import.

## 5. Verification — the nine fields

All nine true:

| Field | Result |
| --- | --- |
| `primary_graph_constructed` | ______ |
| `primary_router_wired` | ______ |
| `checkpoint_backend_postgres` | ______ |
| `checkpoint_schema_current` | ______ |
| `claim_store_ready` | ______ |
| `effect_ledger_ready` | ______ |
| `writer_lease_ready` | ______ |
| `reconciliation_worker_ready` | ______ |
| `legacy_new_intake_disabled` | ______ |

**The field that measures Stage A:**

| Field | Expected | Result |
| --- | --- | --- |
| `paid_off_primary_intake_active` | **true** | ______ |
| `primary_runtime_active` | **false** | ______ |

`primary_runtime_active` false is **success**, not failure. It takes live
provider dispatch as its first conjunct, and live dispatch is mutually exclusive
with offline simulation, so under Stage A it cannot be true. Expecting otherwise
is the easiest way to misread a good pass as a bad one.

**Ownership proof:**

| Field | Expected | Result |
| --- | --- | --- |
| `langgraph_primary.new_intake_owner` | `langgraph` | ______ |
| `intake_fencing_generation` | incremented from ______ to ______ | ______ |
| `ambiguous_effect_count` | `0` | ______ |

**Switches that must not have moved:**

| Field | Expected | Result |
| --- | --- | --- |
| `paid_runs_enabled` | false | ______ |
| `live_provider_dispatch_requested` | false | ______ |
| `offline_simulation_requested` | **true** | ______ |

`legacy_new_intake_disabled` is not an independent switch — it is a
database-enforced mirror of `intake_owner = 'langgraph'`, held by a CHECK
constraint and a trigger. If it is true, the authority row genuinely moved.

## 6. n8n exercise against S

| Item | Result |
| --- | --- |
| Intake request sent (direct HTTP; S has no Slack) | ______ |
| Task record created, task ID | ______ |
| Idempotency: same event ID re-sent creates no second task | ______ |
| Provider result labelled `SIMULATION — NO PROVIDER INTERACTION` | ______ |
| Non-2xx mapped to an explicit webhook error response | ______ |
| Status workflow posted the readiness change | ______ |
| Bearer auth via credential, `httpBearerAuth`, not inline | ______ |

## 7. Operational findings from A-16 (public research, Director-side surface)

Recorded as untrusted public evidence, evaluated by the Director. It is **not**
evidence that the repository Firecrawl lane is enrolled or live.

1. **`thread_id` has a bounded column length.** LangGraph's Postgres savers store
   `thread_id` in a limited-length column; values over 255 characters produce a
   database error. Deterministic IDs should be UUIDs or hashes rather than
   concatenated descriptive strings. **Check the Director's generated thread IDs
   against that bound before the first long-running task.**
2. **Checkpoints grow without bound.** Safe pruning is an open feature request
   upstream (`langgraph#8531`), not a shipped capability — the proposed
   `keep_latest` would retain the newest checkpoint per
   `(thread_id, checkpoint_ns)`. Until it exists, the primary database grows
   monotonically. Plan backup sizing and disk headroom accordingly, and do not
   assume a retention policy exists.
3. **`(thread_id, checkpoint_ns)` is the isolation key.** This matches the
   deployment's per-profile checkpoint namespace design, and it is why a distinct
   `LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE` per profile is load-bearing rather
   than cosmetic — two profiles sharing a namespace would share checkpoint rows.

### Checked against this source, 2026-09-09

Finding 1 verified, and it is **not a live defect**. `thread_id` is composed as
`f"{namespace}:{task_id}"` (`langgraph_primary.py:2188`, and identically in
`langgraph_shadow.py:697`). At real configured values:

| Profile | namespace | task id | composed | headroom to 255 |
| --- | ---: | ---: | ---: | ---: |
| deployed `default-director-primary-v1` | 27 | 17 | **45** | 210 |
| proposed sim `sim-director-primary-v1` | 23 | 17 | **41** | 214 |

With this task-id shape a namespace could reach 237 characters before the bound
is at risk, so current values are safe by a wide margin.

**But there is no length validation on the composed value anywhere in the
source.** An operator who sets a long checkpoint namespace gets a database error
at runtime rather than a configuration refusal at startup. Recorded as a latent
robustness gap for the follow-on, not a blocker: a `configuration_valid` check
on `len(namespace) + len(task_id) + 1 <= 255` would convert a runtime failure
into a startup refusal, which is the behaviour every other switch in this
configuration already has.

Finding 2 also verified. A search of the source for pruning, retention,
`keep_latest`, or any checkpoint deletion returns **nothing**. The upstream gap
applies here unmodified: **the primary database grows monotonically.** Size
backups and disk headroom on that basis, and do not assume a retention policy
exists to be tuned.

## 8. Evidence bundle — what the report consumes

Assemble as one folder, redacted, named for the profile and UTC completion.

| Item | Captured |
| --- | --- |
| Redacted `/health` JSON, before and after the switch write | ☐ |
| Every control action, its exit state and UTC timestamp (§3) | ☐ |
| Generation manifest (§1) | ☐ |
| Preconditions results (§2), including the in-container cleanliness check | ☐ |
| The nine fields, the two Stage A fields, ownership proof, unmoved switches (§5) | ☐ |
| n8n exercise record (§6) | ☐ |
| Backup file name and `RestoreDrill` outcome | ☐ |
| Any refusal: the exact failing fact, verbatim | ☐ |
| Attended minutes actually spent, per block | ☐ |

Attended minutes matter: the close-out report compares them against the 3.6-hour
estimate, and that comparison is the only honest measure of whether R5 helped.

## 9. What this worksheet does not establish

A completed Stage A pass proves that the primary plane owns intake and runs its
own lifecycle with the money switched off. It proves **nothing** about live
provider execution, and its provider results are produced by in-process doubles.
It satisfies Gate 3 and supplies evidence toward gates 2 and 6 for the ledger
to record, closes no other gate, and leaves eight of nine outstanding, Gate 8
among them at `F`. Do not describe it as an activation.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 11:30 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 19:30 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:25 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 20:43 UTC | END]] -->
