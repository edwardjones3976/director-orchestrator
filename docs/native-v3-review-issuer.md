<!-- [[Grok Bot | unreported | 2026-09-10 | 16:00 UTC | BEGIN]] -->
# Native v3 independent-review issuer (collect-only)

Collect-only producer of one **challenge-bound**, **write-once**
`independent_review` receipt for `provider-v3-two-party`. It is **not** owner
adoption, activation, security analysis, CI issuance, provider launch, or a
native product wire schema.

## Enrolled reviewers

Product identity is separate from the observed canonical provider family:

| Enrolled family | Qualified product | Typical use |
| --- | --- | --- |
| `openai` | `codex` | Enrolled Codex reviewing Bot-authored work |
| `grok` | `grok-bot` | Qualified actual Grok Bot independent review |

Unqualified model aliases (`unknown`, `default`, `grok-bot-default`, …) still
refuse. Chat ACCEPT or branding alone is not qualification.

## Trust chain (enforced before signing)

1. `LANGGRAPH_PRIMARY_ACCEPTANCE_MODE` must be exactly `provider-v3-two-party`
   **before** configuration secrets or registry bytes are opened.
2. Installed config is digest-pinned and must **not** embed a private key.
   The Ed25519 private key is read only from
   `<root>/broker/private.key` after canonical containment checks reused from
   `native_acceptance_issuer`.
3. Owner-pinned registry (`registry_path` + `registry_sha256`) is loaded and
   passed through `provider_acceptance_v3.validate_adoption`. The installed
   signer and observation keys must match the enrolled
   `independent_review` and `observation_authority` entries; revocations refuse.
4. Challenge context comes only from
   `<root>/authorizations/<challenge>.json` (`base` + `context`). It must match
   the adopted repository, PR, packet digest, and activation identity. Caller
   path labels are not “adoption.”

## Protected capture contract (internal)

Capture bytes at `<root>/captures/<challenge>.json` use the **internal**
document version `director-protected-independent-review-capture/v1`. This is a
documented protected-custody contract, **not** a claim of real native product
wire qualification (that remains the separately signed observation).

Required fields:

- `verdict` must be exactly `ACCEPT` (rejected / missing / malformed terminal
  reviews refuse; opaque `CHANGES_REQUIRED` bytes cannot force ACCEPT)
- `challenge`, `packet_digest`, `candidate_commit`, `candidate_tree` must equal
  the authorized context
- `session_id`, `provider_family`, `product` must match enrollment and the
  signed observation

## Observation

`<root>/observations/<challenge>.json` must verify under `OBSERVATION_DOMAIN`
with the enrolled observation key. Session, capture digest, context, product,
and family must match the protected capture and authorization.

## Publication

- Durable single-claim in `<root>/broker/claims.sqlite3` (failures remain
  claimed; no dispatch/retry/reset)
- Output only at `<root>/public/<challenge>.json` (filename is challenge-derived;
  alternate paths are not accepted)
- Receipt signature uses the v3 `DOMAIN` expected by
  `verify_two_party_acceptance`

## CLI

```text
python -m director_orchestrator.native_v3_review_issuer collect \
  --config /run/issuer/issuer.json \
  --config-sha256 <64-hex> \
  --challenge <64-hex-challenge>
```

## Non-goals

- No provider launch, host bridge, or credential acquisition
- No manufacturing of observation or owner-adoption signatures
- No CI or security receipt roles
- No merge, activation, paid dispatch, or runtime setting changes
- No inventing a native Grok Bot wire protocol

## Dependencies reused (not duplicated)

- Path/custody: `canonical_native_path`, `contained_native_path`, `claim`,
  `write_new`, `context_from` from `native_acceptance_issuer`
- Adoption: `validate_adoption` from `provider_acceptance_v3`
- Full Windows SID/ACL `Config.identity` / `verify_boundary` from the v2 native
  issuer remain the production broker boundary for **dispatching** issuers; this
  collect-only module does not weaken those guards and does not claim to replace
  them. Outer `verify_two_party_acceptance` still rejects unregistered attacker
  keys.

## Tests

`tests/test_native_v3_review_issuer.py` covers successful collect through the
complete existing v3 verifier fixtures, plus refusals for rejected/malformed
terminal capture, mismatched session/context/candidate, unprotected
custody/invalid adoption, durable claim replay, wrong mode/expiry/observer key,
author-family reviewers, and unqualified aliases.
<!-- [[Grok Bot | unreported | 2026-09-10 | 16:00 UTC | END]] -->
