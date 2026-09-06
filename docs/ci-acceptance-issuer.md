<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:15 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | BEGIN]] -->
# Isolated CI acceptance issuer

This implementation can issue only the CI receipt. It does not enroll itself,
activate production, dispatch providers or sign another review role. The workflow
is dormant unless `CI_ISSUER_ADOPTED` is owner-provisioned after separate exact
review. No provisioning or workflow dispatch is part of this change.

The protected `ci-acceptance-issuer` environment must pin `CI_ISSUER_POLICY_JSON` to
the same owner policy used by the acceptance journal. Both jobs check out exactly
`github.workflow_sha` and verify the checkout against that revision. The registry's
`workflow_ref` and `workflow_sha` therefore bind the actual issuer code as well as
the executing workflow; a mutable source variable cannot substitute other code.
A candidate cannot adopt or update these pins. Restrict environment access to the trusted workflow
and require owner approval for changing its source/policy. The workflow checks out
only workflow-bound issuer source. A separate preparation job with no OIDC
permission installs an explicit version-pinned set of four issuer dependencies
with automatic dependency resolution disabled. It packages the runtime with a
source/run/attempt manifest and outputs its SHA256 and immutable artifact ID.
The signing job downloads that artifact from the same workflow run, verifies its
SHA256 against the trusted preparation output and its exact manifest before
extraction, and executes the prepared runtime without dependency installation.
Both jobs use Ubuntu 24.04 and Python 3.12. Candidate code, candidate dependencies
and candidate artifacts never execute in either issuer job. Dependency publisher
and GitHub-hosted runner trust remain explicit deployment prerequisites.

`GitHubOIDCPolicy.from_document` requires repository name, immutable repository
and owner IDs, exact OIDC subject, issuer workflow ref and SHA, principal,
deployment, required check names, and candidate check workflow ID/path/source
commit. `required_ci_checks` is a JSON list. Issuer workflow identity and the
workflow being checked are separate authorities. This first implementation accepts
only successful same-repository **push** runs: PR merge-ref runs and fork PRs are
held rather than inferring which source actually executed. It authenticates the
PR head, candidate tree, exact run and attempt, workflow ID/path, unchanged pinned
check-workflow blob and each required successful job through the GitHub API.
The production CLI requires all six baseline checks: Contribution attribution,
Python 3.12 (ubuntu-latest), Python 3.12 (windows-latest), Compose and container
build, Secret hygiene, and Cross-process recovery probe. Policy may add checks
but cannot omit that baseline.

Repository metadata independently supplied by the coordinating review on
2026-09-06 identifies `edwardjones3976/director-orchestrator`, repository ID
`1333573567`, owner ID `145315321`, and CI workflow ID `333971972` at
`.github/workflows/ci.yml`. These public IDs are provisioning examples, not an
installed registry. Pin the actual GitHub-issued subject explicitly: immutable-ID
and customized subject formats are supported and must not be guessed from the
repository name. The issuer workflow SHA/ref (also binding issuer source) and
accepted check-workflow source commit still require separate owner-reviewed pins.

The CI identity envelope has exactly `version: director-ci-oidc/v1`, `public_key`
(base64 raw Ed25519 public key), and `token` (GitHub OIDC JWT). The audience is
`director-ci-oidc/v1:` plus SHA256 of canonical JSON containing the public key and
complete review context. The ephemeral private key stays in issuer process memory.
The signed v2 receipt retains the standard closed receipt schema; output is
`{receipt, ci_identity}`. No signing-key file or coordinator signing secret exists.
`receipt.session_id` names the authenticated OIDC issuing run and attempt. The
candidate check run remains in `ci_checks` and transport evidence; it is not
misrepresented as the signing execution.

At first acceptance `resolve_ci_identity` fetches keys only from GitHub's fixed
JWKS endpoint, rejects redirects, verifies RS256 signature, exact audience/issuer,
repository and owner IDs, subject, workflow ref/SHA, run identity and bounded
expiry. It returns the verified authority and actual issuing session; the runtime
must require that session to equal the signed CI receipt session at acceptance and
readiness. Caller JWKS and alternate issuers are not accepted. The runtime must
atomically store the resolved public authority with the complete accepted package
and original trusted acceptance time. Later readiness must reauthenticate the
original JWT against GitHub's fixed fresh JWKS and current owner policy, evaluating
its signed validity bounds at the original acceptance time and comparing the
result with the stored authority. A database snapshot or hash is not an identity
anchor. GitHub key removal or JWKS unavailability fails readiness closed; an
owner-protected historical key archive is not implemented. Never accept keys from
a caller-provided archive. The JWT contains no private signing key,
but remains short-lived evidence and is retained with the restricted receipt.

No live issuer provenance is claimed by unit tests. Synthetic RSA/Ed25519 keys and
mocked authenticated API responses exercise rejection behavior only.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:15 UTC | END]] -->
