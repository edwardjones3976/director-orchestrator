<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:19 UTC | BEGIN]] -->
# Provider transport configuration

Use this runbook to separate configuration, installation, authentication,
packet qualification and live activation. A configured credential or installed
CLI does not satisfy the later gates. Keep secrets in the approved deployment
environment or protected files; report names and readiness booleans only.

## Windows host bridge

The Windows server and the orchestrator client have different environments.
Setting client variables in Docker does not start the Windows server.

| Process | Required configuration |
| --- | --- |
| Client | `LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT`, `LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET` |
| Windows server | Same bridge secret, `DIRECTOR_HOST_PROVIDER_COMMANDS_JSON`, `DIRECTOR_HOST_PROVIDER_BRIDGE_STORE`, `DIRECTOR_HOST_PROVIDER_POSTGRES_DSN`, `LANGGRAPH_PRIMARY_POSTGRES_SCHEMA`, workspace and Python |
| Server listening beyond loopback | `DIRECTOR_HOST_PROVIDER_BIND=0.0.0.0`, `DIRECTOR_HOST_PROVIDER_TLS_CERT_FILE`, `DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE` |

The default port is18765. The supported Docker client URL is
`https://host.docker.internal:18765/v1/provider/dispatch`; plain HTTP is accepted
only for loopback hostnames/addresses. Install the existing approved CA in the
actual client's trust configuration and verify certificate hostname matching.
Do not downgrade TLS to make Docker connectivity work.

Run the existing launcher from the reviewed checkout with explicit deployment
paths. These shell variables denote operator-selected paths, not defaults:

```powershell
$env:PYTHONPATH = Join-Path $reviewedWorkspace 'src'
& (Join-Path $reviewedWorkspace 'scripts/windows-host-provider-bridge.ps1') `
  -Action Preflight -EnvironmentFile $hostEnvironmentFile `
  -Workspace $reviewedWorkspace -Python $pythonExecutable
```

Preflight checks configuration and executable resolution without provider work.
An authorized `-Action Run` invocation starts the foreground server; it is a
state-changing operation, not a diagnostic. Preserve the durable claim store.
Use a supervised ordinary user process and sanitized logs. The host Postgres
DSN must reach the same authoritative writer database from Windows. If Docker
requires a database port publication, bind it to loopback and preserve the
existing image, project, volume and data; review that change separately.

Read-only diagnostics are executable resolution, path existence, listener
inspection, `GET /health`, and the supported authenticated
`challenge_host_provider_bridge(endpoint, secret)` helper. Never print secrets
or construct authorization headers in chat. Challenge checks TLS/HMAC and roster;
OpenAI also performs native authentication status. Claude/Grok roster entries
alone do not establish login or packet conformance. The Postgres writer fence is
checked before dispatch, not by health/challenge.

See `host_provider_bridge.py`, `host_provider_bridge_server.py` and the launcher.
Firecrawl and Microsoft365Copilot are not host-bridge lanes.

## Owner-only ChatGPT Slack alias

Use `CHATGPT_BRIDGE_ENABLED`, `CHATGPT_BRIDGE_SLACK_USER_ID`,
`CHATGPT_BRIDGE_CHANNEL_IDS` and `SLACK_OWNER_USER_ID`. The orchestrator needs
`OPENAI_API_KEY` and `OPENAI_CHATGPT_BRIDGE_MODEL`; the gateway does not receive
the OpenAI key. Both services need their documented bearer/HMAC configuration,
including the distinct `SLACK_GATEWAY_SECRET`.

This alias accepts the exact owner's messages in allowlisted channels. It ignores
bot messages and cannot be used to answer provider bot posts automatically. It
uses the API through the Director identity, not a ChatGPT subscription session.
The initial mention requests owner approval; the subsequent signed approval is
bound to the exact prompt. It cannot delegate, review, merge or deploy.
Inspect configuration presence and gateway readiness without sending a paid
test prompt. See [ChatGPT alias protocol](chatgpt-slack-alias-bridge.md).

## GitHub readback and merge authority

The current backend credential fields retain their Oracle names:
`ORACLE_AUTOMERGE_GITHUB_TOKEN`, or the complete alternative trio
`ORACLE_AUTOMERGE_GITHUB_APP_ID`, `ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID`,
`ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64`. Do not configure both alternatives.
These credentials can support GitHub readback independently of enabling Oracle
automerge. A developer's authenticated `gh` session is not automatically injected
into the service.

Keep `ORACLE_AUTOMERGE_ENABLED=false` and its kill switch engaged unless that
specific workflow is separately authorized. Credential presence, successful
readback or a mark-ready effect does not enable a generic review-to-merge loop.
Exact-head review, CI, security, actor independence, allowlists and approval gates
remain necessary. Inspect readiness booleans and authorized repository readback;
do not use a merge as a credential test. See `config.py`, `main.py` and
`primary_effect_gateways.py` for credential consumers.

## Firecrawl

The primary transport needs both `FIRECRAWL_API_KEY` in its execution environment
and a discoverable `firecrawl` CLI. `FIRECRAWL_API_URL` is the optional supported
transport variable. A host CLI does not satisfy container executable discovery;
use the reviewed runtime image containing the pinned CLI. Check executable
resolution/version and key presence without printing the key or making a scrape.
Qualification still requires the supported request/result contract and scoped
research task; installation is not live acceptance.

## GitHub Copilot

Use the installed native Copilot product's supported interactive OAuth login
under the actual execution identity when subscription login is intended. Do not
replace it with an unrelated GitHub API credential and call that subscription
qualification. Current direct discovery checks `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`
or `GITHUB_TOKEN`; therefore native OAuth login alone is not proof that this
backend discovery reports ready. The Windows bridge child allowlist carries
`GH_TOKEN` and `GITHUB_TOKEN`, not every credential from the parent environment.

Inspect native help/status locally using the installed version's supported
commands, keeping account identifiers private. Then require exact packet/result
qualification, bounded execution and accepted provider bindings. Merely adding
`github-copilot` to the command map does not supply those proofs. See
`primary_default.py`, `primary_transports.py` and the Copilot provider profile.

## Microsoft365Copilot

This is a separate Work IQ REST integration, not GitHub Copilot or a Windows CLI.
Its actual configuration is `MICROSOFT_365_COPILOT_DELEGATED_TOKEN_FILE`,
`MICROSOFT_365_COPILOT_TENANT_ID`,
`MICROSOFT_365_COPILOT_EXPECTED_SUBJECT` and
`MICROSOFT_365_COPILOT_EXPECTED_OBJECT_ID`. An organization administrator must
enable the required service and delegated access.

The token must be in the protected, bounded, non-symlink file accepted by the
runtime. Validation binds signature, issuer, tenant, audience, validity, subject,
object ID and delegated `api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask` scope.
An arbitrary bearer token or client secret does not substitute for this contract.
File existence/ACL checks are read-only diagnostics; do not dump or decode tokens
into reports. A Work IQ capability challenge creates remote evidence and must
be treated separately from local inspection. Current discovery holds the lane
unauthenticated until the required capability evidence is accepted; configuring
the four fields alone does not activate it. See `primary_transports.py` and
`primary_default.py`.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:19 UTC | END]] -->
