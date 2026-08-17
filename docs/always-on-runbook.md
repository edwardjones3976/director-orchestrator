# Always-On Windows Runbook

This is the first deployment target. The host is a Windows desktop running Linux containers in Docker Desktop. Slack connects outbound through Socket Mode, so no router port-forward or public webhook is required.

## One-time host setup

1. Install Docker Desktop and Git for Windows.
2. In Docker Desktop, enable **Start Docker Desktop when you sign in** and use the WSL 2 engine.
3. In Windows power settings, disable sleep and hibernation while plugged in. Screen-off is fine.
4. Clone the private repository and run `scripts/windows-bootstrap.ps1` from PowerShell. The script securely prompts for the Slack `xapp-` and `xoxb-` tokens when they are not already present; input is hidden and the generated `.env` ACL is restricted to the current Windows account, SYSTEM, and local Administrators.
5. Initialize n8n at `http://localhost:5678`, import `n8n/workflows/director-intake.json`, inspect it, and activate it.
6. Add provider credentials to `.env` one at a time. Never place them in source control or Slack. Rotate any credential that was exposed in a message after the replacement has been installed locally and verified.
7. Restart the stack with `docker compose up -d`.

## Startup and recovery behavior

- Every service uses `restart: unless-stopped`.
- SQLite task state and n8n/PostgreSQL data live in named Docker volumes.
- On orchestrator startup, queued, planning, running, and reviewing tasks are reloaded.
- A recorded provider run is polled or reviewed again; it is not blindly redispatched.
- Slack events are deduplicated locally and again by a unique source-event key in SQLite.
- Tasks waiting for an owner decision remain paused after restart.

## Daily check

```powershell
docker compose ps
docker compose logs --tail 100 orchestrator slackbot
```

Healthy means the orchestrator is `healthy`, Slackbot and n8n are `running`, and `http://localhost:8080/health` returns `"ok": true`.

The health response also includes `usage_monitor`. Its `latest` list shows the most recent bounded check for each provider without exposing model replies or credentials. Periodic checks remain dormant unless both `USAGE_CHECK_ENABLED=true` and `ALLOW_PAID_RUNS=true`.

## Periodic usage-check operation

- Default cadence: once every six hours per configured provider.
- Minimum enforced cadence: 15 minutes, even if the environment is misconfigured lower.
- Provider checks run sequentially with a delay between them.
- A restart does not immediately reprompt a provider that was checked within the interval.
- Token counts are recorded only when returned by the provider.
- Claude Routine can confirm prompt acceptance but requires its configured completion callback for final usage metadata.
- Model-reported quota is advisory; use each provider dashboard for authoritative subscription or billing balance.

To pause the checks without disabling ordinary approved work, set `USAGE_CHECK_ENABLED=false` and restart `orchestrator`. To request one bounded cycle ahead of the normal six-hour cadence, call `POST /v1/usage-checks/run` with the local bearer secret. The endpoint still refuses to run unless both `USAGE_CHECK_ENABLED` and `ALLOW_PAID_RUNS` are true, and it never bypasses the 15-minute per-provider minimum.

The manual endpoint returns a structured schedule contract instead of an ambiguous acceptance:

- `202` + `status=accepted` when at least one configured supported provider is due. The body lists `scheduled_providers` and any `cooling_down_providers`, and only then schedules the background cycle.
- `200` + `status=not_due` when every configured supported provider is still inside the 15-minute minimum. No `cycle_id` is accepted and no background work is scheduled.
- `409` + `status=busy` when a cycle is already running or reserved, so callers cannot be promised records another pass cannot create.

Edge Oracle recovery note: isolated live-validation used to wait for three records after every accepted `cycle_id`. When providers were cooling down, that wait timed out and could close paid-run switches even though no probes were due. The shared schedule contract fixes that for every deployment that calls the endpoint; do not encode Edge Oracle committee rules into global Director governance.

## Scheduled recovery soak

The repository runs a bounded recovery probe every six hours and supports manual runs from GitHub Actions. Each scheduled job performs 12 independent cycles. Every cycle starts three separate Python processes against the same temporary SQLite database: one seeds an in-progress task with a recorded terminal provider result, one recovers and accepts it, and one verifies that a second recovery is an idempotent no-op.

The probe fails if persisted state disappears, the assignment advances more than once, an extra provider run or outcome appears, or the engine attempts to redispatch or poll the recorded terminal run. It uses deterministic fake provider and Director components, does not load repository secrets, and makes no paid model calls.

This distributed schedule is more useful for regression detection than one CI job sleeping for 24 hours: it exercises fresh GitHub runners at four points each day and leaves a separate pass/fail record for each observation. It is still not a substitute for observing the actual Windows host across sleep, Docker restart, network loss, or a real provider callback. Before production use, also run a shorter supervised host recovery drill with paid execution disabled.

## Backup

Before upgrades, stop new tasks and back up the three named volumes: `director-data`, `n8n-data`, and `n8n-db-data`. Store backups encrypted. Test a restore before treating the machine as production infrastructure.

## Safe update

1. Keep `ALLOW_PAID_RUNS=false` during the update.
2. Back up volumes.
3. Pull the reviewed repository branch.
4. Run `docker compose build --pull` and `docker compose up -d`.
5. Check health and run the dry-run Slack smoke test.
6. Re-enable paid runs only after the dry-run behaves as expected.

## Host outage

Provider-side background runs may continue while the desktop is offline. When the host returns, recorded Cursor and Gemini run IDs are polled again; Perplexity Agent API calls return synchronously. Claude Routine sessions require their completion callback or manual completion report. Never create a second work order merely because the host rebooted; first inspect the original task ID.
