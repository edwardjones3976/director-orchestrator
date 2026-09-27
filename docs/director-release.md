<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:38 UTC | BEGIN]] -->
# Director R1 preparation and doctor

`scripts/director-release.py` is a **preparation-only proposal**, not a complete
release runner. It can inspect pinned inputs, render the exact Compose artifact,
and resume a completed render without replaying checks. Every result reports
`release_ready: false`. There is no `run`, `Activate`, or `--rehearsal` option.
Passing these diagnostics does not mean a generation is accepted, a paid window
is admitted, a rehearsal ran, or the system is deployed.

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

The descriptor and executables are operator inputs, not authenticated authority.
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

## Commands and exit status

From the candidate checkout, with the repository virtual environment:

```powershell
$env:PYTHONPATH = 'src'
python -B scripts/director-release.py doctor --descriptor C:\protected\r1-descriptor.json
python -B scripts/director-release.py prepare --descriptor C:\protected\r1-descriptor.json
python -B scripts/director-release.py resume --descriptor C:\protected\r1-descriptor.json
```

These are templates: replace the descriptor path with a reviewed local file.
`doctor` performs read-only configuration, Git, Docker image/configuration,
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

The doctor verifies:

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
7. The existing read-only `live-dispatch-preflight.py`, followed by a reread of
   all pinned files before writing anything.

Platform children use closed stdin, a shared deadline, bounded captured output,
and no shell. Raw probe inputs go through temporary stdin, not command-line
arguments. Inherited provider keys, Git overrides, `COMPOSE_FILE`, and
`DOCKER_HOST` are not forwarded. Docker still uses the operator's configured
local context; image identity is checked in that context. The descriptor's
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
lock, writes and flushes a descriptor-bound intent, creates the artifact
exclusively, verifies its bytes, and flushes a digest-bound completion receipt.

An intent without its receipt, a truncated or changed journal, an existing
unrecorded output, or changed inputs/outputs requires reconciliation. None is
automatically replayed or overwritten. The journal hashes detect accidental
changes; they do not authenticate an operator or authorize a release. This
journal covers **only Compose preparation**. It neither imports old provider
receipts nor resolves any uncertain paid or GitHub action.

## Deliberately missing R1 capabilities

The smallest follow-up changes are in existing authority owners, outside this
proposal's four-file scope:

| Dependency | Existing owner/callers | Required change before a release runner can use it |
| --- | --- | --- |
| Fresh generation authority | `primary_generation.require_generation`; `primary_generation.main` (`verify`, `reserve`); `windows-langgraph-primary-control.ps1` (`SelectGeneration`) | Extract/expose read-only validation of a new manifest with its existing acceptance, database, image, and deployment bindings. Current `verify` requires an already reserved journal; `reserve` creates it. A selector comparison must never substitute for this authority check. |
| Individual paid admission and resume | `provider_validation_cli.main` (`validate`), called by `primary_generation.main` (`validate-providers`) and control `ValidateProviders`; `PostgresValidationStore` and the existing provider budget/meter owners | Add one-lane admission and capture readback tied to authenticated campaign and daily-dollar reservation/settlement. The current CLI iterates every lane and reports results only at the end; its validation units do not prove campaign dollars. Preserve every occupied or ambiguous slot and conservative hold. Do not treat a mutable campaign JSON or an operator `approved=true` flag as authority. |
| Disposable bootstrap and rehearsal | Control `PrepareAcceptance`, `BootstrapDatabaseIdentity`, `SelectGeneration`, snapshot and bridge tools | Supply separate accepted disposable-project/database identities, private temporary paths/network/ports, and an explicitly unpaid profile. Wire the existing tools in their enforced order, including backup/restore and rollback proofs. No real rehearsal was executed by this proposal. |

After those dependencies land, a separately reviewed orchestration change can
record intents and receipts around actual generation selection, staging,
qualification, publication, and activation. It must retain CI, security,
acceptance, migration, restore, budget, cancellation, and sole-writer gates.
The trusted scorecard launcher/release descriptor, pricing/probe inputs,
paid-window scorecard collection, Slack delivery/readback, and final acceptance
remain separate unimplemented release work. This preparation descriptor does
not replace the scorecard's descriptor or attestations.

## Offline verification

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest tests.test_director_release -v
```

The tests use the real `ActivationIdentity`, real resolved Compose digest helper,
native validation binding, and repository work-packet fixture. Docker, bridge,
snapshot, and database I/O are mocked. A separate short-lived Python subprocess
tests deadline cleanup and sanitized failure handling. No provider, container,
generation, production database, or live rehearsal is started by the suite.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:38 UTC | END]] -->
