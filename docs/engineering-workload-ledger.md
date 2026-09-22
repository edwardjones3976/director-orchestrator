<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]] -->
# External engineering assignment ledger

`scripts/engineering-workload.py` provides a durable local journal for engineering
assignments made outside the operational LangGraph task ledger. It never calls a
provider, spends money, grants repository access, accepts a review, merges, or
deploys. The existing `TaskStore` remains the operational runtime journal; an empty
runtime table does not establish an empty external engineering history.

"External engineering" includes real coding and review assigned to Codex
subagents, Claude, Gemini/Antigravity, actual Grok Bot and other enrolled products.
It does not mean only a third-party vendor or an external command-line process.
Use one journal and one stable assignment ID for each bounded work packet across
tools and sessions. Include `parent_assignment_id` for delegated work. A parent
that only coordinates child coding assignments is `coordination`; do not credit
the same implementation again as parent production. Multiple substantive child
assignments have their own distinct packet IDs and each consumes an admission.

The smallest viable implementation uses SQLite transactions and the existing
allocation/retry policy. No service, dependency, parallel dispatcher or dashboard
is introduced. The journal is an operator control: restrict its directory to the
trusted coordinator, keep it outside Git, and back it up with the source receipts.
A user able to edit this database or its enrollment inputs remains trusted. The
module validates evidence structure and consistency; it does **not** authenticate
the contents of referenced evidence or attest complete historical coverage.

## What the twenty means

Let `W` be the most recent nineteen recorded, non-coordination assignments plus
the proposed new assignment, and `c(p)` the number attributed to allocation bucket
`p` in `W`. Admission uses the same twenty-slot startup denominator as the charter:

```
prospective_share(p) = 100 * c(p) / 20
normal band(p) = target(p) ± 3 percentage points   (advisory routing preference)
admission condition(p) = target(p) > 0
```

Only real reservations fill slots. Unfilled startup slots are reported as unfilled,
not assigned to fictitious providers. The normal band supplies the under-target
routing preference only; it never refuses admission. The owner retired the
former target+5 hard admission ceiling on 2026-09-21 as outdated. `historical_maximum_slots`
and `historical_hard_caps_ok` in the status payload retain that retired number for
reference; they are explicitly non-enforcing and never block or refuse a
reservation. `next_eligible_routes` ranks admitted-capacity candidates by their
prospective distance below target. The caller must still apply capability, exact
scope, quota, reviewer independence, security and budget gates.

Every real reservation consumes one assignment once. Reserved, failed, rejected,
cancelled and ambiguous rows remain in admission history. Settlement never refunds
capacity. The twenty-first assignment removes the oldest from the current window,
not from storage. A retry remains a child attempt of its original assignment; it
does not create another productive credit. Coordination is record-only:
`begin-attempt` refuses it before inserting an attempt or issuing a permit. It can
settle with evidence of completed coordination, but cannot launch provider work or
inflate this window. Coding, review or other provider work must use its actual
counted assignment kind. This implementation issues **no authenticated
productive-work credit**; success and cost fields remain correlated operator
reports pending their independent verification.

SQLite `BEGIN IMMEDIATE` serializes the read/check/reserve operation across
processes. The assignment ID and product/kind/packet-digest uniqueness checks stop
replays. Calling `reserve` again returns the prior reservation with
`dispatch_permitted: false`; only a new `begin-attempt` yields a one-time workload
permit. Immutable database triggers prevent normal SQL updates/deletions of the
journal. These are consistency controls, not protection against its trusted owner.

## Product identity and allocation policy

The owner selected **the existing five-provider allocation**: OpenAI 40%, Claude
25%, Cursor 15%, direct Grok 10% and Antigravity 10%. The canonical engineering
profile remains unchanged. It has **no actual Grok Bot allocation binding**.

| Product ID | Canonical allocation bucket | Identity constraint |
| --- | --- | --- |
| `openai-codex` | `openai` | Includes actual OpenAI implementation subagents |
| `claude-code` | `claude` | Actual Claude-authored work |
| `cursor` | `cursor` | Cursor product, including its managed model usage |
| `xai-grok` | `grok` | Direct xAI Grok, never actual Grok Bot |
| `gemini-antigravity` | `antigravity` | Actual Gemini/Antigravity work |
| `grok-bot` | Missing | Requires an explicit owner policy decision |

Use this canonical policy in the prospective bootstrap manifest:

