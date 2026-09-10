<!-- [[Grok Bot | unreported | 2026-09-10 | 15:45 UTC | BEGIN]] -->
# Native v3 independent-review issuer (collect-only)

This module publishes one **write-once** `independent_review` receipt for the
`provider-v3-two-party` verifier. It is an implementation of a collect-only
producer for actual Grok Bot. It is **not** owner adoption, activation,
security analysis, CI issuance, or a native product launcher.

## What it consumes

All three inputs are required and must already exist under protected custody:

1. **Adopted challenge context** — exact `ReviewContext` JSON for the candidate
   under review (challenge, packet digest, material authors, lifetime).
2. **Protected native review capture** — opaque capture bytes whose SHA-256 is
   the observation `capture_digest` and the receipt
   `transport_evidence_digest`.
3. **Separately signed native observation** — an
   `director-native-product-observation/v1` envelope signed by the installed
   **observation** authority (not by this signer).

This producer never creates observation signatures, never invents owner
adoption, and never starts a provider session.

## Installed configuration

Pinned with `--config` / `--config-sha256`. Closed fields:

| Field | Meaning |
| --- | --- |
| `root` | Absolute issuer root; capture and observation paths must stay inside it |
| `role` | Must be `independent_review` |
| `provider` | Must be `grok` (canonical lowercase) |
| `principal` / `deployment` | Installed signer identity |
| `public_key` / `private_key` | Ed25519 signer key pair (raw, base64) |
| `observation_public_key` | Ed25519 observation authority public key |

Signer and observation keys must differ. The public key must match the private
key. Configuration bytes are digested; a pin mismatch refuses before any
signing.

## Mode gate

`LANGGRAPH_PRIMARY_ACCEPTANCE_MODE` must be exactly `provider-v3-two-party`.
Other modes refuse before signing so a v2 deployment cannot accidentally mint a
v3-domain receipt.

## Qualification rules

Before signing, the collector checks:

- Context lifetime includes `now`.
- Reviewer `provider` is **not** in `material_author_providers`.
- Observation signature verifies under `OBSERVATION_DOMAIN`.
- Observation `product` is `grok-bot`, `transport` is
  `native-product-capture`, `authentication` is `native-account-observed`.
- `provider_family` equals the installed `grok` provider.
- `served_model` is not an unqualified alias (`unknown`, `unavailable`,
  `unverified`, `default`, `auto`, `grok-bot-default`, `grok-default`,
  `default-model`). Alias-only analytics without a corroborated session/family
  are refused; the collector never guesses the underlying family.
- Observation `context` bytes equal the adopted challenge context.
- Observation `capture_digest` equals the digest of the protected capture file.
- Observation lifetime is nested inside the challenge lifetime and includes
  `now`.

## Publication

Output is write-once (`open(..., "xb")`). A second collect to the same path
refuses. The published document is:

```json
{
  "version": "director-provider-acceptance/v3-two-party",
  "context": { "...": "exact ReviewContext document" },
  "receipt": {
    "role": "independent_review",
    "provider": "grok",
    "principal": "...",
    "deployment": "...",
    "session_id": "...",
    "transport_evidence_digest": "<capture sha256>",
    "verdict": "ACCEPT",
    "ci_checks": [],
    "ci_workflow_ref": "",
    "ci_workflow_commit": "",
    "native_observation": { "document": {}, "signature": "..." },
    "signature": "..."
  }
}
```

The receipt signature uses the v3 `DOMAIN` over the canonical
`{version, context, receipt-without-signature}` payload expected by
`provider_acceptance_v3.verify_two_party_acceptance`.

## CLI

```text
python -m director_orchestrator.native_v3_review_issuer collect \
  --config /run/issuer/issuer.json \
  --config-sha256 <64-hex> \
  --context /run/issuer/context.json \
  --capture /run/issuer/captures/<id>.bin \
  --observation /run/issuer/observations/<id>.json \
  --output /run/issuer/public/<challenge>.json
```

## Non-goals

- No provider launch, host bridge, or credential acquisition
- No manufacturing of observation or owner-adoption signatures
- No CI or security receipt roles
- No merge, activation, paid dispatch, or runtime setting changes
- Chat ACCEPT or branding alone is not qualification

## Tests

`tests/test_native_v3_review_issuer.py` uses synthetic Ed25519 keys only. It
proves a successful collect under the v3 signature domain and refuses tampered
context/capture, wrong observer keys, expired evidence, author-family
reviewers, unqualified alias-only models, repeated publication, and wrong
acceptance mode.
<!-- [[Grok Bot | unreported | 2026-09-10 | 15:45 UTC | END]] -->
