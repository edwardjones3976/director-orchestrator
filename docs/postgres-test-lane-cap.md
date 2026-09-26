<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:13 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-26 | 22:05 UTC | BEGIN]] -->
# Local PostgreSQL test-lane concurrency

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

Each lane records its container name, PID, and UTC start time in a slot file under
`%LOCALAPPDATA%\director-orchestrator\test-lanes\`. A global Windows mutex
serializes admission through container startup and serializes Stop. Docker's
labelled containers count across worktrees and accounts using the same daemon,
including containers created before slot tracking. Access or Docker-listing
errors fail closed; the script does not change mutex permissions. Separate Docker
daemons do not share a container inventory.

`Stop` releases the slot after successful container removal or confirmed absence.
A stale slot is reclaimed only when its PID is dead **and** its container no
longer exists. A live shell retains its slot even if its container disappears;
use `Stop` to release it. Failed or ambiguous removal retains the slot. Invalid
slot records fail closed and require investigation. A container name cannot be
admitted while its recorded PID is alive.

## Dispatcher integration still required

The author sandbox denies writes to `.agents/skills/postgres-test-lane/SKILL.md`.
Copy the operational guidance above into that skill's body under a concurrent
lanes section, preserving its frontmatter and existing attribution. This file
does not substitute for the work order's required skill update.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-26 | 22:05 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:13 UTC | END]] -->