```json
{
  "kind": "canonical",
  "profile": "engineering"
}
```

`policy-preview --input canonical-policy.json` reads the existing allocation
authority and reports these numbers without creating a database:

| Allocation bucket | Target | Normal full-window count | Retired hard ceiling (reference only) |
| --- | --- | --- | --- |
| OpenAI | 40% | 8 | 9 |
| Claude | 25% | 5 | 6 |
| Cursor | 15% | 3 | 4 |
| Direct Grok | 10% | 2 | 3 |
| Antigravity | 10% | 2 | 3 |

The retired-ceiling column is historical reference only; the owner retired this
target+5 admission gate on 2026-09-21 and it no longer blocks a reservation.

For actual Grok Bot, diagnostics report `allocation_binding_missing`; enrolling
it without a binding fails with that reason. It does not borrow the direct Grok
or Cursor allocation, and their credentials, quota and actors remain separate.

Generic custom-product policy support remains available for a future explicit
owner decision. Its targets must total 100, and zero-target products are ineligible.
Any custom weights or new Grok Bot bucket binding require separate recorded
`policy_authorization`. None is selected by this owner decision. No policy changes
revive a quota-limited route or attest an underlying managed model family.

Each enrolled route fixes `route_id`, `product_id`, `actor_id`, `provider_identity`,
`model_family`, `eligible`, allowed `roles`, and `enrollment_evidence`. A route not
enrolled or explicitly ineligible cannot be used. Product/model labels submitted
in an assignment do not replace this operator enrollment. The same route binding
cannot be overwritten. A missing product can be enrolled later with `enroll-route`.

Several distinct actors may have separate route IDs within the same product; they
share its allocation bucket and do not obtain separate quota. An enrolled identity
is never overwritten or renamed into another actor.

Review admission requires an exact 40-character `candidate_sha`, material-author
identities, `authorship_evidence`, and an explicit `review_policy`:

- `ordinary_pr` applies the current per-acting-identity rule: the reviewer must not
  be any material-author actor, including a material-author parent. Two distinct
  Codex actors may share a product or family. Unknown Grok Bot model-family metadata
  does not prohibit a distinct Codex actor reviewing Grok Bot's source work.
- `native_acceptance` additionally requires known, distinct product, provider and
  model-family identities because that acceptance surface requires those checks.

Unknown author/actor provenance remains unknown; a model label cannot fill it in.
These checks compare **operator-attested identities**, not authenticated native
receipts, and do not determine whether the caller omitted a material author. The
downstream review consumer still must authenticate provenance and select the
correct review-policy scope. This ledger creates neither a canonical `ACCEPT`
verdict nor merge eligibility.

## Establish history once, without inventing it

All CLI examples are run from the repository with `PYTHONPATH=src` and the existing
Windows virtualenv Python. Replace the example paths with a single selected
operator-controlled journal; do not create a separate quota journal per session.
Do not point this tool at the runtime SQLite database.

```powershell
$env:PYTHONPATH = 'src'
& .venv-win\Scripts\python.exe scripts/engineering-workload.py --db <journal.sqlite3> --human status
& .venv-win\Scripts\python.exe scripts/engineering-workload.py policy-preview --input <canonical-policy.json>
```

`status` on a missing path reports `history_unknown_bootstrap_required` and creates
nothing. To initialize, prepare one JSON manifest with these fields:

| Field | Required meaning |
| --- | --- |
| `operator` | Actual operator making the attestation |
| `authorization` | Owner-authorized establishment: `{ "reference": "...", "sha256": "64 lowercase hex" }` |
| `policy` | Selected policy: `{"kind":"canonical","profile":"engineering"}` |
| `policy_authorization` | Separate owner decision evidence for custom weights/binding |
| `cutover_at` | Actual past or present UTC boundary, with timezone |
| `history_mode` | `verified_last20` or `prospective` |
| `history_evidence` | Source inventory/completeness evidence or quiescent-cutover evidence |
| `routes` | Operator-enrolled actual product identities, roles, eligibility and evidence |

The selected canonical prospective manifest has the following structure. Its
placeholders deliberately fail validation; replace them with actual verified
operator, authorization, cutoff and quiescence evidence **after integration
acceptance and quiescent cutover**, not with invented receipts. Empty `routes`
keeps every provider unavailable until actual actor enrollment is supplied.

