<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 06:34 UTC | BEGIN]] -->
# No receipt, no state: the Witness Ledger

Adopted 2026-09-12 from the four-design, three-judge panel recorded in the
good-test-run standard. Module: `src/director_orchestrator/state_witness.py`.

## The rule

An agent cannot make **merged**, **green**, **reviewed**, **authorized**,
**the document says**, or **provider down** true by typing it. Each is a claim
class, and a claim class is true only as a row in the insert-only
`state_witness` table. Only an oracle wrapper writes rows.

| Claim class | Oracle | MATCH means |
| --- | --- | --- |
| `gh_pr_merged` | `gh api repos/{r}/pulls/{n}` | `merged` equals the claim and the head SHA is the one claimed |
| `gh_checks_green` | `gh api …/actions/runs` + `…/jobs` | every job at the SHA concluded `success` **and ran at least one step** — a zero-step billing stub is never green |
| `owner_authorization` | Slack message fetch | the message at `channel/ts` exists and its author is the owner's user id |
| `doc_read` | filesystem read | the bytes at `path` hash to the claimed sha256 |
| `provider_reachable` | none yet | never — `UNOBSERVABLE` by construction |

## The four enforced properties

1. **Gates consume rows and re-execute when they act.** `witnessed(kind, subject)`
   answers only from rows within their ttl (10 minutes). `verify_packet()` runs
   the oracle again and writes a fresh row before answering. Wired gates in this
   version: the exact-SHA merge (`OracleMergeService(state_witness=…,
   checks_green_oracle=…)` writes a `gh_pr_merged` row from its final snapshot
   and consumes a `gh_checks_green` row from a real checks oracle that sees job
   step counts — without that oracle the green claim is UNOBSERVABLE and the
   gate denies) and the durable authorization write (`TaskStore.record_external_openai_production_authorization`
   requires a fresh `owner_authorization` MATCH row for the exact digest).
2. **Unobservable means deny.** No row, no oracle, a probe that raised or timed
   out, a stale row: all `UNOBSERVABLE`, and every gate refuses. `MATCH` is
   never the default.
3. **The loop closes inside the task.** A refusal carries `(claimed, observed)`;
   `Refusal.feedback()` is the payload for the claiming agent's next turn.
   `strikes(agent, kind)` counts mismatches; only a MATCH row written by a gate
   (`oracle` ending in `#gate`) clears them — cheap true trivia does not.
4. **Unwitnessed sentences still reach the owner, stamped.** `stamp_unwitnessed()`
   runs on every Director Slack post (`slack_bot._tag`) and prefixes any
   sentence asserting a state claim that cites no `witness:<id>`. Cosmetic;
   nothing transitions on prose.

## Cost

No model calls. Oracles are `gh`, Slack, and file reads; a full packet check is
under three seconds. Rows are append-only at the engine level (UPDATE and
DELETE triggers abort), the same discipline as the delegation audit ledger.
Growth is bounded: past `max_rows` (default 1,000,000) every write raises
`WitnessLedgerFull`, so every gate denies until `rotate()` moves the full file
aside under a dated name and starts an empty one; nothing is deleted. Only
oracle wrappers write rows (`_record` is module-private); any exception inside
an observation is recorded as UNOBSERVABLE; a persisted class or status this
version does not know is not evidence; repository and pull-number inputs are
validated before any `gh` endpoint is built.

## Not in this version

A packet-parsing hook that extracts claims from provider output (callers build
`Claim` objects today), a `provider_reachable` probe, and the strike-driven
automatic-verification routing. Each is additive.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 06:34 UTC | END]] -->
