<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:55 UTC | BEGIN]] -->
# Primary lane staging worksheet

**Purpose:** bring `langgraph_primary.configuration_valid` from false to true on
this deployment. This is configuration staging only. No activation switch is
flipped, no provider is dispatched, and the legacy Director remains sole writer.

**Status of this document:** Pass 2 starts only after explicit owner approval.
The owner-authorized operator supplies the deployment-specific snapshot path.
Do not commit filled values, credentials, account metadata, or machine-specific
user paths.

## What is actually missing

Measured against the running deployment on 2026-09-04. Recheck the sanitized
`/health` response immediately before applying the change.

| Term | Current state | Meaning for Pass 2 |
| --- | --- | --- |
| `authority_root_configured` | false because `REPOSITORY_SNAPSHOT_ROOT` is absent | The only currently false input to `configuration_valid`; fix in Section 1. |
| `provider_activation_bindings_configured` | false because the binding map is empty | Separate live-dispatch readiness signal; leave false during Pass 2. |

`Settings.langgraph_primary_configuration_readiness()` computes
`configuration_valid` without provider activation bindings. Exact bindings are
enforced by `Settings.validate_langgraph_primary_configuration()` only when
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=true`.

Confirmed already correct, so no action: `ALLOW_PAID_RUNS=false`, the required
provider roster is nonempty and unique, and every other input to
`configuration_valid` is true. These observations are not authority to enable
primary execution or paid dispatch.

---

## Section 1 — the snapshot root

Two variables form the operational pair:

```dotenv
REPOSITORY_SNAPSHOT_ROOT=/repository
REPOSITORY_SNAPSHOT_HOST_PATH=<FILL IN: absolute Windows path to the snapshot>
```

`REPOSITORY_SNAPSHOT_ROOT` is the path inside the container. It must be
`/repository`, because `docker-compose.yml` binds the host snapshot to that
target read-only. `REPOSITORY_SNAPSHOT_HOST_PATH` is the existing host directory
bound to it.

`Settings.langgraph_primary_configuration_readiness()` checks that the
container-side root is nonempty. At use time, the primary runtime resolves the
path strictly, so the mounted directory must actually exist. Configuration
readiness alone does not prove snapshot availability or authority.

### Creating the snapshot

`scripts/windows-primary-snapshot.ps1 -Action Create` creates a clean snapshot
and refuses to overwrite an existing target. Its parent directory must already
exist, and the resulting snapshot must remain completely clean for verification.

```powershell
.\scripts\windows-primary-snapshot.ps1 `
  -Action Create `
  -Source <GITHUB-ORIGIN-SOURCE> `
  -Target <REPOSITORY_SNAPSHOT_HOST_PATH> `
  -ExpectedRepository edwardjones3976/director-orchestrator `
  -StartingRef main `
  -ExpectedCommit <FILL IN: accepted 40-hex commit> `
  -ExpectedTree <FILL IN: matching 40-hex tree>
```

The source must have a GitHub URL as its `origin`. Many working checkouts on
this machine use a Cursor mirror as `origin`. A dedicated GitHub-origin source
clone exists, but its local `main` may be stale; it is not usable until the
remote, clean state, exact local `main`, commit, and tree have all been verified
and, when necessary, updated without rewriting unrelated work. Do not
substitute a merely similar checkout.

If GitHub-origin selection must work from a mirrored checkout in the future,
add an explicit remote-name parameter to the snapshot script in a separate,
reviewed activation-path change. Do not rewrite a working checkout's remote to
bypass the current check.

---

## Section 2 — deferred provider activation bindings

The configured roster currently has nine provider lanes. Each
`ProviderActivationBinding` contains six required nonempty identifiers:
`workspace_id`, `provider_version`, `app_version`,
`runtime_adapter_version`, `workspace_version`, and `binding_version`.
`Settings.primary_provider_activation_bindings()` validates the object, and
`Settings.langgraph_primary_configuration_readiness()` reports the binding
signal true only when its provider keys exactly equal the required roster.

This means the binding map is all-or-nothing for
`provider_activation_bindings_configured`: a partial map or any empty field
cannot make that signal true. It does **not** mean 54 values are required for
`configuration_valid`, and it does not block this staging pass while live
dispatch remains false.

Do not paste an empty skeleton or plausible values into `.env`. These fields are
configuration identifiers, not attestations and not activation evidence. The
identifier regex checks only shape and length; it cannot determine whether a
matching string is a credential. Only independently sourced, nonsecret IDs and
version metadata belong here. Credentials stay in the approved secret store.

