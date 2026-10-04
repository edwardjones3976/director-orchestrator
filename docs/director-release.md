# Director R1 preparation, doctor and sibling rehearsal

`scripts/director-release.py` inspects pinned inputs, prepares a Compose artifact,
recovers an interrupted local receipt without replay, and produces a command plan
for a sibling Compose rehearsal. It executes no activation or provider command.
Every result reports `release_ready: false`; a generated plan is not a completed
drill, accepted generation, paid admission or deployment.

**A2 integration:** W2/#313 owns the OS-root locator and installed descriptor
reader. This runner consumes its `owner_root`, `release_descriptor` and
`read_descriptor` API directly. The focused suite loads the production A2 source
in temporary owner roots, replacing only its OS account lookup. A missing
A2 locator or installation fails with `trusted_descriptor_consumer_unavailable`;
missing or invalid owner evidence also blocks before profile reads, platform
probes or preparation writes. The tests perform no real owner installation.

## Descriptor and protected inputs

The closed schema is
[`schemas/director-release-descriptor-v1.schema.json`](schemas/director-release-descriptor-v1.schema.json).
The accompanying [example](schemas/director-release-descriptor-v1.example.json)
uses illustrative paths and zero hashes; it grants no authority and cannot prepare
a release until replaced with verified inputs and an owner's exact-byte pin.
Unknown fields, duplicate JSON keys, and non-finite numbers are rejected. Supply
a protected descriptor with these inputs; no secrets belong in the descriptor:

| Field | Meaning |
| --- | --- |
| `schema_version`, `release_id` | `director-release-descriptor/v1`; unique local preparation name |
| `source`, `candidate` | Absolute checkout containing this runner; exact Git commit and tree |
| `compose_project`, `image_id` | Existing project image alias and immutable `sha256:` image identity |
| `runtime_env`, `bridge_env` | `{path, sha256}` pins for protected environment files |
| `compose_files` | Ordered `{path, sha256}` pins passed explicitly to Compose |
| `generation_manifest`, `validation_manifest` | `{path, sha256}` pins; no generation is created or reserved |
| `bridge_url`, `bridge_ca` | HTTPS configuration-digest endpoint; pinned CA file |
| `snapshot` | Absolute path, repository, starting ref, remote, exact commit/tree |
| `reviewers` | Planned independent and security actor names, checked against configured allowlists |
| `executables` | Existing absolute `python` and `powershell` paths |
| `output_directory` | Existing protected directory disjoint from the checkout and snapshot, including ancestors |
| `timeout_seconds` | Shared whole-doctor subprocess deadline, 1-900 seconds |
| `rehearsal` | Optional sibling descriptor pin, acceptance-artifact pin, fresh backup path and loopback host port |

The descriptor and executables are operator inputs, authenticated for preparation
only by the owner's exact-byte pin described below.
The actor-name check is not a review verdict, provider-family check, or signed
acceptance check. Runtime and bridge profiles must already contain every
prerequisite; this script neither defaults missing values nor edits profiles.
The bridge must already be running with its explicit pinned process environment.

The rendered Compose file can contain credentials interpolated from the runtime
environment. Its output directory must already have appropriate owner-only
permissions. Creation requests mode `0600` on POSIX; it does not create or modify
Windows ACLs. Do not commit or post the rendered file. Reports and journal records
contain digests, fixed reason codes, and check names instead of raw subprocess
output, DSNs, environment values, or HTTP bodies.

## One owner descriptor

Start the runner with Python `-I -B`. It refuses ordinary interpreter startup
before importing filesystem modules: an unverified checkout cannot supply a
shadow `argparse`, `ctypes`, `uuid`, or other imported dependency. Isolation
excludes the script/current directories, user site and `PYTHON*` overrides from
Python's startup search path. After owner authentication and the exact clean
source check, the runner adds only that checkout's `src` for candidate helpers.

