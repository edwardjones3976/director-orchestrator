<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:24 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | BEGIN]] -->
# Provider-v2 runtime acceptance

The v1 GitHub-account mode remains the default. Explicit provider-v2 mode uses a
pre-migration SQLite journal as its authoritative activation state; it never
projects a consumed nonce into a separately written JSON file. The existing v1
JSON artifact is neither imported nor overwritten. Start a fresh PREFLIGHT sequence
for the exact candidate/profile/database identity.

Owner-provisioned configuration:

- `LANGGRAPH_PRIMARY_ACCEPTANCE_MODE=provider-v2`
- `LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH`: absolute registry file path.
- `LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256`: SHA256 of exact registry bytes.
- `LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH`: absolute durable SQLite path.

The path and digest pin are trusted deployment configuration, protected from the
coordinator and providers. A caller-selected registry plus its matching digest is
not enrollment authority. Mount the owner-controlled registry read-only. Compose
uses `LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_DIRECTORY` on the host, containing
`registry.json`, mounted at `/run/director-acceptance-registry`; the shared named
`director-data` volume stores `/data/primary-acceptance-v2.sqlite3`. Protect this
journal against replacement, rollback, deletion and writes outside the trusted
activation/runtime processes; SQLite transactions are concurrency/durability
controls, not protection against an administrator rewriting storage. Preserve
existing COMPOSE_FILE overrides and the staged Postgres port mapping.

The closed registry document contains `version: director-acceptance-registry/v1`,
`activation` (all ActivationIdentity fields), `repository`, `pull_request_number`,
`packet_digest`, `material_author_providers`, `authorities`, `revoked_deployments`,
and `ci_oidc_policy`. Production CI policy must include all six existing activation
checks (attribution, both Python platforms, Compose/container build, secret hygiene
and cross-process recovery); additional configured checks are also mandatory.
The static CI template and dynamic OIDC policy must pin identical principal,
deployment, workflow and required checks. Current revocations apply to the resolved
CI identity as well as the static template. Each of the three authorities contains every IssuerAuthority
field, with base64 public_key and a list of required_ci_checks. `ci_oidc_policy`
is null for static protected CI keys, or the closed GitHubOIDCPolicy document from
the CI issuer module. The full review-packet digest must be independently prepared
from the complete exact source packet before owner provisioning. The registry
constrains identity/context; no acceptance package can enroll itself.

Run the existing control script's `Preflight`, then `Challenge`. Challenge emits
ReviewContext JSON built only from the pinned registry, with a cryptographically
random 256-bit nonce and one-hour window, durably stored before emission. Send this
exact job to the independently protected issuers. `Accept -AcceptanceArtifactPath`
selects the unchanged v1 gate in github-v1 mode, or the v2 transaction in provider-v2.
Python CLI also exposes `challenge` with the existing exact identity arguments.
The remaining Migrate/Backup/RestoreDrill/Activate/Rollback sequence is unchanged.

Static CI uses the unchanged v2 signed package. Dynamic CI uses the closed wrapper
`{version: director-provider-acceptance-envelope/v2, package: <signed v2 package>,
ci_identity: <director-ci-oidc/v1 envelope>}`. First acceptance verifies CI OIDC
online against the pinned GitHub issuer/workflow policy, never caller JWKS, then
stores the resulting authority and original source in the SAME transaction as
challenge consumption and ACCEPTED activation state. Both acceptance and readiness
require the CI receipt session to equal the authenticated OIDC issuing run/attempt;
the candidate check run is separate evidence and cannot substitute for issuer identity.
Dynamic CI requires the
independently reviewed CI issuer module; missing integration fails closed.

BEGIN IMMEDIATE and state revision CAS serialize writers. Unknown, expired or
already consumed challenges cannot be accepted; even an exact second submission
is rejected. Failure after consumption but before state insertion rolls back both.
State readback revalidates the original signed source at its trusted recorded
acceptance timestamp and stored issuer registry, while also applying the currently
owner-pinned policy and revocations. A changed context/key policy fails closed;
renew acceptance under an explicitly provisioned policy rather than silently
relabeling history. Dynamic CI JWT signatures are reauthenticated against the fixed GitHub JWKS endpoint
at readiness using the original acceptance timestamp; the result must match the
stored key and current pinned workflow policy. A substituted database key/JWT
cannot authenticate itself. If GitHub is unavailable or its original signing key
has rotated out, readiness fails closed. No caller/archive-supplied JWKS fallback
is supported; an owner-pinned historical key archive would require separate work.

DefaultPrimaryActivation now checks v2 acceptance/restore/inventory readiness
before opening the primary database. It compares the accepted target identity
before connecting, then reads existing server metadata and the database-instance
UUID before any setup DDL. Each new pool connection validates this metadata, and
control-plane setup repeats the check under its advisory lock on the same
connection that performs setup, without reopening between validation and DDL.
Missing metadata is rejected; v2 startup cannot initialize an unaccepted database.
The existing post-open comparison remains a final consistency check before any
intake/writer acquisition. GitHub-v1 retains its existing initialization path. Both
pre-ACTIVATED readiness and a complete ACTIVATED restart are accepted. Missing,
revoked, tampered or wrong-candidate evidence blocks startup. This does not waive
provider LIVE_OK, owner approval, one-writer, migration/restore, kill-switch or
rollback gates, and it does not create the independent issuers or authorize its
own deployment/adoption.

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:11 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:24 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:07 UTC | END]] -->
