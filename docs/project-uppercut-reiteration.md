# Project Uppercut Reiteration

Project Uppercut uses the same runtime through an isolated deployment profile. It must not rewrite, reuse the task database of, or share local control-plane state with the primary Director deployment.

## Isolation contract

- Populated profile: `.env.uppercut` (ignored by Git).
- Committed template: `.env.uppercut.example` (contains no credentials).
- Compose project: `project-uppercut-director`.
- Orchestrator: `127.0.0.1:18080`.
- n8n: `127.0.0.1:15678`.
- Dedicated Compose volumes and SQLite database.
- Repository allowlist limited to `edwardjones3976/Project-Uppercut`.
- Slack routing limited to the approved Project Uppercut channel and owner.
- Separate callback and internal service secrets. Provider credentials may be copied locally only through the explicit seeding switch; values are never printed or committed.

## Windows initialization

Run from the canonical `director-orchestrator` checkout:

```powershell
git switch main
git pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-uppercut-live-validation.ps1 -SeedProviderCredentialsFromPrimaryEnv
```

The first invocation creates `.env.uppercut`, generates new internal secrets, copies only the OpenAI, Claude Routine, and Cursor provider entries when requested, and then stops if dedicated Slack configuration is still absent. Install the generated `CLAUDE_CALLBACK_SECRET` in the trusted callback sender before live Claude work.

After the dry-run profile is healthy and provider-side limits are set, the paid invocation first proves the isolated task store is empty, starts only the API, runs fresh OpenAI/Claude/Cursor checks, and starts Slack intake only if all three pass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-uppercut-live-validation.ps1 -EnablePaidRuns
```

## Activation gate

1. Finish Gate 0 in the authoritative [LangGraph transition plan in PR #18](https://github.com/edwardjones3976/director-orchestrator/pull/18).
2. Prove the isolated profile leaves the primary `.env`, ports, containers, volumes, and task database unchanged.
3. Run one analysis-only OpenAI → Claude → Cursor task.
4. Run one reversible isolated-branch task that may create only a draft PR.
5. Review provider evidence, spend, callbacks, approvals, and recovery before routine use.

`-SkipProviderChecks` cannot be combined with `-EnablePaidRuns`. If readiness or startup fails, the script closes both paid switches, stops Slack intake, and recreates the API in the disabled state.

No agent may merge, deploy, publish, delete, purchase, expand access, or claim Unreal authority without the matching owner decision and Windows evidence.
