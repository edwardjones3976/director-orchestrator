<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | BEGIN]] -->
# Two-party activation acceptance v3

This is an implemented, explicitly selected acceptance contract, **not an
adopted release policy or activation authorization**. The owner approved its
implementation to support a delivery team consisting of Codex and actual Grok
Bot. Its release requires separate review, exact-head CI, security evidence and
owner adoption of the final implementation and activation candidate.

The tradeoff is one independent nonauthor-provider-family review instead of
provider-v2's two. Codex Security analysis remains mandatory and may belong to
the author's family; its verdict is `ANALYZED`, and its signed record explicitly
states whether it is author-family analysis. It is never counted as the
independent review. Automated analysis and owner approval do not recreate a
second independent provider opinion.

## Selection and compatibility

`LANGGRAPH_PRIMARY_ACCEPTANCE_MODE` still defaults to `github-v1`. Selecting the
exact, case-sensitive value `provider-v3-two-party` selects a separate verifier,
registry version, envelope version and signature domain. Selection alone fails
closed without an owner-pinned registry containing valid signed adoption.

No v1/v2 receipt, challenge snapshot or accepted record is converted. The v2
verifier is unchanged. A v2 registry/package is rejected by v3 and vice versa;
changing the mode changes the generation's configuration digest. Select a fresh
generation and journal through the existing exclusive reservation mechanism.
Do not reinterpret an earlier accepted journal under this mode.

Changing only the mode to `github-v1` while a signed registry/generation pin or a
non-default journal/registry/generation path remains selected now refuses before journal or runtime database access.
This applies to selected v2 and v3 profiles; a pristine legacy profile remains
unchanged, including Compose's inert default paths without pins. An owner who removes the entire protected selection and explicitly
configures a different deployment is exercising configuration authority. The
verifier cannot infer a removed profile from absent settings; this is not a claim
of preventing owner-controlled reconfiguration.

The same registry/journal path and SHA256 settings are used. No default path,
key, account, deployment identity or approved candidate is supplied here. No
runtime roster, allocation, task approval, spending setting, provider binding,
retry bound or live-dispatch switch changes with this implementation.

## Trust and closed evidence contracts

The production validator is `provider_acceptance_v3.py`; its `_closed` checks
reject additional and omitted fields. These activation contracts do not change
the existing v1 model-work packet schemas.

| Evidence | Required binding and authority |
| --- | --- |
| Registry `director-acceptance-registry/v3-two-party` | Existing exact activation identity, repository, PR, packet digest, material-author families, three receipt authorities, revocations and GitHub OIDC policy; plus owner authority, observation authority and signed owner adoption. Raw registry bytes must match the owner-controlled SHA256 pin. |
| Adoption `director-two-party-adoption/v1` | Owner signature over `ADOPT_TWO_PARTY_V3`, reviewed policy commit/tree, actual installed policy-module byte digest, registry digest, and exact activation/repository/PR/packet. The registry digest excludes only the adoption envelope and revocations. This avoids a self-referential hash while binding authorship, issuer and observer choices. |
| Package `director-provider-acceptance/v3-two-party` | Exact issued context and `security`, `independent_review`, `ci` receipts, each signed in the new version's domain. All existing candidate/image/database/generation and challenge/context requirements remain. |
| Envelope `director-provider-acceptance-envelope/v3-two-party` | Package plus authenticated GitHub OIDC identity. Dynamic CI is mandatory here, including current GitHub key verification during replay. |
| Native observation `director-native-product-observation/v1` | Separately signed exact context, observed product/family/served model/session, authentication and transport, capture digest, installed-runtime digest, and lifetime inside the challenge. Its capture digest must match the provider receipt. |

The owner, native observer and each of the three receipt issuers must have
distinct public keys, principals and deployments. This is service/key custody
separation, not a claim that five AI providers participated. The protected
registry is a trust anchor; a packet cannot enroll its own owner or observer.
The owner controls installation/adoption and must independently establish those
trust anchors before authorizing a candidate. Revoked authorities fail closed.

