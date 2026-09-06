---
name: postgres-test-lane
description: "Postgres tests: use scripts/windows-postgres-test-lane.ps1, never docker run postgres. On Windows with Docker Desktop it starts, exports, tests against, and removes the disposable postgres:17-alpine lane on 127.0.0.1:55433 and sets LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN for the Postgres-backed unit tests. Without Docker, report the gap instead of improvising a database."
when_to_use: "Any request like: run the tests, run the full unit suite, run the Postgres suites, run tests/test_langgraph_postgres.py or a tests/test_primary_* module, a test fails with LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required, start a Postgres container for the tests, docker run postgres for the test database, reproduce the CI disposable PostgreSQL step locally, port 55433 is already in use, stop or remove the test database container, no Docker available for the Postgres tests."
argument-hint: "[Start | Stop | Status | Dsn | Test [-TestNames tests.module]]"
---

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:13 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:51 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 18:21 UTC | BEGIN]] -->

# postgres-test-lane - disposable PostgreSQL 17 for the local unit tests

## What this skill does

Points you at `scripts/windows-postgres-test-lane.ps1`, the only supported way to give the Postgres-backed unit tests a database on a Windows host with Docker Desktop. The script starts, inspects, exports, tests against, and removes one disposable `postgres:17-alpine` container pinned to the same digest as Compose and CI, published only on `127.0.0.1:55433`, trust auth for the throwaway `director_ci` role, no volume. It is not the primary database, records no activation evidence, never reads `.env`, and never starts Compose services.

Never hand-start a database for the tests: no `docker run ... postgres`, no unpinned image tag, no ad-hoc container name, no copy of the "Start disposable PostgreSQL (Linux)" step from `.github/workflows/ci.yml`, no host PostgreSQL install, and never point `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN` at the primary database or any other database.

This is local host tooling, not a shared provider workflow skill: the Director never selects it and it grants no authority.

## When you need it

These suites read `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN` and fail rather than skip when it is absent:

- `tests/test_entitlement_control_plane.py`
- `tests/test_langgraph_postgres.py`
- `tests/test_primary_authenticated_ingress_postgres.py`
- `tests/test_primary_backup_restore.py`
- `tests/test_primary_coordinator.py`
- `tests/test_primary_default.py`
- `tests/test_primary_effect_gateways.py`
- `tests/test_primary_effects.py`
- `tests/test_primary_staging_controls.py`
- `tests/test_primary_workload.py`

`tests/test_primary_backup_restore.py` also runs `pg_dump` and `pg_restore` inside the container named by `LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER`, so no host PostgreSQL install is needed.

## Run (Windows with Docker Desktop)

From the repository root, in the same PowerShell session that will run the tests (the variables are process-scoped):

```powershell
$env:PYTHONPATH = "src"
.\scripts\windows-postgres-test-lane.ps1 -Action Start
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\windows-postgres-test-lane.ps1 -Action Stop
```

Or let the script own the lifecycle; it removes the container even when tests fail:

```powershell
.\scripts\windows-postgres-test-lane.ps1 -Action Test
.\scripts\windows-postgres-test-lane.ps1 -Action Test -TestNames tests.test_langgraph_postgres
```

`-Action Start` exports `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN=postgresql://director_ci@127.0.0.1:55433/director_test?sslmode=disable` and `LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER=director-primary-test-pg`; the container carries the label `director-orchestrator.postgres-test-lane=true`. `-Action Status` inspects the lane; `-Action Dsn` re-exports the variables in a new shell (it requires a running lane the script created and never starts one). `-Action Test` resolves `.venv\Scripts\python.exe` relative to the repository root; in a worktree without its own `.venv`, pass `-Python <path to an existing venv python.exe>`.

## No Docker (Cursor Cloud, or any Linux or WSL host without a daemon)

Do not improvise a database: no `docker run`, no PostgreSQL package install, no pointing the DSN at an existing database, and do not mark the suites above as passed or skipped. Run the remaining suites, list the Postgres-backed modules that did not run, state that `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN` was unset, and report the gap as a coverage limitation in your handoff. CI runs those suites on every pull request.

## Top pitfalls

1. **`-Action Start` refuses when another container already publishes the port** (`Host port 55433 is already published by container(s): ...`). The script never removes a container it did not create. Remove the container you started by hand yourself (`docker rm --force <name>`) or pass `-HostPort <other port>`; the DSN follows the port.
2. **A same-name container without the `director-orchestrator.postgres-test-lane` label is refused** (`was not created by this script`). Remove or rename it yourself, or pass `-ContainerName`.
3. **The variables live only in the shell that ran the script.** A test run in another window fails with `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required`; run `-Action Dsn` there.
4. **A red Postgres suite with the DSN unset means the lane was not started**, not that the code is broken. Start the lane before diagnosing.
5. **The lane is disposable and loopback-only.** Never load real data into it and never reuse it as the primary database.

<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 18:21 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 20:51 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:13 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]] -->
