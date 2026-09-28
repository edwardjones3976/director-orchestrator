<!-- [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:17 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-22 | 01:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 21:06 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:55 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 10:24 UTC | BEGIN]] -->
# Offline Slack trial evidence

This repository port collects and records evidence; the owner performs the
trials. It does not send messages, approve work, dispatch providers, deploy, or
award scorecard points. Retain the reviewed tooling commit with the receipts;
each receipt also records the collector's file SHA256.

The source collector SHA256 was
`58f1ba7710e536f509104ae3b8913f4a8cd1ea8660478cfee2adef04a194c26b`;
its 33-test suite was
`aeff3998098be06db6d3eba69965e49ca3e0461c760b334371adb06c2676d21f`.
All 33 source cases are retained and adapted to repository entry points.

## Declare correctness before dispatch

Read [the structural rubric](examples/offline-slack-structural-rubric-v1.json)
before trial 2. It requires:

- Exactly one native terminal reply bound to its durable delivery receipt.
- The bound primary task is `DONE`.
- Every assignment outcome is an `offline-double:` run whose result is exactly
  `SIMULATION — NO PROVIDER INTERACTION`, the label in `primary_default.py`.
- Every outcome has a 64-hex `provider_result_digest`.
- Terminal text contains that simulation label and `Actual USD cost: not recorded`.
  A terminal reply Slack reports as edited, or with any subtype, never counts as
  containing either: Slack keeps its original timestamp but returns the new text.

The collector accepts only this contract; a file cannot weaken its requirements.
Owner ruling (2026-09-24), option chosen: "Offline = pipeline proof". Check 10 proves the pipeline works end to end offline (delivery, simulation labels, ten consecutive, committed structural rubric); answer correctness is check 20's job (the real paid run). The structural rubric
therefore proves the offline pipeline, not that any answer is correct; check
10 does not verify answers and check 20 (the real paid run) does. A receipt
counts only as a bot reply's leading header, never as text quoted inside a
provider result.
Compute its **file-byte SHA256** and include
`rubric-sha256:<64-lowercase-hex-digest>` in the owner's original trial request.
The collector matches that source event back to immutable intake text. The
rubric's timezone-aware declaration time must precede the input. A backdated
file alone is insufficient: changed bytes also change the digest already
declared in the source message.

```powershell
(Get-FileHash docs/examples/offline-slack-structural-rubric-v1.json -Algorithm SHA256).Hash.ToLowerInvariant()
```

This measures simulated orchestration structure, not real provider answer
quality. The good-run standard additionally requires authentic owner input,
bounded work, one task, no ambiguous effects/delivery, and complete attributable
evidence. Wall time, machine time and actual cost are reported metrics; the former
60-second/USD 1 figures are not acceptance gates. Nonnegative measurements and
wall = owner pause + machine remain required; separate spending caps still apply.

## Collect after the owner completes a trial

```powershell
$env:PYTHONPATH = 'src'
python scripts/collect-offline-slack-trials.py `
  --expected-head <full-observed-release-commit> --trial <thread-timestamp> `
  --rubric docs/examples/offline-slack-structural-rubric-v1.json `
  --output-dir <new-receipt-directory>
