<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 19:08 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 18:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | BEGIN]] -->

# Primary provider budget production: unsigned preparation and already-signed import

This document covers `src/director_orchestrator/primary_budget_producer.py`,
`scripts/prepare_primary_provider_budget.py`, and
`tests/test_primary_budget_producer.py`. It does not change any existing
source: the closed `ProviderBudget` model, its `verify()` HMAC contract, and
`PrimaryProviderBudgetAuthority.register()` in `antigravity_team.py` stay
exactly as they are.

## Scope

Two operations, kept as separate as the work order that requested them:

1. **Unsigned preparation** (`prepare_unsigned_budget_intent`, and the
   default `prepare` action of the CLI script). Pure function of its
   explicit arguments. No secret is read, no database connection is opened,
   no provider is called, and no limit, identity, grant, approval, or
   account entitlement is inferred. Produces a canonical
   `UnsignedProviderBudgetIntent` document and its digest.
2. **Already-signed import** (`verify_signed_budget`, `import_signed_budget`,
   and the `inspect` action of the CLI script for structural-only, unverified
   checking). Accepts an externally supplied, complete `ProviderBudget`,
   verifies it with the secret already held by a caller-injected
   `PrimaryProviderBudgetAuthority`, and registers it on a connection and
   transaction the caller owns.

Neither the module nor the script signs anything, loads a secret, bootstraps
a database, or exposes a new HTTP surface. Live operator wiring (an actual
service that constructs a `PrimaryProviderBudgetAuthority` from settings and
calls `import_signed_budget` on a real connection) is a separate, later
authorization, exactly as scoped.

## Unsigned preparation

`UnsignedProviderBudgetIntent` has the same required field names as
`ProviderBudget` minus `owner_signature`, which it does not have at all --
not a zero-filled or placeholder one. Its `canonical_document()` is exactly
`self.model_dump(mode="json")`, which equals `ProviderBudget.signing_document()`
for the same field values, so an owner-controlled signer can turn a prepared
intent into a real `ProviderBudget` by computing
`hmac.new(secret, canonical_json_bytes(intent.canonical_document()), hashlib.sha256).hexdigest()`
and attaching that as `owner_signature`. No such signer exists in this
change: constructing one requires a real owner authority secret and is out
of scope here, and this task does not invent one.

Validation beyond `ProviderBudget`'s own field patterns:

- `day` must be an actual UTC calendar date (`date.fromisoformat` succeeds),
  not merely four-two-two digit-shaped text, enforced by a `field_validator`
  on `day` (not a whole-model validator, so a safe CLI error summary can
  name the field without ever including the value). `2026-02-30` matches
  `ProviderBudget`'s own regex but is rejected here.
- All monetary and token fields must be positive integers (already true of
  `ProviderBudget`; restated here so preparation fails the same way before
  anything is signed).

Because the intent has no `owner_signature` field, `ProviderBudget.model_validate`
on its `canonical_document()` always fails with a missing-field error. A
zero-filled or otherwise fabricated `owner_signature` attached by hand still
fails `ProviderBudget.verify()` against any real secret, since `verify()`
recomputes the HMAC over the same canonical document and compares in
constant time. Unsigned output cannot pass as signed authority either way.

## Already-signed import

