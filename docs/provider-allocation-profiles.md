<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:10 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:00 UTC | BEGIN]] -->
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

## Dual activity ledgers

The dual OpenAI activity ledgers (`openai_director_activity` and `openai_direct_production`) ensure that production credit is never derived from Director activity. 
Director/control-plane activity includes intake, planning, decomposition, packet preparation, capability-based routing, team communication, synthesis, acceptance supervision, completion reporting, and owner escalation. 
Direct-production activity encompasses bounded production assignments authored by OpenAI under ordinary authorship, review, CI and merge gates.
The ledgers are distinct, append-only SQLite files. A rolling 7-day observation window evaluates prospectively from the first recorded event. A minimum of 20 total valid events must be recorded before history is considered sufficient, and a floor of 75% Director activity must be maintained to avoid a utilization hold. Unknown activity kinds are recorded separately and are not included in the utilization ratio.
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
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:00 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:10 UTC | END]] -->
