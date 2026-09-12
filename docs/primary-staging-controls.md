<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | BEGIN]] -->
<!-- [[OpenAI Codex mechanical recovery | GPT-6 | 2026-09-10 | 18:21 UTC | BEGIN]] -->
<!-- [[Grok Bot | serving model unreported | 2026-09-10 | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]] -->
# Primary staging and generation controls

These controls prepare a candidate for activation. Staged readiness does not
mean LangGraph owns intake or that providers, effects, or legacy recovery run.
The operator's protected environment file remains selection authority. Keep it
and the owner-provided manifest outside writable provider workspaces.

## Explicit preparation

Build the reviewed clean checkout explicitly before running runtime controls;
the control script no longer builds images or starts Postgres as a side effect.
Set the build-only `DIRECTOR_RUNTIME_IMAGE` process value to
`<compose-project>-orchestrator` so building never retags a selected image ID.
Use the same Compose project/environment as the deployment, with
`--build-arg DIRECTOR_CANDIDATE_COMMIT=<exact-commit>` and
`--build-arg DIRECTOR_CANDIDATE_TREE=<exact-tree>` on `docker compose build
orchestrator`. These label values must match the control checkout and configured
candidate pins. Building is not acceptance. The image must already exist for
utility containers, which use `--no-deps --pull never`. The control tool inspects
the actual content-addressed image ID and rejects an embedded entrypoint. It
then sets the Compose image to that immutable ID. Candidate labels help identify
the checkout but are not accepted as image-content identity.

Start the intended Postgres service explicitly under the approved host setup.
`BootstrapDatabaseIdentity` then creates only the schema container and
`director_primary_control_plane` identity singleton, or verifies its existing
version, storage digest, and UUID. It does not start services, construct a pool,
create task/effect tables, or initialize checkpoint storage. Incompatible
metadata fails closed instead of being repaired. `Migrate` requires the exact
authenticated ACCEPTED state before application/checkpoint DDL; migration
retains the identity UUID established by bootstrap.

`Health` observes only the existing health endpoint. `Preflight` records its
evidence against an existing database identity; it does not build an image,
start a service, or initialize application/checkpoint schemas. Challenge and
acceptance actions deliberately write their bounded evidence journal.

## Owner-provided generation manifest

Provider-v2 controls require a fresh selected generation. The closed JSON
contract is `director-primary-generation/v1`, with exactly these fields:

| Field | Bound value |
| --- | --- |
| `version` | `director-primary-generation/v1` |
| `activation` | Complete existing `ActivationIdentity` document |
| `journal_path` | Fresh direct `/data/<name>.sqlite3` path |
| `registry_path` | Effective container registry path |
| `registry_sha256` | Exact registry bytes and configured digest |
| `provider_bindings_sha256` | SHA256 of the complete UTF-8 `LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON` string |
| `deployment_configuration_sha256` | `primary_generation.deployment_digest(Settings())` under the intended resolved container settings and new journal path |
| `image_id` | Actual `sha256:...` Docker image ID from host inspection, covering image configuration and root filesystem content |
| `resolved_compose_sha256` | `primary_generation.resolved_deployment_digest` of the complete resolved Compose JSON with that immutable image selected |

The deployment digest covers all resolved Settings fields, including secrets
only through the digest. Its documented exclusions are the operational switches
for paid runs, primary enabled, new intake, live dispatch, offline simulation,
and the parallel kill switch. Staging independently requires execution gates
off; live activation uses the existing deterministic authority gates. All other
settings changes require a fresh manifest. Manifest path/digest selection is
outside Settings and therefore does not create a circular self-hash.

The second deployment digest binds the entire resolved Compose graph: mounts,
ports, networks, dependency configuration, environments, image ID, command and
entrypoint. Only the explicit mode switches, checked main/staging command choice,
and the manifest selection/digest self-references normalize. An arbitrary
command or staged command with enabled execution switches is rejected instead
of normalized. The control script computes this digest inside the exact image
using a utility container with no network or host mounts; resolved configuration
travels over stdin and only its digest is printed. When preparing a manifest,
use `primary_generation compose-digest --image-id <id>` with that resolved JSON
on stdin and the intended new journal path selected.

Image and actual Compose verification are host control responsibilities. The
isolated service checks their pinned launch metadata against the manifest; it
does not claim to inspect Docker itself or receive a Docker socket. After start,
the control also reads back the running container's actual image ID and command.

Supply the independently controlled manifest file and its exact SHA256 to
`SelectGeneration -GenerationManifestPath <file> -GenerationManifestSha256 <hash>`.
The action stops Slack, n8n and the orchestrator, confirms every mutating surface
is stopped, and verifies the complete manifest against the database identity and
effective configuration. It exclusively persists the exact manifest bytes under
`/data/generation-<sha256>.json` and exclusively reserves the new journal file.
It binds the new journal to the manifest digest with a generation metadata row,
and rejects a substituted journal on every verification. It checks stopped
state again before atomically replacing the environment file
with all three selection pointers. The owner-controlled digest authenticates
the persisted bytes; merely editing the data-volume copy cannot select new bytes.

