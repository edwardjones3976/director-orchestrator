<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]] -->
# Primary staging and generation controls

These controls prepare a candidate for activation. Staged readiness does not
mean LangGraph owns intake or that providers, effects, or legacy recovery run.
The operator's protected environment file remains selection authority. Keep it
and the owner-provided manifest outside writable provider workspaces.

## Explicit preparation

Build the reviewed clean checkout explicitly before running runtime controls;
the control script no longer builds images or starts Postgres as a side effect.
Use the same Compose project/environment as the deployment, with
`--build-arg DIRECTOR_CANDIDATE_COMMIT=<exact-commit>` and
`--build-arg DIRECTOR_CANDIDATE_TREE=<exact-tree>` on `docker compose build
orchestrator`. These label values must match the control checkout and configured
candidate pins. Building is not acceptance. The image must already exist for
utility containers, which use `--no-deps --pull never`.

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

The deployment digest covers all resolved Settings fields, including secrets
only through the digest. Its documented exclusions are the operational switches
for paid runs, primary enabled, new intake, live dispatch, offline simulation,
and the parallel kill switch. Staging independently requires execution gates
off; live activation uses the existing deterministic authority gates. All other
settings changes require a fresh manifest. Manifest path/digest selection is
outside Settings and therefore does not create a circular self-hash.

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

Final immutable legacy inventory is still captured only by Activate after all
mutating surfaces stop. Activate selects the normal application and retains the
existing acceptance, provider, ownership and effect gates. Provider-v2 normal
startup also verifies the selected immutable generation before database setup.
Rollback selects the normal application after its existing fenced proof.

Provider qualification and issuer enrollment remain separate authorized work.
This package does not implement `ValidateProviders`, enroll a host, call any
provider, create an acceptance signature, or assert live activation.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]] -->
