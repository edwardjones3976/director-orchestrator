<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | BEGIN]] -->
# Native AGY issuer authentication evidence

This consumer permits AGY issuer participation only after independent enrollment.
It does not acquire authentication, enroll an authority, or qualify this host.
Native AGY stream output does not observe account identity; requested project and
authentication labels remain requests. Real authenticated profile/project readback
and a complete credential-state inventory remain operator prerequisites.

The installer accepts `NativeAuthAccountId` and `NativeAuthRegistrySha256` as
explicit owner pins, creates an empty protected `native-auth` directory, and records
the actual accepted Git tree in `installation.json`. It creates no trust records,
evidence, credentials, or signatures. Legacy manifests missing the tree need a
fresh accepted installation; the consumer never infers a tree.

The protected `native-auth/registry.json` uses version
`director-native-auth-trust/v1`, the existing serialized `security`,
`independent_review`, and `ci` authorities, `revoked_deployments`,
`revoked_enrollments`, and the existing provider-v2 `adoption_context`,
`adoption_package`, and integer `adopted_at`. Its canonical SHA256 must match the
installed configuration pin. Adoption must accept this installed commit/tree and
repository, include all required activation CI checks, and use earlier independent
families, principals, and deployments. This is an owner-provisioned trust anchor;
an untrusted worker cannot supply one. Historical acceptance is verified at its
pinned adoption time; current enrollment freshness and revocation are checked now.

`native-auth/enrollment.json` contains exactly `evidence` and a base64 Ed25519
`signature` over `director-native-auth-enrollment/v1` plus a NUL byte and canonical
evidence JSON. Evidence has version `director-native-auth-enrollment/v1`, a unique
`enrollment_id`, `authority_role` (security or independent_review), `binding`
from `installation_binding`, integer `issued_at`/`expires_at` (maximum one hour),
`authentication_mode: native-account`,
`observation_source: native-authenticated-profile-and-project-readback`,
`readback_digest`, and `credential_files` mapping native-home relative paths to
SHA256 values. The independent existing authority signs only after inspecting
actual native readback in protected `native-auth/readback.bin` and all effective
credential state. Opaque bytes alone prove no identity; the independently trusted
signature attests the observation. Never substitute owner prose or synthetic test
artifacts for that inspection.

The binding covers installed code/config inventory, fixed identities, actual
executable/entrypoint/hook hashes, native home, project and account. The broker
checks before source preparation/disclosure and again before signing; the worker
checks before and after execution. Each request pins the exact enrollment digest.
Missing, expired, changed, revoked, foreign or self-adopted evidence fails closed.
Credential refresh requires renewed evidence; ordinary unrelated session-cache
writes are not credential drift. Revocation/pin updates require the protected
operator provisioning path, never the provider or coordinator.

Synthetic Ed25519 tests demonstrate the positive parser path and negative boundary
checks. No real host enrollment or live issuer acceptance is claimed by those tests.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | END]] -->
