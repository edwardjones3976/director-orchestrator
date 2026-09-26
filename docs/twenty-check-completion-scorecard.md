<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 17:22 UTC | BEGIN]] -->
<!-- [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:50 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]] -->
# Twenty checks for a completed Director

This scorecard turns the owner's good-run contract into twenty fixed outcomes.
It is a progress report, not an approval mechanism. The required outcome remains
an actual owner request through Slack, a correct result in the same thread in
under 60 seconds, and less than USD 1 of attributable actual charges. Simulated
runs qualify orchestration only; the project is not complete until paid primary
execution is active and a real run meets that outcome.

Every check is mandatory. A verified PASS contributes one point; FAIL and
UNKNOWN contribute zero. Thus `points = sum(PASS)` and `percent = 5 * points`.
Twenty points would establish only this diagnostic contract. Existing exact-SHA,
independence, authorization, security, budget and activation gates still decide
whether any operation is permitted. The report always says
`activation_authorized: false`; no runtime setting is changed by rendering it.

| # | Required outcome | Evidence needed |
|---|---|---|
| 01 | Correct project route and channel | Director 8080, Director n8n 5678, permitted Slack channel, correlated route |
| 02 | One active Slack gateway | All containers and host listeners using the same app identity |
| 03 | Correct active intake | Actual 4xx refusal, 5xx after reservation and transport ambiguity tests |
| 04 | Status really delivered | Scheduled execution correlated with fresh Slack readback |
| 05 | Primary owns intake | Database-derived ownership, disabled legacy intake, positive fencing generation |
| 06 | Durable restart | Same task/checkpoint restored after restart without redispatch |
| 07 | Reconciliation clear | Fresh generation-bound consistency proof for durable projections, batches, assignments and unresolved effects; this collector is not yet implemented. Health can establish a blocker only. |
| 08 | Authenticated owner approval | Original scoped instruction, delivered prompt and signed decision |
| 09 | Durable final reply | One final result plus duplicate-input and ambiguous-post recovery evidence |
| 10 | Ten offline Slack runs | Ten consecutive actual-owner events, independent correctness and measured time, zero operational provider calls |
| 11 | Actual team products | Codex, actual Grok Bot and Gemini/Antigravity transports verified as those products |
| 12 | Fresh activation captures | Correct surface, identity, binding digest, stage and expiry verified natively |
| 13 | Rolling twenty admissions | Authoritative assignment history and recomputed reported resource-bound compliance |
| 14 | Bounded execution | Attempts, cancellation deadline and token/output limits on all selected routes |
| 15 | Monetary control and usage | Enrolled daily cap, reservations and correlated actual charges |
| 16 | Executed exact-head CI | All six required jobs completed successfully with actual steps. A focused PR base-merge rerun also requires the CI scope job to verify a green full run on the previous head and run the mapped tests; the unchanged container and recovery areas are recorded as carried forward. Push runs use the full suite. |
| 17 | Independent ACCEPT | One materially non-author identity, original evidence, exact candidate |
| 18 | Complete Security scan | Sealed complete coverage and disposition bound to exact candidate |
| 19 | Paid primary active | Paid enabled, primary runtime, effect executor and reconciliation worker active |
| 20 | Real paid result | Actual-owner Slack input, independent correctness, one reply, wall time <60s, actual charges <$1 |

## Run it

From the repository, with its Python environment and `PYTHONPATH=src`:

```powershell
$env:PYTHONPATH = 'src'
python scripts/director-scorecard.py --candidate-sha <full-40-character-sha> --evidence-dir <explicit-evidence-folder> --output-dir <report-folder>
```

The default is offline. It produces `completion-scorecard.json` and
`completion-scorecard.md`, with UNKNOWN for facts it has not observed. Add
`--live` to read the explicitly named Director container,
`http://127.0.0.1:8080/health`, deployment topology and known host gateway processes.
For topology checks 01 and 02, also supply `--gateway <exact-running-container-name>`.
The name must match the intended gateway in the complete running-container inventory;
the collector never substitutes a predecessor or the first matching process. It
records the selected container ID and immutable image ID with both observations.
Omitting this option leaves topology UNKNOWN. The Python API accepts `gateway=`;
its existing `topology=False` option still disables topology collection.
Add `--github` with `--live` to query the
candidate's CI through the existing authenticated GitHub CLI. There are no
provider calls, Slack writes, retries, deployments or imported credentials.
With `--live --ledger <journal-path>`, it also reopens the actual assignment
journal read-only. A missing path stays UNKNOWN and is not created.
Add `--security-scan <native-scan-UUID>` to verify that exact scan through the
installed native Security index and original sealed artifacts.