The runner uses A2's same OS account anchor (Windows Known Folder API or POSIX
effective-UID account record) without executing a checkout locator. It locates the
OS account's
`.director-scorecard` directory (Windows `SHGetKnownFolderPath(FOLDERID_Profile)`;
POSIX `pwd.getpwuid(os.geteuid()).pw_dir`). The runner loads the exact source
bytes of `launcher.py` from that directory without reading or writing bytecode,
then uses only the installed copy's `release_descriptor()` and
`read_descriptor(path) -> (whole_file_sha256, document) | None`. Both copies must
agree on the fixed root and descriptor path. Missing installations never fall
back to a checkout reader. All path components must be free of symlinks and
Windows reparse points before the installed file is loaded or evidence is read.

Add this mapping to the existing `director-cli-release-descriptor/v1` owner
descriptor, retaining its evaluator, CLI releases and other pins:

```json
"release_preparations": {
  "r1-final": "<64 lowercase hex: SHA256 of exact final preparation JSON bytes>",
  "r1-sibling": "<64 lowercase hex: SHA256 of exact sibling preparation JSON bytes>"
}
```

The keys must equal the existing preparation `release_id` fields. Both the final
and sibling need entries. A missing, malformed or different digest refuses.
The complete preparation file already binds its candidate, image, executable
paths, protected profiles, output location and sibling reference; these values
need not be duplicated in the owner mapping. The preparation schema is unchanged.

No evaluator environment, nonce or pending scorecard receipt is created or
consumed. `HOME`, `USERPROFILE` and all `DIRECTOR_SCORECARD_*` claims cannot select
owner authority. This checks the owner's configuration under the existing
protected-file trust boundary; it does not authenticate a scorecard run or
protect against an actor who can rewrite the reviewed runner or owner installation.

The W5 bootstrap, interpreter and dependency search paths must themselves be
trusted. This is an owner-pin and source-identity boundary, not an OS sandbox.
Only captured installed-launcher bytes execute during bootstrap. Its real A2
reader authenticates the exact preparation descriptor before its candidate is
trusted. The checkout commit/tree and cleanliness are then checked before reading
the checkout schema or importing candidate helpers. No checkout
`scorecard-launcher.py` is executed. Doctor stops on an owner or source failure;
each checkout child and helper also rechecks those boundaries before use. The
owner must protect these files against concurrent writers during execution.

In the already-approved setup slot, the owner installs `launcher.py` from the
reviewed evaluator commit and verifies its exact bytes, installs the single
`release-descriptor.json` including these pins and the finalized R1 bindings,
and places the final/sibling preparation JSON at the agreed protected paths.
The preparation files may live in the same `.director-scorecard` directory.
Compute their pins from final bytes; subsequent formatting changes invalidate
them. Existing protected profiles, output directories, snapshot and image are
prerequisites. The owner also verifies headroom and approves per-call caps.
This code performs none of those installation or authorization actions.

## Commands and exit status

From the candidate checkout, with the repository virtual environment:

```powershell
python -I -B scripts/director-release.py doctor --descriptor C:\protected\r1-descriptor.json
python -I -B scripts/director-release.py prepare --descriptor C:\protected\r1-descriptor.json
python -I -B scripts/director-release.py resume --descriptor C:\protected\r1-descriptor.json
python -I -B scripts/director-release.py rehearsal --descriptor C:\protected\r1-descriptor.json
```

These are templates for the reviewed owner setup and require the A2 installation
above. `doctor` performs read-only configuration, Git, Docker image/configuration,
bridge GET, and database SELECT checks. It does not start Docker services, pull
images, create database tables, reserve attempts, or call a model. It currently
exits **2 even when `preparation_ready` is true**, because release authority remains
unwired. Read `checks` to distinguish a diagnostic failure from the separately
reported capability dependencies.

`prepare` runs the same diagnostics and writes only the local preparation
artifact and its journal. It exits 0 only for that completed operation. `resume`
on a complete receipt verifies pinned input and output bytes, without repeating
platform checks. It is **not a fresh readiness check**; run `doctor` explicitly
when fresh observations are required. `resume` with no prior intent behaves like
`prepare`.

After the owner binding passes, the doctor verifies:

1. A clean source checkout at the exact descriptor commit/tree and an already
   present project image with matching source labels and immutable image ID.
   The actual Git child disables fsmonitor, hooks and untracked-cache execution;
   executable clean/smudge/process filters and submodule checkouts are refused
   before status. It does not change the operator's Git configuration.