`verify_signed_budget(budget, *, authority)` is **signature-and-structure
only**. It first forces full revalidation on a plain `dict` of `budget`'s
raw field values -- never on the instance itself, since passing an
already-typed `ProviderBudget` to `ProviderBudget.model_validate`
short-circuits and hands back the same, possibly `model_construct`-built or
`model_copy`-tampered object unrevalidated, and never through a
`model_dump()`/reparse round trip, since serializing a wrong-typed value (a
`bool` into its declared `int` field) can coerce it into something that
silently re-validates. It also enforces the same real-calendar-day rule as
`UnsignedProviderBudgetIntent`, since `ProviderBudget`'s own `day` field is
only a regex pattern. Only then does it verify the HMAC. A structurally
valid but *expired* (not today's UTC day) document can still return
`VERIFIED`: import eligibility is a separate, later check.

`import_signed_budget(*, authority, connection, budget)`:

1. Requires a typed `PrimaryProviderBudgetAuthority` (never constructs one).
2. Calls `verify_signed_budget` (signature and structure, as above).
3. Takes the same per-project advisory lock `PrimaryProviderBudgetAuthority.register`
   itself takes (`pg_advisory_xact_lock` is reentrant within one transaction,
   so taking it twice in the same transaction does not self-block).
4. **After** the lock, rechecks current eligibility: the budget's `day` must
   equal today's UTC day (mirroring `PrimaryProviderBudgetAuthority.reserve`'s
   own rollover check), and the project's current, non-superseded
   registration must still exist with a ceiling at or above this budget's
   (mirroring `register`'s own ceiling check against the same latest active
   row, never a pinned `registration_generation`). This recheck runs before
   *every* successful outcome below, including an idempotent repeat: signature
   verification is never equated with current eligibility.
5. Looks up every row matching this budget's exact consumer key
   (`task_id`/`provider_id`/`project_id`/`day`) and, separately, any row for
   this exact `grant_digest`.
6. If a row for this `grant_digest` exists, its content must match exactly or
   the import is rejected as a conflict; if it matches, any *other* digest
   also matching the consumer key is rejected next, before an
   `ALREADY_REGISTERED` receipt is ever returned -- ambiguity is always
   checked before idempotency is reported, never after.
7. Otherwise (a fresh `grant_digest`), any existing row at all under this
   consumer key is a conflict, checked before insertion. Only then does the
   existing `PrimaryProviderBudgetAuthority.register` perform the actual
   insert, together with its own signature, registered-project ceiling, and
   per-task/day ceiling-drift checks.

No row is ever updated or deleted by this producer; only `SELECT` and the
existing `register`'s own `INSERT` run.

Import never reserves an attempt, marks a budget ACTIVE, dispatches, creates
usage/spend, grants approval, or releases a reservation. Those remain the
unchanged responsibility of `PrimaryProviderBudgetAuthority.reserve`,
`PrimaryProviderBudgetRouting`, and the primary admission path. Atomic
admission and midnight (UTC) revalidation are untouched by this change.

`BudgetProducerStatus` keeps `PREPARED`, `VERIFIED`, `REGISTERED`, and
`ALREADY_REGISTERED` as distinct values; none of them is a primary attempt
lifecycle status (`PENDING`/`ACTIVE`/etc.), and none of the receipts here
ever carries `owner_signature` or other secret material.

## CLI: `scripts/prepare_primary_provider_budget.py`

```
PYTHONPATH=src python scripts/prepare_primary_provider_budget.py \
    --grant-digest <64-hex> --provider-id openai --project-id uppercut \
    --task-id task-one --day 2026-01-01 --task-limit-microusd 500000 \
    --daily-limit-microusd 1000000 --attempt-reservation-microusd 100000 \
    --token-limit 1000
```

Prints the unsigned canonical document and its digest as JSON and exits 0.
Reads no secret, opens no connection, calls no provider.

```
PYTHONPATH=src python scripts/prepare_primary_provider_budget.py \
    --action inspect --input signed-budget.json
```

Structurally validates a supplied document against the `ProviderBudget`
shape and reports its non-secret fields and a digest of its signing
document. Does **not** verify the signature (no secret is available here)
and does **not** register anything; the output says so explicitly. This is
the "CLI may prepare/inspect documents" affordance; it is not a live import
path.

For a `ValidationError` -- the case that can carry attacker-supplied
structure, such as an inspect document's fields or an unexpected top-level
property -- every reported detail comes only from
`ValidationError.errors(include_url=False, include_context=False,
include_input=False)`: a known model field's location and error type, or,
for an unknown top-level property (`extra_forbidden`), the generic
`<extra_field>` location instead of the actual key. Neither branch ever
includes the offending value. This is the CLI's redaction boundary
specifically for `ValidationError`; the other exception types this CLI
catches (`ValueError`, `TeamAuthorizationError`, `OSError`,
`json.JSONDecodeError`) are reported as `str(exc)`, unredacted, and are not
run through this categorical redaction: for example, an `OSError` from a
missing or unreadable `--input` file can include the caller-supplied path.