```

Repeat `--trial` at most ten times. One bounded paginated `conversations.replies`
read per thread supplies exactly one unambiguous task receipt in that thread from
the bot authenticated by `auth.test`: the current gateway's `REQUEST RECEIVED`
reply or the legacy `ACCEPTED` reply. Neither wording is approval or proof of
successful execution. Missing, duplicate, mixed-wording, foreign-bot, wrong-thread
or incomplete-readback evidence cannot select a task. The task API must bind its
ID, engine header, intake thread, configured owner/channel, source event, and
original message text together.

The only network reads are GET-only: Slack `auth.test`, `conversations.replies`,
Director `/health`, `/v1/tasks/{id}`, and `/v1/primary/tasks/{id}/deliveries`.
Secrets and channel scope come from the running canonical container environment
in memory. There is no channel CLI argument or home-directory helper dependency.
Head, image, running state and localhost port are checked before and after reads.
Each thread is bounded to five pages/500 messages, responses to 2 MiB, collection
to 120 seconds plus at most one five-second blocked socket read. Docker
inspections each have a separate 15-second timeout. Reads are never retried.

`receipt.json` follows [the observations schema](schemas/director-slack-trial-observations-v1.schema.json).
It retains selected hashes, task/message identities, states, counts, rubric
checks, timing and unknowns. Failed lookups/collections stay visible. Existing
output directories are never overwritten. [The synthetic example](examples/offline-slack-trial-observations-v1.json)
illustrates a structural pass with remaining authority gaps.
Its digest identifies repository LF bytes. Recompute the actual local file
digest after checkout; newline conversion changes the bytes bound in live trials.

Original-body capture is **off by default**. `--private-dir <new-directory>`
creates a separate directory whose parent must exist, using current-user,
SYSTEM and Administrators ACLs on Windows, or mode 0700 on POSIX. No home helper
is loaded. Bodies containing detected configured credentials, scope values or
Slack identities are withheld even in private mode; their hashes remain.
Other originals are base64-preserved. Request parameters, authorization headers,
container environments and private paths are excluded from source references.
Do not publish private capture.

## Approval pause

An additive, idempotent startup migration adds nullable `owner_decision` to the
delivery table. A matching durable `approval_resume` command records `approve`
or `reject` against the existing request/projection binding. The command enum
`deny` maps to `reject`. Replays preserve the first evidence; legacy rows remain
null. The existing GET endpoint exposes the field through `receipts`, without
changing the endpoint handler. The migration has not been deployed by this work.

Each pause is `owner_decision_at - slack_ts` for a delivered approval row with
explicit `approve`, command identity, and a timezone-aware decision time. Waits
must lie within the input-to-terminal interval and cannot overlap. Multiple
approved waits are summed; no approval rows means zero pause. Reject, legacy,
malformed, missing or truncated evidence leaves pause/machine time null.
`machine_seconds = wall_seconds - owner_pause_seconds`. This arithmetic does not
independently attest historical signed approval scope.

## Qualification evidence chain

The collector requests each task with the read-only opt-in
`GET /v1/tasks/{id}?include_qualification_evidence=true` (the only permitted
API query). The typed `qualification_evidence` in that response is one bounded
`REPEATABLE READ` / `READ ONLY` snapshot of the persisted primary tables; the
ordinary task record and its digests are unchanged. Each trial row carries a
derived `qualification_evidence` object: `readback.state` is `collected`,
`incomplete` (truncated sections are named), `rejected` (task/schema mismatch,
duplicate attempt/assignment/effect/delivery/receipt identities, or attempts
that do not join a listed assignment) or `absent` (no opt-in response), with
the reason. The raw JSON is first validated through the product's own typed
`QualificationEvidenceReadback` model (`models.py`) in strict JSON mode, so
nested shapes, unknown keys at any level, boolean or string counts and naive
or malformed timestamps are refused by the model itself; the collector adds
only what the model cannot express: this task's binding and schema version,
no `NaN`/`Infinity` anywhere, timezone-aware `task_created_at <=
task_updated_at <= captured_at`, known and unique `truncated`/`absent`
sections, `complete` agreeing with `truncated`, absent sections empty,
elapsed time within `wall_seconds`, no section above `row_limit`, and for a
complete readback `task_scope_counters` equal to its own rows. Rejection
diagnostics name field locations, never payload values. Identity families
stay distinct in `chain`: `assignment_ids` and `assignment_sequences`
(AssignmentOutcome carries a sequence, not an ID), `attempt_ids` (primary
attempt digests, never external run IDs), `effect_ids` (external-effect
`effect_key_digest`), `delivery_ids` (Slack `client_msg_id`),
`approval_delivery_keys`, `status_receipt_ids` and settled
`provider_result_digests`. Slack notification keys must belong to the
delivery producer's families: `terminal`, `hold:sha256:<64 hex>` or
`approval:sha256:<64 hex>:sha256:<64 hex>` (152 characters); a malformed
family or digest rejects the readback, an owner decision is accepted only on
an approval row, and the generic identity bounds are not widened for them.
`timestamps` keeps request, completion (latest settled attempt row),
delivery (Slack acknowledgement) and readback distinct and lists every
unproduced edge with its reason under `unknown_reasons`; first-byte and
validation times have no producer. `lineage` reports provider routes,
transports and the attempt-ordinal count from real attempt rows;
`fallback_observed` stays `null` because no producer records fallback
linkage (every attempt is bound to its assignment's immutable provider).
`provider_effect_state` (`ZERO_PROVIDER_ATTEMPT`, `…STARTED_UNSETTLED`,
`…AMBIGUOUS`, `PROVIDER_RESULT_RECORDED`) and `external_effect_state`
(reservation, bound-unconfirmed, ambiguous or terminal disposition) are kept
apart from the coarse `effect_state` the scorecard consumes; a nonempty
run/contact/history list is never effect evidence. `usage.cost_basis` is
`authenticated_usage_table` (summed `actual_microusd`, with the reserved
budget reported separately), `provider_reported` (finite non-negative task
value; `inf`, `nan`, negatives and booleans are rejected) or `UNKNOWN`.
Validation before any derivation (`_validate_attempts`, `_validate_usage`):
every attempt event must join a listed assignment with the same provider and
sequence; the events of one attempt (assignment, ordinal) must share their
attempt, idempotency and budget digests, provider, format and transport;
event sequences are unique within 1..3, statuses are the producer's four, and
one digest never names two attempts. The latest valid event settles an
attempt, so an authenticated `SUCCEEDED` event 3 supersedes an earlier
`AMBIGUOUS` event 2, while any attempt whose latest event is still
`AMBIGUOUS` or `STARTED` keeps the whole task unsettled. Usage rows must join
exactly one valid attempt of the same provider, once; duplicated, orphaned,
provider-mismatched, boolean or negative usage rejects the readback. External
effects are aggregated across every reservation: one completed sibling never
hides an executed-but-unbound one (`EXTERNAL_EFFECT_AMBIGUOUS`), and a mix of
reserved-only and terminal rows is reported as
`EXTERNAL_EFFECT_MIXED_RESERVED_AND_TERMINAL:…`. `retry_observed` comes from
a second ordinal on the same assignment. A rejected, incomplete or absent
readback derives nothing: identities, sequences, routes, states and amounts
stay empty/UNKNOWN, the chain never falls back to the ordinary task record's
`assignment_outcomes` or to the separate deliveries endpoint, and an
incomplete one carries only `readback.partial_diagnostics` (`not_proof:
true`). That ordinary context (outcome sequences, providers and result
digests, delivery `client_msg_id`s) is reported only under
`unjoined_context` with a fixed note; it is not the validated snapshot join
and is never qualification evidence.

Status-only evidence is prospective. The readback exposes `captured_at` and
`task_scope_counters` (`thread_tasks` bound to the same Slack thread, this
task's `assignments`, `provider_attempts` event rows, distinct
`attempt_ordinals`, `started_attempts`, durable `status_receipts` and
`effect_reservations`; scope `task-and-thread-local/v1`) from the same
snapshot. `status_receipt_delta` compares two complete typed readbacks of the
same task, source event and engine, with the same runtime head/image and the
same declared configuration scope/key set/digest, and requires
before-capture < receipt-creation <= after-capture (timezone-aware), an exact
unique receipt join absent from the baseline, no decreased counter and
exactly one added durable receipt row. Consistency is not authentication:
only readbacks this same process obtained through the authenticated GET (the
signed synthetic test sequence) yield `proof: true`; a prior receipt supplied
with `--status-baseline <receipt.json> --status-receipt-id <id>` must first be
a schema-valid collected observation and then yields only
`baseline.observed_delta` with `proof: null` and the reason that no existing
mechanism authenticates a saved file. `provider_attempts_created` counts new
attempt ordinals, never settlement events; `deduplicated_receipts` is set only
from an observed replay, while `durable_receipt_rows` is the plain row count.
After-only collection keeps the created counts `null`.

`revision_binding` is a read-time identity: `configuration_digest` covers the
public fields resolved exactly as `Settings` resolves them (`ALLOW_PAID_RUNS`,
the three LangGraph primary switches as booleans with default `false`,
`LANGGRAPH_PRIMARY_POSTGRES_SCHEMA` with its declared default and
`INTAKE_ALLOCATION_PROFILE` lower-cased with the default profile and validated
against the allocation profiles), scope
`supplied-public-configuration-resolved-with-declared-defaults/v1`, with
`configuration_keys` and `supplied_keys` listed and values never emitted. An
omitted field equal to its default does not change the digest; a changed
profile, switch or schema does; secret, identity and unlisted keys are
excluded by construction; malformed values or unknown profiles refuse
collection. `inspect_runtime` derives the emitted `offline_configured` and
`paid_configured` flags through that same resolver (`1`/`true`/`yes`/`on`
and declared defaults, as `Settings` reads them), and `revision_binding`
re-derives its public flags from the resolver rather than copying the
runtime dict, so a spelling difference can never split the flags from their
digest. It is neither the native account/credential pin nor an
execution-time binding (`execution_time_binding: UNKNOWN` with its reason).
This block adds evidence; it changes no rubric check and cannot turn
`EVIDENCE_ONLY_NOT_QUALIFIED` into a pass.

## Record one row and stop on failure

```powershell
$env:PYTHONPATH = 'src'
python scripts/record-offline-trial.py `
  --receipt <receipt-directory>/receipt.json `
  --rubric docs/examples/offline-slack-structural-rubric-v1.json `
  --trial-index 1 --output <sequence-file>.jsonl
```