2. Matching runtime, bridge, generation, and validation selectors; validation
   expiration inside the existing 24-hour limit. This is a selector check, not
   full fresh-generation authority validation. The snapshot repository/ref and
   effective execution commit/tree must match the runtime profile: explicit
   project-execution pins take precedence, otherwise candidate pins apply.
   Selected generations require an explicit supported acceptance mode, its
   required paths and a correctly formed registry/adoption digest. A missing
   mode defaults to `github-v1` in the control script and is refused here.
3. The existing `resolved_deployment_digest` contract, including the Slack
   gateway's exact image, explicit `pull_policy: never`, and the nonempty read-only
   `/repository` snapshot mount.
4. `windows-primary-snapshot.ps1 -Action Verify` and the planned reviewer
   allowlists, without adding actors or issuing acceptance.
5. `windows-host-provider-bridge.ps1 -Action Preflight`, followed by the existing
   authenticated GET `/v1/provider/configuration-digest`. Its running lane digests
   and `pinned_environment` must match the supplied validation manifest. The same
   endpoint's closed `configured_selection` object must match the selected
   candidate commit/tree and generation, canonical workspace digest, and exact
   validation-store configuration digest. These values are captured from the
   serving process after pinned environment and service initialization. Neither
   raw paths nor DSNs are returned. Missing, old or mismatched selection metadata
   fails closed. This proves configured selection, not generation acceptance or
   live database-instance identity.
6. Existing database identity and instance UUID through `database_binding`
   with its default `bootstrap=False`, then a read-only query for occupied
   validation slots. Every observed slot blocks preparation; successful,
   unfinished, and ambiguous slots are never treated as unused.
7. The existing read-only `live-dispatch-preflight.py` checks a hypothetical live
   target: only that validator child's environment enables its four target
   switches and disables offline simulation. Protected profiles, Compose
   rendering, the parent environment and the emitted sibling plan retain their
   selected unpaid settings. Real validator failures remain failures. This is
   followed by exact-byte
   rereads of the descriptor itself and all pinned files before writing anything.

Platform children use closed stdin, a shared deadline, bounded captured output,
and no shell. Raw probe inputs go through temporary stdin, not command-line
arguments. Inherited provider keys, Git overrides, `COMPOSE_FILE`, and
`DOCKER_HOST` are not forwarded. The doctor resolves the configured Docker context,
refuses remote/TLS endpoints, and pins its local Unix socket or Windows named pipe
and daemon ID. Later Docker checks use that endpoint with ambient Docker selectors
removed. Windows Program Files
variables are retained because Docker discovers its Compose plugin there. The descriptor's
protected profiles and explicit executable paths remain trusted local inputs.

The current host doctor requires host-readable profile paths and a database DSN
whose canonical identity matches the manifest. Container-only paths or a host
port alias producing a different storage identity fail; the script never
rewrites those inputs or claims the identities are interchangeable. A fresh
database without its existing validation tables also fails. These conditions
need the bootstrap follow-up below before first-generation preparation can pass.

## Resume and uncertain work

The output directory receives `<release_id>.activation.compose.json`,
`<release_id>.jsonl`, and an OS-held `<release_id>.lock`. One writer acquires the
lock, writes and flushes an intent bound to preparation, whole owner descriptor
and installed-launcher digests with the intended output digest, creates the artifact
exclusively, verifies its bytes, and flushes a digest-bound completion receipt.

If interruption leaves a complete artifact whose bytes match the flushed intent,
`resume` can append the missing receipt after verifying every input. It does not
rerun the doctor, render again, overwrite the artifact or execute any effect.
The installed launcher bytes and complete owner descriptor are reread before
completion. Every field and whitespace byte in the owner descriptor participates
in the binding. A new process or `Release` instance must match both owner
descriptor and installed-launcher digests against the intent and receipt;
rotation, even with the same evaluator and preparation pins, remains held.
Old journal records without `owner_descriptor_sha256` or `owner_launcher_sha256` are refused without
migration or replay. Both sibling and final bindings must come from the same
owner descriptor bytes.
An absent, partial or different artifact after intent, a truncated or changed
journal, an existing unrecorded output, or changed inputs requires reconciliation.
None is automatically replayed or overwritten. The journal hashes detect accidental
changes; they do not authenticate an operator or authorize a release. This
journal covers **only Compose preparation**. Symlinks and Windows reparse points
are refused along the complete input/output paths. It neither imports old provider
receipts nor resolves any uncertain paid or GitHub action.