Before a later live-dispatch gate, generate the complete map from a reviewed
provider inventory and refuse unknown values. Actual activation remains a
separate Postgres evidence chain for both the app and runtime-adapter surfaces:
`INSTALLED`, `AUTHENTICATED`, `WORKSPACE_BOUND`, `DRY_RUN_OK`, then `LIVE_OK`.
Configuration presence cannot advance any of those stages.

---

## Section 3 — deferred host provider bridge

Host-bridge command, claim-store, and writer-fence configuration is not an input
to `configuration_valid` while primary and live dispatch are disabled. Leave it
unchanged during Pass 2.

The bridge preflight resolves configured executables and invokes no provider.
Run it later from the exact authorized repository root with the complete
invocation:

```powershell
.\scripts\windows-host-provider-bridge.ps1 `
  -Action Preflight `
  -EnvironmentFile .\.env `
  -Workspace . `
  -Python (Join-Path (Get-Location) '.venv-win\Scripts\python.exe')
```

Executable paths in the future command map must be derived on the deployment
host, for example with `(Get-Command claude -ErrorAction Stop).Source`; do not
commit an owner's home-directory path or a literal placeholder argument.
Preflight also fails closed on incomplete bridge settings, secret collisions,
unsupported providers, and unresolvable executables. It still invokes no
provider.

### Claude authentication status

The supported command `claude auth status --json` returned exit code 0 and
`loggedIn=true` on 2026-09-04. It returned the same result when launched with
only the operating-system variables allowed by the bridge and without
`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` in that environment.

Therefore, zero-length fields observed in an internal credentials file are not
an authoritative sign-out signal and do not block the Claude lane. Do not edit
the credentials file or run interactive `/login` while the supported status
command is green. Raw status output may contain account identifiers; record only
the exit code and `loggedIn` boolean.

Claude authentication is not needed to make `configuration_valid` true and is
not tested by the executable-only bridge preflight. Immediately before the
first real Claude dispatch, rerun the supported status command under the exact
Windows identity that will host the bridge. If that check is no longer green,
stop for an owner-controlled credential action rather than guessing from files.

---

## Pass 2 — apply

1. Capture the sanitized `langgraph_primary` health block. Build a rollback
   payload containing only whether each snapshot variable existed and its prior
   value, then protect that payload with Windows DPAPI for the deployment
   identity. Never write an unprotected rollback payload or a plaintext copy of
   the entire `.env` file. This is an operator-performed procedure: no committed
   repository helper currently implements the required DPAPI payload.
2. Create and verify the exact snapshot. Update the environment through a
   same-directory temporary file and atomic replacement, changing only
   `REPOSITORY_SNAPSHOT_ROOT` and `REPOSITORY_SNAPSHOT_HOST_PATH`. Preserve the
   target file's access controls. Leave
   `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON` and all bridge
   variables unchanged. Do not use an in-place whole-file rewrite path for this
   operation; no committed repository helper currently implements these atomic
   update requirements.
3. Keep `LANGGRAPH_PRIMARY_ENABLED`,
   `LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED`,
   `LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED`,
   `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED`, and `ALLOW_PAID_RUNS`
   false. Restart only the orchestrator service.
4. Do not run a provider or advance activation evidence in this pass.

## Verification

- Diff the sanitized `langgraph_primary` health block before and after. Expect
  `authority_root_configured=true` and, if the other measured inputs remain
  true, `configuration_valid=true`.
- Expect `provider_activation_bindings_configured=false`; that is valid and
  intentional for this pass.
- Confirm `paid_runs_enabled=false`, `langgraph_primary.enabled=false`,
  `live_provider_dispatch_requested=false`, `primary_runtime_active=false`, and
  `langgraph.legacy_sole_writer=true`.
- Confirm the container is healthy, the snapshot is mounted read-only, and no
  provider invocation or activation-evidence write occurred.
- To roll back, decrypt the DPAPI payload under the same deployment identity,
  atomically restore or remove only the two snapshot variables according to
  their recorded prior state, restart the orchestrator, and verify the sanitized
  health block matches the before-capture.

## What this does not do

This pass does not configure provider bindings, start the host bridge,
authenticate providers, grant Grok or any other provider review authority,
enable the primary lane, change the sole writer, or permit paid/external work.
Those actions remain behind the separately accepted primary activation gates.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:55 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:50 UTC | END]] -->