`--trial-index` is one-based. Record trials in their actual order and retain the
receipt/rubric with the JSONL. CLI output includes their file hashes. Rows use
exactly `OFFLINE_RUN_RECORD_FIELDS`: `RUN_RECORD_FIELDS` plus `simulation_label`.
The recorder and ten-run evaluator share `evaluate_run_record`; the latter keeps
its count and duplicate-ID requirements. Both schemas are checked, rubric
evaluations are recomputed, and changed rubric hashes or conflicting checks fail.

| Receipt evidence | Run fields |
| --- | --- |
| Accepted task or unresolved thread | `run_id` |
| Matched owner input, gateway-attested by the task's one append-only intake claim (its digests re-derived from the served intake, in a complete readback) | `actual_owner_event` |
| Recomputed predeclared rubric | `correctness_verified` |
| Global deduplication authority | `task_count`, otherwise null |
| Native terminal count and approved-pause arithmetic | `terminal_reply_count`, `wall_seconds`, `owner_pause_seconds`, `machine_seconds` |
| Stable health and complete delivery evidence | `ambiguous_effects`, `delivery_ambiguous` |
| Verified historical execution image | `runtime_sha`, `image_id`, otherwise null |
| Complete zero-call authority, labeled doubles, empty exposed attempt lists, stable offline runtime and paid disabled | `operational_provider_attempts: 0`, `actual_cost_usd: 0`, `cost_basis: simulation_zero_calls` |