## Sibling rehearsal interface

The final descriptor's optional `rehearsal` object pins a second complete
descriptor and its acceptance artifact, and names `host_port` and `backup_path`.
The sibling must have the same candidate/image, a different project/release ID,
generation and database instance, separate protected profiles and output directory,
and explicitly unpaid switches. It cannot contain another rehearsal descriptor.
Its image, database identity and generation must already have been provisioned
through the existing approved controls; this interface does not bootstrap them.

`rehearsal` verifies the available final database through the existing read-only
probe. The complete backup/restore rehearsal requires the stock
`langgraph-primary-db` service and the orchestrator's corresponding container-local
DSN, database and user; an external-only database has no supported backup contract.
The sibling's previously provisioned identity is **DEFERRED**, with
`preparation_ready: false`, until its planned database-only start succeeds.
`StartDatabase` uses the pinned Compose inputs and cached image, no build/pull or
dependency startup, and a 120-second readiness bound. Immediately afterward,
before any existing control action, a read-only probe in the candidate image
checks the actual database identity/instance against the sibling manifest and
the observed final identity. A host read-only query also requires unused
validation slots. These checks repeat before subsequent controls. They never
bootstrap an identity or validation schema. A failed start or proof stops the
plan for reconciliation; planning itself starts nothing and reports no DB PASS.

The runner compares the resolved graphs **and all actual final-project containers**,
including stopped containers, using Docker inspection. Their bind sources, volume
names, attached networks and configured published ports participate in isolation.
The launcher rechecks this inventory's digest before each action; a replaced
container or changed resource requires a new plan. It refuses shared/external
volumes or networks, driver options, host namespaces or Docker API socket
delegation, reused effective Slack tokens/channels (including Compose overrides),
non-loopback ports and final-project or sibling-internal port collisions. Writable sibling
binds cannot overlap either release's checkout, snapshot, output, authority root,
descriptor, pinned inputs or selected process-environment file, or any final
Compose or observed container bind. Docker socket binds, parent
directory binds exposing the selected/default engine socket, and named-pipe
mounts are refused even when marked read-only; read-only filesystem access does
not make the Docker API read-only. Effective sibling Compose paid/live switches must remain false
and its kill switch true even if an override replaces profile values. The backup
path must be new and directly inside the sibling output directory. Each output
directory must also be disjoint from the other release's protected paths,
including its snapshot. The health mapping must use TCP (omitted protocol means
TCP); a UDP mapping is insufficient.

The supported complete rehearsal graph is orchestrator, slackbot, n8n,
`langgraph-primary-db`, and optional `n8n-db`. Slackbot may depend on both
orchestrator and n8n. Extra services, alternate gateway commands, auxiliary
startup commands, external gateway routes and unsupported n8n/Postgres settings
are refused. Both application services require explicit unpaid/live/kill
switches; the separate ChatGPT bridge must be disabled. All startable images
must be immutable, already cached and explicitly `pull_policy: never`. Builds
are forbidden on auxiliary services; the existing control uses `--no-build` for
the application starts. Auxiliary n8n/Postgres images must use the sanctioned
image repositories without command/entrypoint overrides. Every service must be
a singleton: both `scale` and `deploy.replicas`, when present, must be integer 1;
global replication is unsupported. Health commands are limited to the checked-in
orchestrator HTTP check and stock Postgres readiness checks, or disabled/absent
checks. Image-inherited health commands are checked too, including when Compose
overrides only their timing. Arbitrary healthcheck commands are refused.

