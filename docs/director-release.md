<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:38 UTC | BEGIN]] -->
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
| `output_directory` | Existing protected directory outside the checkout |
| `timeout_seconds` | Shared whole-doctor subprocess deadline, 1–900 seconds |
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

The reviewed A2 source's `owner_root()` locates the OS account's
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
$env:PYTHONPATH = 'src'
python -B scripts/director-release.py doctor --descriptor C:\protected\r1-descriptor.json
python -B scripts/director-release.py prepare --descriptor C:\protected\r1-descriptor.json
python -B scripts/director-release.py resume --descriptor C:\protected\r1-descriptor.json
python -B scripts/director-release.py rehearsal --descriptor C:\protected\r1-descriptor.json
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
2. Matching runtime, bridge, generation, and validation selectors; validation
   expiration inside the existing 24-hour limit. This is a selector check, not
   full fresh-generation authority validation.
3. The existing `resolved_deployment_digest` contract, including the Slack
   gateway's exact image, explicit `pull_policy: never`, and the nonempty read-only
   `/repository` snapshot mount.
4. `windows-primary-snapshot.ps1 -Action Verify` and the planned reviewer
   allowlists, without adding actors or issuing acceptance.
5. `windows-host-provider-bridge.ps1 -Action Preflight`, followed by the existing
   authenticated GET `/v1/provider/configuration-digest`. Its running lane digests
   and `pinned_environment` must match the supplied validation manifest.
6. Existing database identity and instance UUID through `database_binding`
   with its default `bootstrap=False`, then a read-only query for occupied
   validation slots. Every observed slot blocks preparation; successful,
   unfinished, and ambiguous slots are never treated as unused.
7. The existing read-only `live-dispatch-preflight.py`, followed by exact-byte
   rereads of the descriptor itself and all pinned files before writing anything.

Platform children use closed stdin, a shared deadline, bounded captured output,
and no shell. Raw probe inputs go through temporary stdin, not command-line
arguments. Inherited provider keys, Git overrides, `COMPOSE_FILE`, and
`DOCKER_HOST` are not forwarded. Docker still uses the operator's configured
local context; image identity is checked in that context. Windows Program Files
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
lock, writes and flushes an intent bound to both preparation and whole owner
descriptor digests with the intended output digest, creates the artifact
exclusively, verifies its bytes, and flushes a digest-bound completion receipt.

If interruption leaves a complete artifact whose bytes match the flushed intent,
`resume` can append the missing receipt after verifying every input. It does not
rerun the doctor, render again, overwrite the artifact or execute any effect.
The installed launcher bytes and complete owner descriptor are reread before
completion. Every field and whitespace byte in the owner descriptor participates
in the binding. Resume requires its current digest to equal the journal's;
rotation, even with the same evaluator and preparation pins, remains held.
Old journal records without `owner_descriptor_sha256` are refused without
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

`rehearsal` compares the resolved graphs and refuses shared/external volumes or
networks, driver options, host namespaces, shared writable bind paths, reused
Slack identity, non-loopback ports and final-project port collisions. The backup
path must be new and directly inside the sibling output directory.

The result contains argv arrays for the existing control script, in gate order:
`Preflight`, `Accept`, `Migrate`, `Backup`, `RestoreDrill`, `StageRuntime`,
`PrepareOfflineHandoff`, `ActivateOffline`, `Health`, `Rollback`, `Health`.
The restore proof precedes activation as the existing acceptance chain requires.
The separately pinned acceptance artifact is passed to `Accept`; the runner
never considers its mere existence or a successful plan to be acceptance.
The result also supplies the exact `cwd`, `COMPOSE_FILE` and
`COMPOSE_PATH_SEPARATOR` needed by the existing control consumer.

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
deadline cleanup and sanitized failures. No provider, production database or
live rehearsal is started by the suite. Exact-head full-suite, CI, security and
Claude review evidence remain required.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:38 UTC | END]] -->
