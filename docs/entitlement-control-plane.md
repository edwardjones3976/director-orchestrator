<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:25 UTC | BEGIN]] -->
<!-- [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:32 UTC | BEGIN]] -->
# Entitlement control plane (default off)

**Status:** Mechanism only. Default off. No readiness router, provider dispatch,
or external-effect consumer wires the dormant action-boundary query.

SQLite rights provisioning, startup authorization snapshots, and the pending
readiness hunk are removed. Entitlements live in PostgreSQL with immutable
generation/event lineage, generation CAS, idempotent stored outcomes, and
mandatory owner notices.

## Contracts and signing

| Contract | Purpose |
| --- | --- |
| `SignedEntitlementTemplate/v1` | Owner-signed profile template composed with universal `shared-integrations/v1` |
| `ProjectRegistration/v1` | Owner-signed project/profile registration with destinations, scopes, budgets |
| `InstallAttestation/v1` | Authenticated install evidence with non-self-referential verifier signature |
| `EntitlementEvent/v1` | Immutable `GRANT`, `SUSPEND`, `REVOKE`, `RESTORE`, `SUPERSEDE`, `UNINSTALL` |
| `OwnerNotice/v1` | `PENDING`, `DELIVERED`, `FAILED`, `ACKNOWLEDGED` |

Signing bytes exclude signature fields. Subjects never declare rights.

## Configuration (secret-reference / file only)

| Variable | Meaning |
| --- | --- |
| `ENTITLEMENT_CONTROL_PLANE_ENABLED` | Default `false` |
| `ENTITLEMENT_POSTGRES_DSN_FILE` | File containing the Postgres DSN |
| `ENTITLEMENT_POSTGRES_SCHEMA` | Versioned schema (default `entitlement_control_plane_v1`) |
| `ENTITLEMENT_TRUSTED_VERIFIER_SECRET_FILE` | File containing the install attestation verifier secret |
| `ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE` | File containing the distinct route and destination-health verifier secret |
| `ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE` | File containing the owner authority secret |
| `ENTITLEMENT_ALLOW_INSECURE_LOCAL_POSTGRES` | Loopback/approved disposable endpoint only; parsed host, never substring |

## Dormant action-boundary query

`EntitlementControlPlaneStore.query_action_boundary()` intersects live entitlement
with registration generation, template composition, build digest, repository/ref/path,
budget, notice deadline (five-minute fail-closed without scheduler), and audit lineage.

Runtime authorization proofs are single-use effect claims. The first exact,
fresh, signed proof can reserve its installation/action/destination/effect/build/source
binding. An exact replay receives the stored binding but is denied permission to
execute the effect again; reuse of the nonce with any different binding fails closed.
<!-- [[Cursor Agent | Composer 2.5 | 2026-09-04 | 21:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 23:25 UTC | END]] -->
