<!-- [[Grok Bot | unreported | 2026-09-10 | 16:06 UTC | BEGIN]] -->
# Native v3 independent-review issuer (collect-only)

Collect-only producer of one **challenge-bound**, **write-once**
`independent_review` receipt for `provider-v3-two-party`.

## Custody (enforced)

Before any private-key read or signature:

1. Mode must be `provider-v3-two-party`.
2. Config is the fixed `<root>/issuer.json` digest pin (no embedded private key).
3. Distinct `broker_sid` / `worker_sid` and non-empty `runtime_roots`.
4. `current_sid()` must equal `broker_sid`.
5. `verify_boundary(...)` from `native_acceptance_issuer` must succeed over root,
   code, config, runtime roots, broker private key, claims, and each consumed
   registry/authorization/capture/observation plus the public destination.
6. Non-Windows hosts and missing ACL evidence **fail closed**.

Private key bytes are read only from `<root>/broker/private.key` after those checks.

## Product vs family

| Product | Family rule |
| --- | --- |
| `codex` | Must be enrolled `openai` |
| `grok-bot` | Independently observed enrolled family (not inferred from Bot branding) |

## Authorization

`<root>/authorizations/<challenge>.json` must match the owner-adopted repository,
PR, packet, activation, and **exact** `material_author_providers` set.

## Capture / observation / publication

Internal capture `director-protected-independent-review-capture/v1` requires
terminal `ACCEPT` and exact challenge/packet/candidate/session binding.
Observation must match. Output only `<root>/public/<challenge>.json` after a
durable SQLite claim.

## CLI

```text
python -m director_orchestrator.native_v3_review_issuer collect \
  --config /run/issuer/issuer.json \
  --config-sha256 <64-hex> \
  --challenge <64-hex-challenge>
```
<!-- [[Grok Bot | unreported | 2026-09-10 | 16:06 UTC | END]] -->