The sibling project must have no existing containers, named networks or prior-image record.
n8n state must be fresh: only new, project-scoped named volumes or ephemeral
image state are supported. Reused databases/workflows, bind-mounted n8n state
and alternate database hosts are refused because Director paid switches cannot
constrain arbitrary stored workflows. These restrictions require explicit sibling
Compose overrides for the stock graph's pull policies and gateway unpaid flags;
the runner never rewrites the production graph or profile. Previously provisioned
isolated primary database storage remains subject to the existing identity checks.

Offline rehearsal supports only `provider-v2` and `provider-v3-two-party`, exactly
as `PrepareOfflineHandoff` does. `owner-local-evidence-v1` can satisfy doctor
selector checks but cannot emit this offline plan. The sibling profile must also
set `DIRECTOR_PROVIDER_VALIDATION_ENABLED=false` before the plan is produced.

The result contains executable launcher `argv` arrays and separate inspection-only
`control_argv` arrays for the database-only Compose start and existing control
script, in gate order:
`StartDatabase`, `Preflight`, `Accept`, `Migrate`, `Backup`, `RestoreDrill`, `StageRuntime`,
`PrepareOfflineHandoff`, `ActivateOffline`, `Health`, `Rollback`, `Health`.
The restore proof precedes activation as the existing acceptance chain requires.
The separately pinned acceptance artifact is passed to `Accept`; the runner
never considers its mere existence or a successful plan to be acceptance.
Each executable `argv` invokes a fixed Python launcher which actually replaces
the control child's environment with the emitted allowlist. The result declares
`environment_mode: replace`; it is not an overlay or a promise that JSON null
unsets a variable. This binds `cwd`, the local `DOCKER_HOST`, `COMPOSE_FILE`,
`COMPOSE_PATH_SEPARATOR`, `DIRECTOR_HOST_PORT` and unpaid switches. Ambient
`DOCKER_CONTEXT`, TLS/certificate/config selectors and provider keys cannot reach
the control child. Before each action the launcher rechecks exact bytes for both
descriptors, the owner descriptor and installed launcher, both sets of immutable
profile/Compose/manifest/CA inputs, and the acceptance artifact. The sibling
runtime profile retains its original byte pin until `StageRuntime` changes it.
Thereafter each step requires the full expected environment-map digest, with
only the existing successful `StageRuntime`, `ActivateOffline` and `Rollback`
switch edits included. Formatting changes from the writer are allowed; an edited
DSN, credential, image, switch or other value is not. Pins are checked again
immediately before invoking each action. Inputs are not silently re-pinned.

Before each step the launcher also rechecks the pinned source commit,
tree and cleanliness, both before probes and immediately before launching the control
script. It refuses executable Git filters and submodule checkouts. The separate
snapshot verifier also refuses its own local executable filters and submodules,
and disables fsmonitor and hooks using an explicit `/dev/null` hook path for its
cleanliness check. These checks retain the trusted
operator model; they are not an atomic defense against concurrent filesystem replacement.
The launcher also rechecks daemon identity and every cached image ID; before the
first step it rechecks fresh project networks and project/n8n
state. Any prior-image record must remain within the verified candidate image,
so rollback cannot select an uninspected older image. Execute `argv` in order;
running `control_argv` directly discards these protections and is unsupported.
Activation and rollback each have a 260-second child timeout, allowing their
75-second lease wait or 65-second drain plus the full 120-second health wait
and command overhead. Other child actions retain a 140-second bound; the database
proofs each have a separate 30-second read-only subprocess bound. Plans whose
serialized launcher or control argument list exceeds Windows' 32,767 UTF-16-unit
limit (including quoting and the terminating NUL) are refused before emission.
Shorten the protected paths or inherited command search path and generate a new
plan; the runner does not create an unpinned fallback launcher file.

Claude's assigned W6 operator executes this plan only within the separately authorized
setup/rehearsal slot, preserving the existing control gates and durable command
receipts. The plan carries a total bound of 1,499 seconds; **the operator must
enforce that bound**. Timeouts and uncertain outcomes stop the drill for
reconciliation without replay. This command emits `PLANNED` and `executed: false`;
it does not execute the plan, enforce a running drill's deadline or attest a
successful sub-25-minute rehearsal.

The sibling rollback/restore drill satisfies the owner's #81 rehearsal scope.
The runner contains no path that recreates the final container. Once the final
container is activated for evidence collection, the operator must keep its exact
identity until the final scorecard and owner acceptance; no sibling command may
be repointed to it.

