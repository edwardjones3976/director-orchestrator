<!-- [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 10:30 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | Gemini 3.8 Flash (High) | 2026-09-13 | 10:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:48 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:09 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | BEGIN]] -->
# Provider allocation profiles

Owner-approved policy; the prospective Claude/OpenAI duty swap is effective 2026-09-08. Engineering is the default for this repository.
The canonical runtime definitions are in `allocation_profiles.py`; select one
profile before plan approval and bind its digest to the immutable assignment plan.

| Provider lane | Mixed | Engineering (default) | Public research | Enterprise |
| --- | ---: | ---: | ---: | ---: |
| OpenAI / Codex production | 27% | 40% | 20% | 32% |
| Claude | 33% | 25% | 25% | 38% |
| Cursor | 10% | 15% | 5% | 5% |
| Grok | 10% | 10% | 5% | 5% |
| Gemini / Antigravity | 5% | 10% | 5% | 5% |
| Perplexity | 5% | 0% | 25% | 0% |
| Firecrawl | 5% | 0% | 15% | 5% |
| GitHub Copilot | 5% | 0% | 0% | 10% |
| Total | 100% | 100% | 100% | 100% |

Canonical profile IDs are `mixed`, `engineering`, `public_research`, and
`enterprise`. Canonical lane IDs are `openai`, `claude`, `cursor`, `grok`,
`antigravity`, `perplexity`, `firecrawl`, and `github-copilot`. Models, native apps, CLI contacts, API transports, and
aliases map to their existing parent lane; they never create a second quota or
an independent reviewer. Gemini and Antigravity share the Antigravity lane.

## Admission and accounting

Assess the prospective last nineteen real assignments plus the candidate using
a fixed denominator of twenty, including startup. Normal tolerance is plus or
minus three percentage points; the hard ceiling is target plus five points.
A zero target is ineligible, even though the numerical ceiling would otherwise
be five. Startup under-allocation is warming, never permission for filler work.
Engineering targets correspond to eight OpenAI, five Claude, three Cursor, two
Grok, and two Antigravity assignments per full window; these are targets, not
mandatory concurrent agents or quotas to fill with unnecessary work.

Reserve admission before provider I/O and retain failed or ambiguous reservations
for admission accounting. Accepted productive output and total observed effort
remain separate records. Retries and repeated tool calls create no new productive
assignment credit. Preserve real history across profile changes and simulate the
ordered specialist, producer, and independent reviewer sequence before approval.
A profile change invalidates the previous plan approval; it never resets history.
Do not silently redistribute shares, relax ceilings, or change an approved
provider after failure. Use an explicitly approved feasible fallback profile or
return a concise owner decision identifying the unavailable capability.

### Previously frozen allocation digests

The 2026-09-08 share swap changes all four profile digests. An approval for the
previous digest does not authorize the new weights. Preserve the old plan,
prestage packet, admissions, outcomes, and their original digests as evidence.
For unfinished work, prepare a new plan and canonical prestage packet under the
current profile, then obtain fresh owner approval before dispatch; never edit an
old digest in place or reset the rolling history to make a plan pass.

The existing gates distinguish these persisted states:

- SQLite rejects reuse of the old assignment binding with
  `WorkloadReservationConflict: workload allocation profile binding changed`.
  The engine records `OWNER_DECISION_REQUIRED` before provider I/O; an existing
  approval or another attempt key does not authorize rebinding the assignment.
- PostgreSQL rejects replay of an old admission with
  `workload_admission_identity_changed`. Reading prior v3 history under a changed
  digest, or admitting new work against that history, holds with
  `workload_history_invalid`. The original rows remain intact and no provider
  attempt is inserted.

For the PostgreSQL history hold, keep affected dispatch held and record the old
and current profile digests and affected assignment identities for an explicit,
reviewed compatibility reconciliation approved by the owner. Fresh plan approval
alone does not clear this hold. This change provides no migration or automatic
digest rewrite; do not delete, reseed, relabel, or silently accept old history.

## Capacity, capabilities, and authority

The owner's account context is OpenAI Pro 20x and Claude Max 5x; other providers
are paid but have materially lower capacity. These owner-reported signals inform
the approved matrix, not a conversion between providers' usage units. Prefer
subscription-backed execution when suitable and authenticated. Separately billed
API execution requires its own known budget and gates. Unknown remaining quota,
cost, or usage telemetry is unknown, never zero or assumed entitlement.

ChatGPT/Codex is the acting project Director; actual runtime provider identity
remains an independently recorded deployment fact. This routing-policy change does not attest deployed provider execution or activate
providers, and it does not alter historical attribution.
OpenAI production is separately assigned and attributed; Director coordination,
Moderator and Participant functions, acceptance supervision, and merge activity
do not earn production credit. The materially authoring OpenAI lane cannot review
or merge its own candidate; different-provider exact-candidate review remains
required. A non-author Director inherits no authorship from delegation, QA, or
merge coordination. Reserve a capable different
non-author reviewer before production; all exact-SHA, security, CI and owner gates
remain binding.

Antigravity is capability-routed within the selected authenticated contract. The
restricted fixture contact requires downstream independent verification before
integration; the separate team contract requires its explicit grants and host
qualification. Allocation grants no exact-SHA acceptance, merge authority,
unrestricted context, or broader outbound packet contract.
Perplexity receives only explicitly approved public research, never private
repository context. Firecrawl and enterprise providers require their actual
capabilities, authorized sources, least privilege, authenticated readiness, and
supported adapters. A nonzero target is not evidence that a route is operational.
GitHub Copilot's production lane is distinct from Copilot Lite hygiene observations;
Lite creates no production credit or independent acceptance authority.