```json
{
  "operator": "<actual operator>",
  "authorization": {
    "reference": "<owner-authorized ledger establishment evidence>",
    "sha256": "<actual 64-character evidence digest>"
  },
  "policy": {"kind": "canonical", "profile": "engineering"},
  "cutover_at": "<actual UTC cutover timestamp>",
  "history_mode": "prospective",
  "history": [],
  "history_evidence": {
    "reference": "<verified quiescent cutover inventory>",
    "sha256": "<actual 64-character inventory digest>"
  },
  "prior_history_unknown": true,
  "quiescent_cutover_verified": true,
  "all_future_assignments_required": true,
  "routes": []
}
```

For **`verified_last20`**, include `complete_last20_attested: true` and `history`
containing exactly twenty actual assignments in admission order. Each entry has
`request` (the same immutable reservation payload described below), `admitted_at`,
terminal `outcome` and `evidence`. Every timestamp must precede or equal cutover.
Every entry needs its own source evidence. A partially reconstructed list is not
a complete prior window and must not be padded with fabricated assignments.
Imported over-allocation is preserved and visibly fails current-window checks;
it is not rewritten into a compliant history. Imported evidence remains attested,
not cryptographically authenticated by this CLI.

For **`prospective`**, use an empty `history`, and all three explicit declarations:

```json
{
  "prior_history_unknown": true,
  "quiescent_cutover_verified": true,
  "all_future_assignments_required": true
}
```

The trusted operator must actually inventory/quiesce in-flight provider work at
the boundary and ensure every future assignment enters this journal. A running
provider with an unknown result cannot be treated as quiescent. Creating a fresh
ledger because an existing one reached its ceiling is prohibited. Prospective
startup proves nothing about preceding compliance; status retains that limitation
even after twenty future admissions. This code does not perform or authorize the
quiescence operation itself.

```powershell
& .venv-win\Scripts\python.exe scripts/engineering-workload.py --db <journal.sqlite3> bootstrap --input <manifest.json>
```

Bootstrap is atomic and refuses **any existing initialized database**, including
an empty ledger or a foreign database. An invalid historical item rolls back the
whole import. Existing data is never reset, truncated or silently migrated. Keep
policy changes and recovery as explicit reviewed migrations preserving the same
history; this initial version deliberately provides no reset/force command.

## Reserve, attempt, record, settle

A reservation JSON object contains:

- `assignment_id`: globally stable bounded-assignment ID; optional
  `parent_assignment_id` records its coordination/delegation parent.
- `task_id`, `scope`, `kind` (`implementation`, `review`, `validation`, `research`,
  or `coordination`), and actual `route_id` and matching `product_id`.
- `packet_evidence`: immutable packet reference and SHA256; the packet itself
  carries scope, candidate, tests, stopping conditions and owner authorization.
- `transport`: one existing policy value: `local_cli`, `director_adapter`,
  `github_slack`, `provider_api`, or `browser`.
- `bounds`: positive total `timeout_seconds`, `turn_limit`, `output_bytes_limit`,
  `call_limit` between 1 and 3; `usd_limit` is a positive number or `null`;
  `hard_usd_cap_verified` is a required boolean. A verified cap requires
  `usd_cap_evidence`. Otherwise an `unverified_billing_authorization` evidence
  reference is mandatory. Unknown billing must never be written as zero.
- For review: `review_policy`, `candidate_sha`, `authorship_evidence`, and nonempty
  `material_authors`, each with its actual `actor_id`. The `native_acceptance`
  scope also requires each author's `product_id`, `provider_identity` and
  `model_family`.

```powershell
& .venv-win\Scripts\python.exe scripts/engineering-workload.py --db <journal.sqlite3> reserve --input <assignment.json>
& .venv-win\Scripts\python.exe scripts/engineering-workload.py --db <journal.sqlite3> begin-attempt <assignment-id> --transport local_cli
```

The second command grants a **workload/retry-only permit**. The trusted launcher
must also check all other gates and enforce the returned remaining resource
bounds, including the immutable `deadline_at`, then make at most **one** native
invocation. `assignment_calls_remaining` includes possible later conditional
attempts; it is not a permit for multiple calls now. This ledger is not yet
automatically wired into provider launchers. A process crash after `begin-attempt`
leaves an unknown outcome and cannot be retried as if no dispatch happened.

