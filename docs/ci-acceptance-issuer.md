<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | BEGIN]] -->
# Isolated CI acceptance issuer

This implementation can issue only the CI receipt. It does not enroll itself,
activate production, dispatch providers or sign another review role. The workflow
is dormant unless `CI_ISSUER_ADOPTED` is owner-provisioned after separate exact
review. No provisioning or workflow dispatch is part of this change.

The protected `ci-acceptance-issuer` environment must pin `CI_ISSUER_SOURCE_SHA`
to the separately reviewed issuer implementation and `CI_ISSUER_POLICY_JSON` to
the same owner policy used by the acceptance journal. Pin the executing workflow
source independently using `workflow_ref` and `workflow_sha`; a candidate cannot
adopt or update these values. Restrict environment access to the trusted workflow
and require owner approval for changing its source/policy. The workflow checks out
only trusted issuer source and installs only fixed issuer dependencies, never
candidate code, candidate dependencies or candidate artifacts as executable code.

`GitHubOIDCPolicy.from_document` requires repository name, immutable repository
and owner IDs, exact OIDC subject, issuer workflow ref and SHA, principal,
deployment, required check names, and candidate check workflow ID/path/source
commit. `required_ci_checks` is a JSON list. Issuer workflow identity and the
workflow being checked are separate authorities. This first implementation accepts
only successful same-repository **push** runs: PR merge-ref runs and fork PRs are
held rather than inferring which source actually executed. It authenticates the
PR head, candidate tree, exact run and attempt, workflow ID/path, unchanged pinned
check-workflow blob and each required successful job through the GitHub API.

Repository metadata independently supplied by the coordinating review on
2026-09-06 identifies `edwardjones3976/director-orchestrator`, repository ID
`1333573567`, owner ID `145315321`, and CI workflow ID `333971972` at
`.github/workflows/ci.yml`. These public IDs are provisioning examples, not an
installed registry. Pin the actual GitHub-issued subject explicitly: immutable-ID
and customized subject formats are supported and must not be guessed from the
repository name. The issuer source SHA, issuer workflow SHA/ref and accepted
check-workflow source commit still require separate owner-reviewed pins.

The CI identity envelope has exactly `version: director-ci-oidc/v1`, `public_key`
(base64 raw Ed25519 public key), and `token` (GitHub OIDC JWT). The audience is
`director-ci-oidc/v1:` plus SHA256 of canonical JSON containing the public key and
complete review context. The ephemeral private key stays in issuer process memory.
The signed v2 receipt retains the standard closed receipt schema; output is
`{receipt, ci_identity}`. No signing-key file or coordinator signing secret exists.

At first acceptance `resolve_ci_authority` fetches keys only from GitHub's fixed
JWKS endpoint, rejects redirects, verifies RS256 signature, exact audience/issuer,
repository and owner IDs, subject, workflow ref/SHA, run identity and bounded
expiry. Caller JWKS and alternate issuers are not accepted. The runtime must
atomically store the resolved public authority with the complete accepted package
and original trusted acceptance time. Later readiness uses only that protected
journal snapshot and current owner policy/revocation; it must not re-enroll a key
from an expired JWT or arbitrary archive. The JWT contains no private signing key,
but remains short-lived evidence and is retained with the restricted receipt.

No live issuer provenance is claimed by unit tests. Synthetic RSA/Ed25519 keys and
mocked authenticated API responses exercise rejection behavior only.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | END]] -->