**Zero-call-verified offline run → `actual_cost_usd` 0 with `cost_basis`
`simulation_zero_calls`.** This accounts for the simulated run only, not unrelated
engineering/review usage or actual billing. Empty exposed lists alone do not
establish complete operational calls. Unknown cost remains null; terminal text
continues to say actual cost is not recorded.

The current GET surfaces do not independently establish complete operational
attempts, global task deduplication or the historical execution image. Those
collector fields remain null, and the recorder preserves them. Current collector
receipts alone **cannot qualify a run**. Synthetic fixtures cover the fully
evidenced mapping, not production authority. The separate in-process check-10
collector order must supply that authority. Saved JSON never becomes a scored
observation here.

The recorder recomputes wall time from the input and terminal timestamps and
reuses the collector's approved-interval calculation for owner pause and machine
time. A numeric summary contradicting available source timestamps is refused.
Missing timestamps, incomplete readback or incomplete approval evidence leave
the affected timing fields unknown; a summary cannot fill that evidence gap.

Exit 0 means the row's measured facts qualify; exit 2 means a non-qualifying row
was appended and the sequence stopped; exit 1 means append was refused or
unavailable. Storage errors report an unknown append outcome: inspect the file
before retrying. The
recorder refuses further appends after any non-qualifying row. Diagnose and
retain that sequence before starting a separately identified one. It rejects
duplicate IDs, changed known image/head, an eleventh row, malformed/truncated
logs and an existing sibling `.lock`. The lock serializes read/check/append;
a crash leaves a visible lock for diagnosis. There is no force/reset/retry mode.

Historical trial 1, `D20-20260912-01` / `DIR-LG-9DF8291552`, remains
`FAILED_DELIVERY_READBACK`; its original outcome recorded **65.1816 s** wall time.
It is not rewritten or promoted. A qualification sequence starts at trial 2 only
after diagnosis and the required evidence is available.

## Validation

Collector/recorder fixtures use synthetic HTTP and files. Repository CI already
discovers these tests. For #201, run the delivery tests only on the assigned lane:

```powershell
$env:PYTHONPATH = 'src'
& C:/Users/edwar/director-orchestrator/.worktrees/langgraph-primary-cutover/.venv/Scripts/python.exe -m unittest tests.test_collect_offline_slack_trials tests.test_record_offline_trial tests.test_completion_scorecard -v
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/windows-postgres-test-lane.ps1 -Action Test -ContainerName director-lane-201 -HostPort 55442 -Python C:/Users/edwar/director-orchestrator/.worktrees/langgraph-primary-cutover/.venv/Scripts/python.exe -TestNames tests.test_primary_slack_delivery
```

Static source checks do not replace executed tests, exact-head review, CI or
security evidence. Deterministic policy and activation gates are unchanged.

Windows author validation on 2026-09-12 passed **98 collector, recorder and
scorecard tests in 1.979 seconds**, without skips, after the receipt-timing
correction. The unchanged primary delivery and schema code passed **33 tests**
in the earlier combined **126-test PostgreSQL run (50.022 seconds, no skips)**.
That disposable lane was removed. The negative controls confirmed that the old
recorder admitted contradictory duration summaries and failed the new missing-
evidence assertions. These are synthetic tests, not qualifying real Slack runs.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 10:24 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:25 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:55 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 21:06 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 22:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 23:25 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-22 | 00:15 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-22 | 01:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:38 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:17 UTC | END]] -->
