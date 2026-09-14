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

The collector accepts only this contract; a file cannot weaken its requirements.
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
bounded work, one task, no ambiguous effects/delivery, wall time below 60 seconds,
and attributable cost below USD 1. Machine time alone cannot satisfy wall time.

## Collect after the owner completes a trial

```powershell
$env:PYTHONPATH = 'src'
python scripts/collect-offline-slack-trials.py `
  --expected-head <full-observed-release-commit> --trial <thread-timestamp> `
  --rubric docs/examples/offline-slack-structural-rubric-v1.json `
  --output-dir <new-receipt-directory>
```

Repeat `--trial` at most ten times. One bounded paginated `conversations.replies`
read per thread supplies exactly one `ACCEPTED` reply from the bot authenticated
by `auth.test`. Missing, duplicate, foreign-bot or incomplete acceptance cannot
select a task. The task API must bind its ID, engine header, intake thread,
configured owner/channel, source event, and original message text together.

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
| Matched, gateway-attested owner input | `actual_owner_event` |
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