The candidate SHA and observed runtime image/commit are separate. A merged
candidate's CI cannot prove a different running image passed that CI. Runtime
facts are tied to the inspected image's immutable ID and commit label, and the
inspected service must publish the exact localhost Director port.

## Evidence and limits

Only observations produced by the module's fixed read-only collectors in the
current process can earn points. JSON containing `verified: true`, a copied
scorecard, model prose, a declared provider name or an unverified signature
never becomes authoritative. Files in the explicitly selected evidence folder
are linked with SHA256 as **unverified references**. The scan is non-recursive,
limited to 100 entries and 2 MiB per JSON/Markdown file, and skips symlinks and
hidden files. A hash establishes byte identity, not truth or author identity.

Evidence includes source, observed time, expiry, digest and applicable immutable
bindings. Observations expire after five minutes. Missing, stale, future-dated,
wrong-head and wrong-image evidence yields UNKNOWN, never PASS. The diagnostic
trusts the named local host and service; its in-process marker is not a security
sandbox against arbitrary Python code, and it does not attest an uncompromised
host or validate image-label supply-chain provenance.

The first collectors cover 01, 02, 05, 13, 16, 18 and 19. Check 07 can establish
FAIL from a reported historical hold, hold reason or ambiguous effect, including
when other health fields are missing. Zero health counters cannot prove that the
underlying records agree: absent a generation-bound authoritative consistency
collector, check 07 stays UNKNOWN. Worker readiness is separate in 19.
The collectors can establish FAIL for 12 when
the running activation verifier rejects readiness. A true activation flag does
not expose native capture identity/expiry and therefore cannot PASS 12. CI
reuses `state_witness.gh_checks_green`, including its actual-step and required-job
checks. Its temporary ledger is diagnostic and does not imply that production
merge/approval composition has injected the Witness Ledger.

Check 01 observes localhost port bindings, the gateway's permitted channel and
n8n relay configuration, and reads the actual published intake version through
the native workflow-history table in a read-only database transaction. It requires
the supported webhook-to-request connection and the exact static Director intake
URL before performing a health request to that URL's upstream from Director n8n.
A healthy generic alias cannot pass for a workflow pointing elsewhere. Missing
version history or an unsupported/dynamic workflow cannot pass. It never operates
the Edge Engine instance. This
is configuration/routing evidence; behavioral intake and final delivery remain
separate checks. Collection reads environments in memory but emits only selected
booleans/counts and the public upstream environment name, never tokens or the
full environment.

Check 02 inventories all running Docker containers, not only Compose labels,
and compares app-token fingerprints in memory. It distinguishes the gateway
launch command from the known API launch command, which also inherits the token
without starting Socket Mode. An unclassified same-token container yields
UNKNOWN. A bounded Windows process inventory examines known Python/Node/shell
gateway launch signatures without exporting command lines. Unreadable process
details or a host gateway requiring app-identity reconciliation yield UNKNOWN,
with the verified Docker count retained. Multiple matching Docker gateways
yield FAIL. This health inventory does not attest maliciously disguised processes,
other machines or remote socket sessions. Container inventory is capped at 128;
a changing or oversized inventory stays UNKNOWN rather than claiming completeness.

Check 18 resolves only a UUID through the native workbench database at
`~/.codex/state/plugins/codex-security/workbench.sqlite3`. A custom native state
location is not supported. There is no CLI option to designate a copied JSON file
or an arbitrary index as trusted. The collector verifies the index's stored
manifest digest, original canonical artifact and preserved-source hashes,
completed scan/head/base binding, seal time and every changed Git path's coverage.
It requires complete coverage, no exclusions/deferred surfaces and no canonical
findings. Native operational limitations remain in the source manifest and the
report's evidence facts. It trusts the local native index, not a cryptographic
attestation of an uncompromised workstation. It reads SQLite with `mode=ro`,
closes the handle and never calls native scan finalization.
Canonical artifact hashes cover raw bytes. Preserved checkpoint hashes follow
the native producer's sorted compact JSON digest; formatting changes alone do
not alter that semantic digest.