Record each actual attempt once with `record-outcome <assignment-id> --attempt 1
--input <outcome.json>`. An outcome has `outcome` equal to `succeeded`, `transient`,
`failed` or `ambiguous`, a source `evidence`, and `usage` with exactly `usd`,
`turns`, and `output_bytes`. Each usage value can be `null` when unknown. Retain
the actual observation even when it exceeds the bound; status flags overages.
The outcome cannot subsequently be rewritten to a different result.

One primary attempt is allowed. The second requires a recorded transient failure.
The third requires a materially different transport or `--new-information
<evidence.json>`. Total time, calls, turns, output and any declared dollar budget
are checked before each attempt. Unknown remaining metered cost or unknown output/
turn usage blocks retry. An ambiguous dispatch never automatically retries, even
with new information. Three recorded consecutive non-success outcomes make that
enrolled route unavailable; select an eligible fallback, preserving the failed rows.
Restoring that route requires a separately reviewed recovery migration; creating
a new journal or changing the product's name is not a recovery.

Finally use `settle <assignment-id> --outcome succeeded --evidence
<completion-evidence.json>` (or `failed`, `rejected`, `cancelled`, `ambiguous`).
Provider-work success requires a recorded successful native outcome. Pure
coordination is settled directly with its coordination evidence and still cannot
receive a provider-launch permit or productive credit. An unresolved invocation
can only settle as ambiguous, and ambiguity cannot be relabeled as a clean failure.
Settlement is immutable and retains the admission. If later evidence resolves an
ambiguous outcome, preserve it for an explicit reconciliation change; do not run
the assignment again.

## Historical identity: readable, never new authority

A canonical policy's `canonical_digest` is recorded once, at bootstrap, and is
never rewritten. `status`, `record_outcome`, and `settle` recognize the current
identity, the current-at-base retired hard-tolerance identity, and any listed
pre-retirement identity, so a ledger bootstrapped under an older identity stays
fully readable: its existing rows, an exact idempotent `reserve` replay of an
already-recorded request, and late outcome or settlement reporting for an
already-attempted assignment all continue to work without rewriting metadata.
This is compatibility for reading and reporting prior work, never a second
source of current authority. `reserve` and `begin_attempt` additionally require
the ledger's recorded `canonical_digest` to equal the real current identity
before creating a NEW assignment row or issuing a NEW attempt permit; once the
current identity has moved past a ledger's recorded one,
`canonical_policy_superseded_new_authority_required` refuses new admission or
dispatch for that ledger without touching its immutable history. A ledger
recorded under a superseded identity stays readable and reportable indefinitely.
New admission requires a separately authorized bootstrap under the current
identity. Bootstrap refuses an existing journal; no automatic migration, reset,
or history rewrite is provided.

## Resource-bound result is tri-state, not a share gate

`current_window_checks.reported_resource_bounds_ok` is `true` only when every
admission in the current window has fully known reported usage that stays
within its declared bounds; `false` as soon as any admission has a known
reported bound violation, even while other admissions' usage is still
unknown; and `null` when required reported usage is missing. An unsettled
assignment alone does not force `false` or `null`: known required usage within
bounds can yield `true` before settlement. Unknown required usage is never
treated as compliant, and a known violation is never
hidden by other unknown rows. This result is independent of the retired
allocation hard-cap reference and of the normal-band advisory preference.

## Diagnostics and limits

`status` defaults to JSON; `--human status` renders the current twenty records and
the allocation table. It reports the immutable policy digest, historical evidence,
cutover, targets, shares, deltas, reserved counts, role/identity limitations,
unknown costs, reported bound violations and next allocation-eligible routes.
It reopens the database read-only and checks SQLite integrity, foreign keys and
policy digest; a changed canonical allocation digest fails closed. It recomputes
the current window's retired-hard-cap reference, normal-band checks, and the
tri-state resource-bound result from actual journal rows; none of these are an
admission or completion gate by themselves.
These checks cannot detect work omitted from the journal or authenticate an
operator's imported/native receipts. The completion scorecard must preserve those
limits; an exported `compliant: true` is not evidence.

Validation:

```powershell
$env:PYTHONPATH = 'src'
& .venv-win\Scripts\python.exe -m unittest tests.test_engineering_workload_ledger -v
```

Tests use disposable local SQLite journals, including two separate processes
concurrently reserving OpenAI assignments above the retired hard ceiling. No
provider, live database, Docker container, billing setting or deployment is involved.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:04 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]] -->