The default LangGraph primary OpenAI transport uses the separately billed Responses
API. OpenAI Pro 20x does not fund that API route. A native Codex subscription
transport is not implemented by this allocation package; subscription capacity
is a target-setting signal, not proof of subscription-backed execution.

The default legacy provider registry does not include OpenAI production, Firecrawl,
or Copilot production adapters. Their allocation authority is implemented in
legacy accounting, but that registry must receive a real authenticated adapter
before those lanes can execute there. The LangGraph primary path has its own
authenticated transport composition. Microsoft 365 enterprise production remains
a typed scope/capability hold in that path until a scoped adapter is supplied.

The legacy default provider registry currently supplies Claude, Cursor, Gemini,
Grok, and Perplexity adapters. Its expanded accounting does not add OpenAI,
Firecrawl, or Copilot execution adapters; these lanes remain unavailable there
unless an actual authorized adapter is composed. The primary LangGraph path uses
authenticated transport discovery and scoped capability checks for its new lanes.
Neither path infers production readiness from Director credentials or allocations.

Record profile ID and digest, logical lane, actual provider/model/transport,
material authors, admission and accepted counts, effort, remaining capacity when
known, costs and categorized waste. Token, spend, runtime and concurrency limits
remain separate. Missing eligible integration or review capability fails closed;
selecting a profile does not install an adapter or deploy LangGraph.

## Proposed conditional allocation profile (Issue 207)

The staged conditional profile descriptor (`src/director_orchestrator/conditional_allocation.py`)
encodes an eleven-lane accepted-production policy target pending future activation:

| Provider / contact lane | Policy family | Target | Soft band (preference) | Hard ceiling |
| --- | --- | ---: | ---: | ---: |
| Gemini / Antigravity | Google | 20% | 17% – 23% | 25% |
| Z.ai | Z.ai | 18% | 15% – 21% | 23% |
| Cursor | Anysphere | 13% | 10% – 16% | 18% |
| Direct Grok | xAI | 13% | 10% – 16% | 18% |
| Grok Bot | xAI | 13% | 10% – 16% | 18% |
| OpenAI / Codex production | OpenAI | 10% | 7% – 13% | 15% |
| Claude | Anthropic | 5% | 2% – 8% | 10% |
| Perplexity | Perplexity | 3% | 0% – 6% | 8% |
| GitHub Copilot | GitHub/Microsoft | 2% | 0% – 5% | 7% |
| VS Code Coding Bot | GitHub/Microsoft | 2% | 0% – 5% | 7% |
| Firecrawl | Firecrawl | 1% | 0% – 4% | 6% |
| Total | | 100% | | |

Cursor, Direct Grok, and Grok Bot are intentionally equal at 13%. GitHub Copilot and
VS Code Coding Bot are intentionally equal at 2%.

### Staged contract constraints and limitations

- **Proposed status only:** The descriptor status is `PROPOSED`. It does not modify
  active `allocation_profiles.py` profiles (`engineering`, `mixed`, `public_research`,
  `enterprise`), default profile, legacy digests, or any existing assignment bindings.
  It confers no runtime readiness, admission authority, or enrollment.
- **Descriptive static contract:** This module is an immutable static descriptor,
  not an admission or independence engine. It exposes no eligibility or admission APIs.
- **Activation prerequisites:** Eligibility begins on or after **2026-09-29 UTC**.
  Activation requires owner acceptance of Z.ai Lite qualification evidence and a
  separate, explicit owner activation decision. No automatic Max purchase, activation,
  or quota redistribution is permitted. All deterministic gates (approval, admission,
  exact-candidate review, Codex Security, CI) remain mandatory. Prerequisites are named
  immutable requirements, not runtime status flags.
- **Trial boundary:** Z.ai Lite evaluation through 2026-09-28 operates under a separate
  trial ledger and earns zero accepted-production credit.
- **Policy families and independence:** Direct Grok and Grok Bot share the `xAI`
  policy family; GitHub Copilot and VS Code Coding Bot share the `GitHub/Microsoft`
  policy family. These are owner-selected policy labels for reviewer-independence
  restriction and anti-duplication; they are not authenticated proof of a managed
  serving model. Contacts and products remain distinct. A family label never proves
  reviewer independence, and unknown contacts fail closed.
- **Unresolved integration:** VS Code Coding Bot has no identified installed independent
  integration. Its binding status is unresolved, its route is unbound, and no route is
  invented. All profile descriptors remain unbound.
- **OpenAI Director-activity floor:** At least 75% of OpenAI's own authenticated activity
  must be Director/control-plane effort. This floor applies to OpenAI's own authenticated
  work and has a distinct denominator from the 10% direct-production target. This
  descriptor does not enforce the floor or provide the two activity ledgers; the dual
  activity ledgers and runtime consumer integration remain required and are not
  implemented by this contract.
- **Window and tolerance:** Evaluated across a 20-slot window (5% per slot), with normal
  tolerance of +/-3 percentage points (preference) and target + 5 points hard ceiling.
  For Firecrawl (target 1%), a 5% mathematical slot is within the hard ceiling (6%),
  but this descriptor conveys no actual admission or execution authority.
- **Specialty boundaries:** Perplexity public research and Firecrawl authorized retrieval
  boundaries are preserved; no permanent specialties exist for other participants.
- **Canonical digest derivation:** The canonical digest dynamically binds all semantic
  descriptor fields (effective date, prerequisites, OpenAI floor and denominators,
  contacts, boundaries) in one immutable payload, ensuring the digest never goes stale.
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 01:09 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:57 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:48 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.8 Flash (High) | 2026-09-13 | 10:25 UTC | END]] -->
<!-- [[Gemini Antigravity | Gemini 3.8 Flash (Medium) | 2026-09-13 | 10:30 UTC | END]] -->
