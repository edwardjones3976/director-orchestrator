<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 18:05 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:55 UTC | BEGIN]] -->
# Primary lane staging worksheet

**Purpose:** bring `langgraph_primary.configuration_valid` from false to true on
this deployment. This is configuration staging only. No activation switch is
flipped and no provider is dispatched by these staging steps. They do not transfer
writer ownership; verify the actual owner from fresh generation-bound health and
the authoritative writer ledger before proceeding.

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
signal true only when every bound provider is listed in
`LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS` and the bound set satisfies the core
inventory rule in `primary_roster.core_inventory_ready`: `openai` present and
at least three of `openai`, `claude`, `cursor`, `grok`.

This means the binding map is core-pool-or-nothing for
`provider_activation_bindings_configured`: an empty map, a map without
`openai`, a map with fewer than three core lanes, a provider outside the
required roster, or any empty field leaves that signal false. A core-pool map
does not need every roster lane, but every lane it names must later reach
`LIVE_OK` on both surfaces, because the combined activation signal is AND-ed
across all configured bindings. It does **not** mean 54 values are required
for `configuration_valid`, and it does not block this staging pass while live
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

## After Pass 2 - activation staging inventory (names only)

Pass 2 ends with `configuration_valid=true` and nothing else. The control
script `scripts/windows-langgraph-primary-control.ps1` refuses every runtime
action until each name below is present, non-empty, and not a `REPLACE_WITH_`
placeholder; it never prints values. Record presence only. Values,
credentials, and account metadata never enter this document or any commit.

### Which control version this inventory describes

Two control-script versions are in play, and they differ in what they demand.

The names below are the ones required by the current accepted control script,
whose latest contribution record is `OpenAI Codex | GPT-6 | 2026-09-06 | 20:37
UTC`. That version adds `BootstrapDatabaseIdentity`, `SelectGeneration`,
`StageRuntime`, and `ValidateProviders` to the `-Action` set, refuses to build an
image for you, and verifies an exact-candidate image and a selected generation
before it does anything else.

This branch's own checkout still carries the earlier script (latest record
`OpenAI Codex | GPT-6 | 2026-09-06 | 09:27 UTC`), which has none of those
actions and builds the image itself. The always-required list and the
acceptance-mode list below are identical in both versions, so they can be read
against either. Everything under "Generation, image, and deployment identities"
comes from the current version only: it is not satisfied by, and not testable
against, the script in this checkout. Check the `-Action` ValidateSet of the
script you actually hold before following any of it.

Always required by the script:

- `LANGGRAPH_PRIMARY_POSTGRES_SCHEMA`, `LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE`
- Eight internal secrets, each at least 32 characters and pairwise distinct,
  checked by shape without printing: `LANGGRAPH_PRIMARY_POSTGRES_PASSWORD`,
  `LANGGRAPH_PRIMARY_OWNER_TOKEN`, `LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET`,
  `LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET`,
  `LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET`,
  `LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET`,
  `LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET`,
  `LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET`
- `LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON`
- `LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT`
- `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON`, a core-pool map per
  Section 2; an empty object counts as present but leaves
  `provider_activation_bindings_configured` false
- `LANGGRAPH_PRIMARY_REQUIRED_PROVIDERS`, which the script also checks for
  `openai` plus at least three core lanes
- `LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA` and `LANGGRAPH_PRIMARY_PINNED_TREE_SHA`,
  each 40 lowercase hexadecimal characters naming the accepted candidate. The
  control script rejects every other shape, empty included, and throws *"Primary
  repository authority must have exact commit and tree pins"* before it performs
  any runtime action. `authority_pins_valid` reading true on a deployment where
  both are empty describes a deployment that has pinned nothing; it is not
  permission to run the control script, activate, or dispatch without pins. The
  pins are also the snapshot verification input, and in the current control
  version they must equal the control checkout's own commit and tree
- `REPOSITORY_SNAPSHOT_HOST_PATH`, `REPOSITORY_SNAPSHOT_ROOT`,
  `REPOSITORY_ALLOWLIST`, `ALLOWED_STARTING_REFS`

Required by acceptance mode. `LANGGRAPH_PRIMARY_ACCEPTANCE_MODE` defaults to
`github-v1` when absent:

- `github-v1`: `LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON`
  and `LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON`
- `provider-v2` or explicitly selected `provider-v3-two-party`:
  `LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH`,
  `LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256`,
  `LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH`,
  `LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_DIRECTORY`

The v3 mode additionally requires signed owner adoption and qualified native
evidence under [two-party activation v3](two-party-activation-v3.md). This
inventory grants no adoption or activation. Signed registry/generation pins and
non-default selected paths are also examined by the legacy-mode refusal guard;
those guard references are not additional `$required` entries.

Not checked by the script, but required before live dispatch:

- An external-effect gateway credential. Slack uses `SLACK_BOT_TOKEN`. GitHub
  uses either `ORACLE_AUTOMERGE_GITHUB_TOKEN` or all three of
  `ORACLE_AUTOMERGE_GITHUB_APP_ID`, `ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID`,
  and `ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64`, never both forms, plus
  `LANGGRAPH_PRIMARY_EXTERNAL_EFFECT_PULL_NUMBER` of at least 1. The default
  of 0 fails every GitHub effect closed.