No existing journal or manifest is overwritten. Interrupted selection can leave
an unused manifest or initialized generation journal; preserve those artifacts and select a new
generation rather than reusing or deleting them. This is a fail-closed sequence,
not a transaction spanning Docker, the host filesystem and the data volume.
Earlier selected generations remain available for audit. Compose honors the
selected journal path and retains the historical path as its default for
unselected legacy configurations.

Run Preflight, Challenge, Accept, Migrate, Backup and RestoreDrill for the newly
selected generation. The signed acceptance and restore proof must match its
candidate, tree, database instance, schema, namespace, profile and project.

## Evidence-only service

`StageRuntime` verifies the selected generation and authenticated acceptance and
restore proof before stopping and proving all mutating surfaces closed. It
selects `director_orchestrator.primary_staging:app`, disables execution switches,
and starts only the existing candidate orchestrator image. Startup independently
rechecks generation, database identity and authenticated journal evidence using
read-only access. Startup failure leaves mutating services stopped.

The staged ASGI module has only GET `/health` and `/v1/staging/status`. It never
imports the live application or creates task stores, graphs, provider adapters,
writer leases, recovery tasks, expiry sweeps or reconciliation loops. Existing
legacy tasks are not resumed. Task mutation, intake and effect endpoints are
absent. Health explicitly reports `staged_ready` and `evidence_only`, with actual
runtime, graph, router, dispatch, effects and writer readiness false. It does
not infer ownership of another service from its own successful startup.

`PrepareOfflineHandoff` supplies the inventory prerequisite for a separately
authorized Stage A run. Before selecting the initial generation, set explicit
`ALLOW_PAID_RUNS=false`,
`LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=false` and
`DIRECTOR_PROVIDER_VALIDATION_ENABLED=false` in its protected profile. The action
rejects missing or enabled values rather than rewriting them. Validation mode
is part of the resolved Compose digest, so adding it later requires a fresh
generation. The action requires an explicitly selected signed-provider mode (`provider-v2` or the exact literal `provider-v3-two-party`) and the same clean candidate,
image, database and selected-generation verification as the other controls.

After authenticated acceptance and restore pass `require-staged`, the action
stops and proves the selected project's Slack gateway, n8n and orchestrator
closed, reads the real legacy database inventory, binds it immutably, calls
`require-ready`, and proves those services remain stopped. An identical inventory
can be retried; changed inventory is refused. It changes no switches, starts no
services, transfers no intake ownership and records no `ACTIVATED` stage. It
neither calls `Activate` nor relaxes acceptance or restore requirements.

The stop proof covers only the selected Compose project. Before this action or
Stage A, the operator must separately establish that Default Director (D) and
any other deployment cannot admit the same intake or mutate the inventoried
legacy database. Sibling services are not stopped by this action; this is not
host-wide fencing. Keep that quarantine in force through the handoff.

`Activate` also captures and binds final inventory after stopping intake, then
selects the normal application with its existing live provider, acceptance,
ownership and effect gates. Normal startup under either signed-provider mode
(`provider-v2` or `provider-v3-two-party`) verifies the selected immutable
generation and runtime acceptance, including bound inventory, before offline
provider doubles are selected. `PrepareOfflineHandoff` satisfies that
inventory prerequisite without being live activation. Rollback selects the
normal application after its existing fenced proof.

`ValidateProviders` requires the verified staged container and freshly checked
generation/acceptance/restore evidence. It checks the validation manifest's
candidate/tree and requires its `generation` to equal the primary manifest
SHA256 before invoking the fixed `provider_validation_cli validate` entry point.
No caller command or namespace is accepted, and validation storage is never
bootstrapped implicitly. The separately authored provider-validation package
must be adopted into the deployed candidate; it owns authenticated bridge
access, durable admissions, bounded budgets and captured results. Without that
package or its independently provisioned authority, validation cannot run.

Issuer enrollment and real provider qualification remain separate authorized
work. Developing these controls did not enroll a host, call any provider, create
an acceptance signature or activate the runtime.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-10 | 05:08 UTC | END]] -->

The separately versioned, default-disabled two-party acceptance contract is
documented in [two-party activation v3](two-party-activation-v3.md). Its explicit
selection preserves this lifecycle and requires signed owner adoption and
qualified native evidence; this document grants no adoption or activation.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | END]] -->
<!-- [[Grok Bot | serving model unreported | 2026-09-10 | END]] -->
<!-- [[OpenAI Codex mechanical recovery | GPT-6 | 2026-09-10 | 18:21 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | END]] -->
