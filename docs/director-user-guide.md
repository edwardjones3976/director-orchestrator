<!-- [[Codex | GPT-6 Astra | 2026-09-23 | 00:02 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 21:21 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 20:59 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:37 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 22:36 UTC | BEGIN]] -->
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 10:45 UTC | BEGIN]] -->
# Director-Orchestrator operator guide


For the owner running this system day to day. It covers what to do, what the
output means, and what to do when something refuses.

One idea underpins everything here: **the system is built to refuse, and a
refusal is information rather than a fault.** Almost every stop you will hit is
a gate doing its job. The useful response is to read what it refused on, not to
retry it.

## INVESTIGATOR responsibility

OpenAI/Director members also act as **INVESTIGATOR** under the
[canonical charter](team-operating-charter.md#openaidirector--investigator): identify and document
issues and suggested solutions, then hand them off through the acting Director's
task thread or an existing PR/issue, when the assignment authorizes communication.
Do not directly fix an investigated issue unless separately assigned that fix,
and do not take over the ongoing assignment. Record disposition with the finding;
The Director relays sanitized summaries only to Slack; detailed findings stay in
the task thread or PR/issue, subject to existing authorization gates.

## Daily operation

### Giving the Director work

Post in the Director Slack channel, starting the message with `DIRECTOR`. The
gateway accepts only your user ID and that channel, ignores bot messages, and
forwards a stable Slack event ID. n8n normalizes it and posts to the
orchestrator's `/v1/intake`, which deduplicates on that event ID, creates a task
record, applies hard limits, and builds either a preview or a live plan
depending on whether paid runs are enabled.

Duplicate posts are safe. The event ID is the idempotency key, so re-sending
does not create a second task.

### Approving

Approval-gated plans arrive in Slack with a confirm button. Where the request is
ambiguous, the button is withheld and you answer in writing:

```
/director approve DIR-… <clarification>
```

An approval is bound to a specific action, scope and candidate. It does not
widen, and it does not survive a change to what it approved — if the candidate
moves, the approval is stale and a fresh one is required. This is deliberate.

### Reading health

```powershell
curl.exe http://127.0.0.1:<port>/health
```

`/health` returns readiness **booleans only** — never secrets, allowlist values
or DSNs. The fields worth knowing:

| Field | Meaning |
| --- | --- |
| `paid_runs_enabled` | Whether money can be spent at all |
| `langgraph_primary.new_intake_owner` | `legacy` or `langgraph` — who owns new task intake |
| `intake_fencing_generation` | Increments on each authority change; a stale generation is rejected |
| `ambiguous_effect_count` | **Watch this.** Non-zero blocks rollback and means an effect's outcome is unresolved |
| `paid_off_primary_intake_active` | Primary owns intake with the money switched off — the field that measures Stage A |
| `primary_runtime_active` | The **live** path. False under Stage A by design |
| `legacy_new_intake_disabled` | A database-enforced mirror of the authority row, held by a CHECK constraint and a trigger |

The single most common misreading: expecting `primary_runtime_active` to be true
after a successful Stage A pass. It cannot be — it takes live provider dispatch
as its first conjunct, and live dispatch is mutually exclusive with offline
simulation. Stage A success is `paid_off_primary_intake_active` true.

### Status posts

The n8n status workflow polls `/health`, diffs the readiness booleans against
the previous run, and posts to the Director channel **only on change**.
**It speaks anyway** — regardless of change — on three conditions: the health
endpoint is unreachable, `ambiguous_effect_count` is above zero, or the parallel dispatch kill switch is engaged while parallel dispatch is enabled. An engaged switch with parallel dispatch disabled is the expected offline posture: it posts once as a readiness change and is then silent. The ambiguous-effects alarm matters most:
rollback is refused while that count is non-zero.

## The control script

`scripts\windows-langgraph-primary-control.ps1`. Every invocation needs the exact
environment file, Compose project and host port — profile identity is never
inferred. Run it from the deployment snapshot, not from a repository checkout
that may be on a different branch.

The order, with Slack intake closed:

```
build (explicit) → BootstrapDatabaseIdentity → SelectGeneration
  → PrepareAcceptance → Preflight → Accept → Migrate
  → Backup → RestoreDrill → Health
  → [StageRuntime → ValidateProviders] → Activate
```

Building is not acceptance. Under the default `github-v1` acceptance mode, `Accept` reads the canonical GitHub pull request and
requires the head to equal the frozen candidate, every named CI check completed
successfully with none skipped, pending or failed, and two distinct non-author
artifacts — security and independent review — each an `APPROVED` review whose
body is exactly the canonical `director-activation-review/v1` JSON. Comments,
quoted markers and prose never qualify. This runtime-activation requirement is distinct from ordinary single-reviewer engineering review. Alternative modes apply their own specific evidence rules. See [langgraph-primary-operations.md](langgraph-primary-operations.md) for the `github-v1` path, [provider-acceptance-v2.md](provider-acceptance-v2.md) and [provider-acceptance-v2-runtime.md](provider-acceptance-v2-runtime.md) for v2, and [two-party-activation-v3.md](two-party-activation-v3.md) for v3's explicit signed adoption path. Exact candidate, CI, security, independent identity, owner approvals, and fail-closed requirements remain mandatory where the selected contract imposes them, without imposing v3's signed `owner_adoption` mechanism on all modes.

## Backups

Take them with the activation switches still off.

```powershell
$env:DIRECTOR_ENV_FILE = ".env"
$backupName = "director-primary-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
New-Item -ItemType Directory -Force ".\backups" | Out-Null
docker compose --env-file .env exec -T langgraph-primary-db pg_dump `
  --username=director_primary --dbname=director_primary `
  --format=custom --file="/tmp/$backupName"
docker compose --env-file .env cp "langgraph-primary-db:/tmp/$backupName" ".\backups\$backupName"
docker compose --env-file .env exec -T langgraph-primary-db rm -f "/tmp/$backupName"
```

Restore only in a supervised window, with both primary switches and paid
execution off, every Director client stopped, and one explicitly named backup.
n8n is not stopped or restored — it uses a different database and volume.

Backup files are sensitive operational data. Store them encrypted, verify a
disposable restore before relying on one, and never commit them.

## The kill switch

`LANGGRAPH_PARALLEL_KILL_SWITCH` blocks new graph tasks and reconciles in-flight
effects. It is the fast stop. It does not undo anything already executed.

## Rollback — read this before you need it

**Rollback changes who owns new intake. It is not an undo.**

It requires, in order: intake proved closed (it stops `slackbot`, `n8n` and
`orchestrator`, then re-reads the container lists and throws if any survives);
the same accepted pins, a clean checkout matching them, and an exact-candidate
image; `ambiguous_effect_count == 0`; and quiescence.

Both refusals fire **after** intake has been stopped and the switches written
false. A refused rollback is therefore not a no-op — it leaves the deployment
with intake down and primary still owning the authority row. The deployed script
does not yet carry PR #115's correction, and that correction does not change the
refusal path.

What it never does: transfer ownership of tasks that already exist, undo a
merge, a Slack message, a GitHub write or a provider invocation, or clear
`ambiguous_effect_count` — it refuses while that count is non-zero, which is
precisely the situation where an undo would be most wanted.

Plan on not needing it.

## Where the evidence lives

| Evidence | Location |
| --- | --- |
| Acceptance, CI, security, review bundle | Immutable in the primary Postgres control plane, bound to task, candidate SHA, provider-result digest and gate-ledger digest |
| Provider activation rows | Five stages × two surfaces per bound provider; `LIVE_OK` expires after 86,400 s with no renewal |
| Task and effect state | Primary Postgres, distinct namespaces per deployment |
| Backups | `.\backups\` |
| Plan, rules, matrix, findings, audit | `docs/` on the delivery branch |

Restart readback revalidates canonical content and all three distinct authority
proofs. Missing or conflicting evidence stays `OWNER_DECISION_REQUIRED` and can
never be replaced by a synthetic pass.

## When something refuses

| Symptom | What it means | Do this |
| --- | --- | --- |
| `Accept` refuses | A check, review or head does not match exactly | Read which fact failed. Do not retry — fix the fact |
| Blank or mismatched pins | The candidate is not pinned | Stop. Set the pins to the accepted candidate's commit and tree |
| `combined_live_ready` false | Provider activation evidence incomplete or expired | Check for a lapsed `LIVE_OK`; the chain must be redone, not renewed |
| `ambiguous_effect_count > 0` | An effect's outcome is unresolved | Reconciliation, never a blind retry. Rollback is blocked while it is non-zero |
| HTTP 503 on every intake | `LANGGRAPH_PRIMARY_ENABLED=true` with `NEW_INTAKE_ENABLED=false` | There is no per-request fallback; the flag is read once at import. Set both together |
| Snapshot dirty in the container, clean on the host | Line-ending normalization | The container's check is the authoritative one |
| A provider is silent, out of quota or unauthenticated | Nothing has been approved | Absence of evidence is never consent |

## Things that are true and easy to forget

Configuration is not runtime state — an `.env` switch records intent, and only
`/health` records reality. A merged pull request activates nothing. Green CI
proves tests passed, not that a provider executed. Coordination creates no
production credit. No lane, including the Director, may accept or merge its own
material work. And a gate reading *unknown* is not a gate reading *pass*.
<!-- [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 10:45 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 22:36 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:37 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 20:59 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 21:21 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-23 | 00:02 UTC | END]] -->