Check 13 recomputes the current journal through `EngineeringWorkloadLedger.status`.
It needs twenty actual admissions after the prospective cutover and reads the
ledger's own authoritative tri-state `reported_resource_bounds_ok`: PASS only
when every admission's reported usage is fully known and within its declared
bounds; FAIL as soon as any admission has a known reported bound violation,
even while other admissions' usage is still unknown; and UNKNOWN whenever
usage is unknown, incomplete, or the value is malformed — unknown usage is
never treated as compliant and never yields PASS. The owner retired the
rolling-twenty allocation hard ceiling on 2026-09-21; normal allocation bands
remain an advisory routing preference and this check does not gate on either.
Imported historical attestations do not count as authenticated proof.
Before twenty prospective admissions exist, UNKNOWN honestly describes the
incomplete window. This observation does not authorize the journal's policy,
authenticate declared provider identities or confer dispatch/production credit.

The other checks intentionally remain UNKNOWN until their source verifiers are
connected. A deployed workflow file cannot establish its failure-after-reservation
behavior, and a saved Slack-readback summary cannot authenticate itself. Each displayed row
names the next proof needed. The reusable run-fact validator enforces count,
unique IDs, actual owner origin, runtime/image binding, correct mode, wall time,
owner-pause arithmetic, final-message count, ambiguity, zero offline calls and
the basis of actual cost. It does not authenticate those facts, and the CLI does
not award points for a user-authored run JSON file.

An estimate or reservation is not actual cost. Unknown cost is not zero.
Machine time excluding an owner pause is shown separately and cannot substitute
for the required under-60-second wall time. Actual Grok Bot is distinct from
direct xAI Grok and Cursor Grok, regardless of shared branding or quota labels.

## Offline trial records

[Offline trial tooling](collect-offline-slack-trials.md) checks a rubric declared
in the original source message. Its JSONL recorder uses exactly
`OFFLINE_RUN_RECORD_FIELDS` (`RUN_RECORD_FIELDS` plus `simulation_label`) and
shares `evaluate_run_record`. The ten-run evaluator keeps its count, distinct-ID,
wall-time, origin, image, cost, ambiguity and zero-call requirements.

A zero-call-verified offline run maps to `actual_cost_usd: 0` with
`cost_basis: simulation_zero_calls`; incomplete operational-attempt authority
stays null. The recorder retains a non-qualifying row and stops further appends.
Saved receipts do not become scored observations, and the in-process check-10
collector remains a separate work order.

## Qualification verdicts with explicit UNKNOWN cost

`evaluate_qualification_record` classifies one canary's **asserted** lineage
row (source event digest, task, assignment/attempt/effect identities, provider
route, exact runtime SHA, image and configuration digest, effect state,
delivery/readback, status-receipt counts, duplicate-effect and policy-violation
counters) into `QUALIFIED_COST_MEASURED`, `QUALIFIED_COST_UNKNOWN`,
`NOT_QUALIFIED` or `UNKNOWN`. The row is untrusted caller input: the function
checks that the asserted links are complete, well-formed, unique and bound to
the exact revision; it cannot verify that any link is real, and its reasons
say "asserted links only". Authenticated collector correlation remains a
separate requirement that no verdict here claims. It is deliberately separate
from the check 10/20 criterion: `evaluate_run_record` still returns `None` for
an unknown cost and never accepts a `QUALIFIED_COST_UNKNOWN` row as a
measured-cost PASS.

Strict inputs: identities must match the compact identifier form and be unique
within each lineage list; `provider_route` must be one of the canonical
`allocation_profiles.LANES` (aliases such as `external_openai` are not
accepted); counters must be exact non-negative integers (booleans and floats
are rejected); a cost must be a finite non-negative number (`inf`/`nan` are
not measured costs). A `ZERO_PROVIDER_EFFECT` simulation is `NOT_QUALIFIED`
for a live provider round trip, as are ambiguity, duplicates, violations,
unverified readback and foreign revision or configuration bindings. Missing or
malformed links and absent status-receipt counts (the Issue #230 dependency)
stay `UNKNOWN`. No verdict from this function contributes points.

## Validation

```powershell
$env:PYTHONPATH = 'src'
python -m unittest tests.test_completion_scorecard -v
```

Tests cover stale/future evidence, forged self-claims, candidate/image mismatch,
missing fields, real CI steps versus empty billing stubs, UNKNOWN cost, incorrect
cost basis, simulations presented as paid runs, duplicate results, measured wall
time, unlabelled duplicate gateways, wrong project routes, unavailable host
visibility, altered native seals and incomplete changed-path coverage. Native
SQLite fixtures verify read-only evidence consumption. No live provider or
Postgres lane is needed.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]] -->
<!-- [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 17:22 UTC | END]] -->