- `LANGGRAPH_PRIMARY_OPENAI_TRANSPORT`, `responses-api` when absent. On that
  default the openai lane is the Responses API adapter, and it is built only when
  `OPENAI_API_KEY` is set and openai authentication is observed
  (`primary_transports.py`). The key is therefore conditionally required by the
  selected transport rather than unconditionally required. The default is not a
  contradiction that blocks the openai lane: the host-bridge loop skips openai
  unless the transport is `native-codex-host`, which is what stops a host route
  from overriding the selected API adapter. `native-codex-host` additionally
  needs the host bridge lane and a ChatGPT login on the host, and it is the
  transport whose native capture the validation store knows how to normalise.

### Generation, image, and deployment identities

Current control version only. These names are not in the script's presence
check. They are *selected* by the control actions and re-verified on every later
runtime action, so they cannot be filled in by hand during this staging pass:

- `DIRECTOR_RUNTIME_IMAGE` and `DIRECTOR_RUNTIME_IMAGE_ID`, both the
  content-addressed `sha256:` identity of an already-built exact-candidate
  image. The script reads it from `docker image inspect`, requires the image's
  `org.director.candidate.commit` and `org.director.candidate.tree` labels to
  equal the control checkout's commit and tree, requires an empty entrypoint,
  and refuses to build the image for you.
- `DIRECTOR_RESOLVED_DEPLOYMENT_SHA256`, the digest of the fully resolved
  Compose graph, computed inside that same image with no network and no host
  mount by `primary_generation compose-digest`. Operational switches and the
  generation pointers are normalised out before hashing, so this identity pins
  the image, command, mounts and networks, not the execution switches.
- `DIRECTOR_ASGI_APP`, either `director_orchestrator.primary_staging:app` for
  the evidence-only service or `director_orchestrator.main:app` for the live
  one. `StageRuntime` writes the first; `Activate` writes the second.
- `LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256`, the owner-supplied 64-hex
  digest of the exact manifest bytes, and
  `LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH`, which `SelectGeneration`
  rewrites to the content-addressed copy it reserves under the data mount.
- `LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH`, taken from the manifest's
  `journal_path` and required to be a direct SQLite file in the data mount.
  Selection creates it exclusively with a single-row generation marker, so an
  interrupted selection is preserved rather than overwritten and a retry needs a
  fresh generation.

The manifest is a closed contract with exactly these fields: `version`
(`director-primary-generation/v1`), `activation`, `journal_path`,
`registry_path`, `registry_sha256`, `provider_bindings_sha256`,
`deployment_configuration_sha256`, `image_id`, `resolved_compose_sha256`. A
missing manifest is never treated as an implicit legacy generation, and
`provider_bindings_sha256` binds the exact
`LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON` bytes, so changing the
binding map after selection invalidates the generation.

The staging lifecycle, in order, all of it before any provider is dispatched:

1. `BootstrapDatabaseIdentity` is the only action that may establish the durable
   pre-acceptance database identity. It initialises no application or checkpoint
   schema.
2. `SelectGeneration` requires `provider-v2` or `provider-v3-two-party`. It
   stops and proves every intake surface stopped, reserves the manifest and
   journal, and then writes the six identities above.
3. `Preflight`, `Accept`, `Migrate`, `Backup`, `RestoreDrill` are unchanged in
   intent, but in either signed provider mode each one first re-verifies the
   selected generation.
4. `StageRuntime` starts the evidence-only app with `ALLOW_PAID_RUNS`,
   `LANGGRAPH_PRIMARY_ENABLED`, `LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED`,
   `LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED` and
   `LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED` written false and
   `LANGGRAPH_PARALLEL_KILL_SWITCH` written true, then requires `staged_ready`
   and `evidence_only` true with `primary_runtime_active` false.
5. `ValidateProviders` runs only against that exact staged service.

`ValidateProviders` also reads names the control script never checks:
`DIRECTOR_PROVIDER_VALIDATION_MANIFEST`, whose `generation`, `candidate` and
`tree` must equal the staged generation and the pins, and the capture verifier's
`DIRECTOR_PROVIDER_VALIDATION_ENABLED`, `DIRECTOR_PROVIDER_VALIDATION_CANDIDATE`,
`DIRECTOR_PROVIDER_VALIDATION_TREE`, `DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN`,
`DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY` and
`DIRECTOR_PROVIDER_VALIDATION_GENERATION`. Their values, the validation store's
own schema creation, and any provider canary are outside this staging pass and
are not authorised by it. A canary is a real provider invocation and may be
billable or consume subscription allowance even when it produces no effects.

Presence of every name changes no switch. `LANGGRAPH_PRIMARY_ENABLED`,
`LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED`,
`LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED`,
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED`, and `ALLOW_PAID_RUNS` stay
false and `LANGGRAPH_PARALLEL_KILL_SWITCH` stays true until the accepted
activation gates in `docs/langgraph-primary-operations.md` run in order.

## What this does not do

This pass does not configure provider bindings, start the host bridge,
authenticate providers, grant Grok or any other provider review authority,
enable the primary lane, change the sole writer, or permit paid/external work.
Those actions remain behind the separately accepted primary activation gates.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:55 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 09:50 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 18:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 08:23 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | END]] -->
