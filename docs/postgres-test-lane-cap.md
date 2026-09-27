<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:53 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:28 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:13 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-26 | 22:05 UTC | BEGIN]] -->
# Local PostgreSQL test-lane concurrency

## Opt-in verified TLS

Use `-Tls` with `Start` or `Test` to generate a one-day throwaway CA and a
server certificate covering `localhost` and `127.0.0.1`, using the selected
venv Python's `cryptography` package. The CA signing key stays in memory.
The certificate files and server key are created in a unique temporary
directory and mounted read-only; the container copies its server files to
a private directory, owned by postgres, with the key set to mode 0600.
`LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN` uses `sslmode=verify-full` and the CA;
`LANGGRAPH_RUNTIME_POSTGRES_TEST_CA` names that CA file. Connection values
are not printed. `Dsn` recovers the TLS configuration from the owned lane.

```powershell
.\scripts\windows-postgres-test-lane.ps1 -Action Test -Tls -ContainerName director-trusted-collect-test -HostPort 55434 -Python C:\Users\edwar\director-orchestrator\.venv-win\Scripts\python.exe -TestNames tests.test_completion_scorecard.TrustedCollectionLaneTests
```

The existing ownership, port and slot refusals still apply. Stop, Test's
finally block and failed startup remove the owned container and its temporary
TLS files. Test restores the caller's connection and CA environment. If
container removal fails, files remain for the retained container; rerun Stop.
Without `-Tls`, the lane keeps its existing non-TLS transport. The non-TLS
refusal regression and the TLS collection regression each skip clearly when
run in the other mode. CI adoption of `-Tls` is a follow-up; this change does
not modify `.github/workflows/ci.yml`.

The TLS collection test currently asserts PASS for 07, 12 and 17 using real
store reads and signed capture/adoption producers. It asserts UNKNOWN for
08, 11, 15 and 20 because that combined fixture does not yet produce a signed
approval/delivery lifecycle, paid attempts and correlated monetary settlement.
It is not the completed seven-check production-shaped happy path.

CLI captures with an executable attestation use `director-validation-capture/v2`.
The digest and version are signed; legacy captures retain their original
unversioned V1 bytes and remain verifiable. Missing executable evidence remains
UNKNOWN for product attestation, while a verified digest conflicting with a
valid pinned CLI release is a mismatch (FAIL). An absent or invalid descriptor
does not grant attestation.

`scripts/windows-postgres-test-lane.ps1 -Action Start` and `-Action Test`
default to a cap of two for local callers and four when
`$env:GITHUB_ACTIONS -eq 'true'`. Set
`DIRECTOR_TEST_LANE_MAX_CONCURRENCY` to a positive integer to override either
default for that caller. Each caller compares the total occupied slots with its
own cap; CI and local runs share the same inventory, not separate pools. Two
local lanes therefore leave room for two CI lanes, while a local caller waits
when two or more slots are occupied, including slots held by CI. A CI caller
waits at four occupied slots. This gives CI extra admission capacity without
preempting existing lanes or guaranteeing queue order. Concurrent lanes need
distinct container names and host ports. Existing label and port refusals still
apply.

Admission waits up to 45 minutes, prints progress every 15 seconds, and fails
clearly on timeout. `-SlotWaitSeconds <seconds>` changes this bound (1 through
86400). `-TimeoutSeconds` still controls database readiness only. Waiting never
reads stdin, including when stdin is redirected from an empty file.

Admission uses never-started Docker containers named
`director-test-lane-slot-<N>`, labelled with their owning lane container name.
Docker name uniqueness coordinates Windows and WSL callers on the same daemon.
Slot creation uses the lane's pinned image with `--pull=never`; no additional
image is downloaded. Separate Docker daemons have independent caps.

The lane container is created, but not started, before slot admission. Thus a
pending caller already has an owner container and cannot be reclaimed as stale.
All reservations (including higher numbered CI slots) and older labelled lanes
count against the caller's cap. Managed containers waiting for admission do not
run PostgreSQL or consume a slot. Contention can conservatively delay admission;
there is no queue-order guarantee.

Stop and Test cleanup remove the lane before releasing its reservation.
A reservation is stale only when a successful daemon inventory shows its owner
no longer exists. Removal uses immutable container IDs to avoid deleting a
replacement slot. Listing or removal failures fail closed. If a process dies
with its stopped owner container still present, use Stop for that lane; it is
not reclaimed automatically. Existing same-name lanes require explicit Stop
before a fresh Start, preventing replacement of another active caller.

Local test state uses `LOCALAPPDATA`, then `XDG_STATE_HOME`, then
`HOME/.local/state`, then `RUNNER_TEMP`, with `director-orchestrator/test-lanes`
appended. These local files are not admission authority.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-26 | 22:05 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:13 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:28 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:53 UTC | END]] -->