The owner's final-window ruling on #81 (comment 5957888004) requires all provider
lanes: Antigravity, Perplexity, Claude/Cursor implementation, Grok adversarial
review, OpenAI integration/acceptance, and one non-gating Copilot hygiene pass.
Lane reachability gaps must be resolved before completion. A subset of provider
qualifications or this generated command plan does not satisfy that coverage.

## Upstream integration dependencies

The smallest follow-up changes are in existing authority owners, outside this
proposal's four-file scope:

| Dependency | Existing owner/callers | Required change before a release runner can use it |
| --- | --- | --- |
| Fresh generation authority | `primary_generation.require_generation`; `primary_generation.main` (`verify`, `reserve`); `windows-langgraph-primary-control.ps1` (`SelectGeneration`) | Extract/expose read-only validation of a new manifest with its existing acceptance, database, image, and deployment bindings. Current `verify` requires an already reserved journal; `reserve` creates it. A selector comparison must never substitute for this authority check. |
| Individual paid admission and resume | `provider_validation_cli.main` (`validate`), called by `primary_generation.main` (`validate-providers`) and control `ValidateProviders`; `PostgresValidationStore` and the existing provider budget/meter owners | Add one-lane admission and capture readback tied to authenticated campaign and daily-dollar reservation/settlement. The current CLI iterates every lane and reports results only at the end; its validation units do not prove campaign dollars. Preserve every occupied or ambiguous slot and conservative hold. Do not treat a mutable campaign JSON or an operator `approved=true` flag as authority. |
| Owner descriptor and trust root | W2/#313; `scorecard-launcher.py` and scorecard evidence consumers | Install the reviewed A2 launcher and single descriptor with exact preparation pins in the owner setup slot. An absent or older API and missing owner evidence fail closed. |
| Sibling setup and execution | W6 operator; existing control `PrepareAcceptance`, `BootstrapDatabaseIdentity`, `SelectGeneration`, snapshot and bridge tools | Supply accepted sibling project/database identities, the unpaid protected profiles and exact candidate image. Execute and time the generated plan with durable receipts. No real rehearsal is claimed here. |

Actual generation selection, staging, qualification, publication and activation
stay with those existing controls and the authorized W6/W7 operator. CI,
security, acceptance, budget, cancellation and sole-writer gates remain intact.
The owner still installs the trusted launcher/descriptor, verifies headroom and
approves per-call caps in the setup slot. This preparation descriptor does not
replace the scorecard's descriptor or attestations.

## Offline verification

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest tests.test_director_release -v
```

The focused tests use the real `ActivationIdentity`, resolved Compose digest,
native validation binding and repository work-packet fixture. Platform I/O is
mocked. The production A2 launcher bytes are copied unchanged to separate
temporary locator and installed files. Only `account_home()` is replaced with
the temporary account root; `owner_root()`, `release_descriptor()` and
`read_descriptor()` are the real producer. The tests exercise exact-byte pins,
environment substitution, missing evidence, root redirection, path links,
mutation and crash recovery. They do not test the native OS account lookup or
the installed scorecard execution/attestation path. Real
`docker compose config` output exercises resource isolation without starting
containers, and the actual control script's parameter block is bound by
PowerShell with its runtime body omitted. A short-lived Python subprocess checks
deadline cleanup and sanitized failures. A harmless real Git fsmonitor fixture
first demonstrates marker execution, then proves the source gate suppresses it.
The emitted launcher's real child records its environment under hostile ambient
Docker selectors; Docker observations alone are mocked in that test. Negative
fixtures cover auxiliary services/images, fresh state, TCP mapping, snapshot
authority and acceptance mode. Additional regressions run the actual profile
writer in temporary files, reproduce checkout import shadowing and root hook
resolution, check every input category at the launcher boundary, and cover
planned stock DB startup, deferred proofs, health commands, singleton counts,
actual stopped-container resources and the Windows command-line limit.
No provider, production database or
live rehearsal is started by the suite. Exact-head full-suite, CI, security and
independent review evidence remain required.