The owner adoption binds hashes of the **actual installed policy module bytes**,
computed by `implementation_digest()`. A checkout digest may differ from the
image's bytes because of line endings. Compute and review the binding from the
intended immutable installation; do not replace it with a Git label or normalize
an observed mismatch away. The complete candidate/image checks remain separate.

The independent reviewer must not belong to **any** declared material-author
family. A Codex-authored candidate therefore needs an independently qualified
Grok Bot family. Bot-authored work may be reviewed by qualified Codex. Mixed
authorship with no eligible reviewer refuses. Product branding never establishes
provider-family independence: if the observed families coincide, the gate fails.

Security evidence must identify the Codex Security product and include a sealed
scan/report digest, complete coverage, zero unresolved findings and the correct
author-family label. The trusted security producer must verify those product
artifacts before signing. A chat answer or a digest invented by the candidate
author is insufficient. All six baseline CI checks remain mandatory, with the
existing immutable workflow provenance and exact-candidate run/attempt checks.

## Actual Grok Bot remains a qualification prerequisite

The observation signer must inspect authenticated native product/session/model
and installation evidence independently of the candidate/receipt producer. It
must not sign requested labels, screenshots of branding, chat `ACCEPT`, or
provider-supplied model assertions as if they were authenticated observations.
The verifier consumes that trusted signed observation; it does not manufacture
the observation, install a collector or grant signing custody.

`grok-bot` is a product identity, **not** a newly invented provider family. The
observed family must be canonical and match the separately enrolled receipt
issuer. Cursor Agent, Cursor's Grok model and direct xAI API are rejected as
substitutes. Unknown/unavailable/default/auto model identities are rejected.
Codex and Codex Security also require authenticated observation evidence.

There is presently no installed v3 native product issuer/observer established by
this change. The existing v2 native issuer explicitly refuses selected v3 before
identity, key/state changes or provider dispatch; unknown modes also refuse.
This refusal reads the broker's trusted environment: the shared `ReviewContext`
is not itself mode-bearing. Distinct signature domains still prevent an old
receipt from being accepted as v3. Do not launch the old issuer to produce an
old receipt for a v3 challenge. No signing helper accepting arbitrary supplied
observations is exposed. A separately reviewed and owner-enrolled producer with
real product evidence is still required; this known absence keeps the mode
unavailable on an unqualified host.

The existing isolated GitHub CI issuer can sign the v3 CI domain when the owner
sets `CI_ACCEPTANCE_MODE` in its protected environment. Its default remains
`provider-v2`; mode is not a workflow-dispatch input. Its installation/workflow
adoption remains independently required. The issuer does not inspect candidate
code under signing authority or weaken its required checks.

## Shared lifecycle

Explicit selection reaches `SelectGeneration`, `Preflight`, `Challenge`, `Accept`,
`Migrate`, `Backup`, `RestoreDrill`, `PrepareOfflineHandoff` and startup replay
through the existing control script, generation verifier and atomic journal.
Runtime verifies accepted evidence before opening PostgreSQL. Readiness still
requires the five completed stages, immutable real legacy inventory, exact
generation/image/database binding and subsequent writer fencing.

`PrepareOfflineHandoff` retains its explicit false paid/dispatch/validation
checks, proves selected intake stopped, reads the actual inventory, binds it
immutably and rechecks readiness/intake. It does not call `Activate`, start a
service, transfer intake or create an `ACTIVATED` record. The separate live
provider floor, current provider captures, historical reconciliation hold and
all existing deployment approvals remain applicable.

Tests use synthetic issuer/owner/observer keys and a fixture GitHub signing key.
They execute signed acceptance/replay, runtime refusal before database access,
exclusive generation reservation and the production PowerShell handoff branch
with actual temporary SQLite inventory. Docker and external CI key retrieval
are substituted. This proves the disabled source path, not qualification or
successful live deployment.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | END]] -->