## Tests

`tests/test_primary_budget_producer.py` covers, in `unittest`:

- Pure unit tests for `UnsignedProviderBudgetIntent` / `prepare_unsigned_budget_intent`
  and for `verify_signed_budget` against a lightweight fake authority,
  including that a structurally valid but expired document still verifies
  while an impossible calendar day does not.
- `StrictSignedInputValidationTests`: six `ProviderBudget.model_construct`
  variants (zero/boolean/fractional `token_limit`, a negative
  `task_limit_microusd`, an invalid provider identifier, an invalid schema
  literal) each fail `verify_signed_budget` and never reach
  `import_signed_budget`'s first SQL call.
- CLI tests, including stdout/stderr redaction: a synthetic marker in a
  forged `owner_signature` never appears on either stream, and a rejected
  `prepare` call names only the offending field, never the value.
- A Postgres-backed suite, `ProviderBudgetImportPostgresTests`, reusing
  `tests.test_entitlement_control_plane.EntitlementControlPlanePostgresTests`
  for setup/teardown (fails rather than skips without
  `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN`, matching every other Postgres-backed
  suite here): new registration, exact-repeat idempotency, conflicting
  content under one digest, a second digest for one consumer key, a wrong
  signature, a missing/exceeded project ceiling, an impossible calendar day,
  an expired (non-today) day, a repeat that must reject once a second
  consumer match, a superseded registration, or a tightened ceiling exists,
  no attempt/spend/usage row ever created, a funded `check_budget_setup`
  preflight after import with those tables still empty, concurrent identical
  imports (exactly one row), concurrent conflicting imports (exactly one
  authoritative row), and a genuine advisory-lock-contention case where a
  second import is provably blocked and then, once unblocked under a
  simulated UTC-midnight rollover, correctly rejected.

## Corrections applied in this revision

Three real-PostgreSQL defects and one CLI echo defect, reported in
`HOST-QA-FINDINGS.md` against the prior candidate, are fixed here:

1. Signed import previously accepted an impossible calendar day and an
   expired (non-today) day. `import_signed_budget` now rechecks both, after
   the project lock, before any successful outcome.
2. An exact-repeat import previously returned `ALREADY_REGISTERED` even when
   a second, distinct `grant_digest` already matched the same consumer key.
   Ambiguity is now checked before idempotency is ever reported.
3. The CLI previously printed the raw `ValidationError` text, including a
   rejected field's actual value, to stderr. It now prints only the error
   type and field location.
4. That CLI fix still echoed an unknown top-level property's own name: since
   Pydantic's `extra_forbidden` location IS the offending key, joining `loc`
   reflected exactly the untrusted input being withheld. `_safe_error_message`
   now reports that case under the generic `<extra_field>` location instead.

An independently reported, offline (fake-connection, no database) structural
gap is also closed: `verify_signed_budget`/`import_signed_budget` previously
trusted the `ProviderBudget` type annotation, so a `model_construct`-built
object bypassing field validation (a zero/boolean/fractional `token_limit`,
a negative limit, an invalid identifier, an invalid schema literal) still
verified and reached the insert. Every signed object is now revalidated from
its raw field values before use.

## Known limitation

`tests/test_primary_budget_producer.py` is not yet listed in
`.agents/skills/postgres-test-lane/SKILL.md`'s explicit suite list. That file
is outside this change's four permitted paths (`tests/test_antigravity_team.py`,
which has the same fail-without-DSN Postgres-backed pattern, is not listed
there either), so this proposal does not edit it. A future change in scope
to touch that file should add this module to the list.

<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 18:50 UTC | END]] -->
<!-- [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 19:08 UTC | END]] -->
