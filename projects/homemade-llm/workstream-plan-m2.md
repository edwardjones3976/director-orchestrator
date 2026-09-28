<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 00:15 UTC | BEGIN]] -->
# Workstream plan: homemade-llm, phase M2 "DIRECTOR data v1 and adapter v0.1 to v0.3" (DRAFT, planning only)

Do not place secrets, credentials, tokens, or raw private source in this plan. Workflow skills are procedure overlays, not permission grants. Canonical provider roles are in [`docs/team-operating-charter.md`](../../docs/team-operating-charter.md), and project-local boundaries in [`provider-roles-contract.yaml`](provider-roles-contract.yaml).

**Status:** DRAFT, written before the M1 gate. Nothing in it is dispatchable. It exists so that the M1 gate decision can open M2 without a planning gap, and so Codex and Cursor can see the packets they would own. It becomes a candidate for owner approval only after (1) PR #293 and PR #294 merge, (2) the M1 gate report names a winning base with a checked license, and (3) the owner records the M1 gate as closed in `decision-log.md`. Each packet is immutable once approved; changing any bound needs a new owner approval.

## Phase frame

- **Project:** `homemade-llm` (Homemade LLM).
- **Entry:** M1 gate closed: one DIRECTOR base chosen by field-level acceptance on both held-out sets, license checked at its pinned revision (`workstream-plan-m1.md`).
- **Phase outcome (PLAN.md section 3, row M2):** DIRECTOR data v1, about 800 rows, tier-tagged and scrubbed; adapters v0.1, v0.2 and v0.3 trained on the winning base; a scored adapter on both held-out sets.
- **Exit bar (O-015, PLAN.md 2.1):** more than 60 % of packets accepted as-is, field-level, on both held-out sets, graded by two non-author providers; the remainder need at most 10 % of their text changed; 100 % schema-valid through `response_format: json_schema`; at most a 3-point regression on full `ifeval` and `gsm8k`.
- **Early warning (O-015, hard stop):** adapter v0.1 below 45 % at week 4 stops the phase. Under O-020 the answer is a smaller model or better data, never rented compute. Under O-022 the date slips and the bar holds.
- **Calendar (PLAN.md):** weeks 3 to 5, target 2026-10-29. Week 4 is the early-warning check.
- **Budget:** USD 25 for M2 paid calls (O-005). PLAN.md section 5 names exemplar generation as the largest line in the whole project, so the caps below leave headroom for grading and hold generation to a fixed row count.
- **Concurrency:** at most 3 dispatches in flight (O-004).
- **Compute:** local only, RTX 5070 12 GB (O-020), inside the pinned `unsloth/unsloth` image (D-004, D-012). Storage guards from D-003 hold: refuse below 150 GiB free on C:, `C:\llm` capped at 120 GiB.
- **Authoring boundary (O-003):** from M1 on, providers author code, datasets and training runs; Claude authors scaffold, docs and work orders only. This file and the row schema are scaffold. Every script and every dataset row below is provider-authored.

## Owner gates before any packet runs

| Gate | Decision needed | Why it blocks |
|---|---|---|
| G-M2-0 | M1 gate closed: winning base named, license stored with its hash, decision-log row written | Every training packet binds to that base and revision |
| G-M2-1 | Approve this plan, including every packet's bounds and the row schema `schemas/director-data-v1-row.schema.json` | No dispatch under the plan before approval |
| G-M2-2 | Re-verification of provider training-data clauses (O-026: "blocked-page clauses re-verified before the first M2 run"). The OpenAI, xAI and Perplexity pages returned 403 on 2026-09-24, so their tiers rest on search results | A tier change moves a provider between generator and grader, or out entirely |
| G-M2-3 | DVC tracking of both held-out sets under `C:\llm\datasets` (O-007; D-M1-0 says "before M2 training starts") | Training must be reproducible against pinned references |
| G-M2-4 | Owner seeds: which items under `C:\llm\seeds\` may become rows, referenced by SHA-256 only (D-006) | Owner content is the only source without a provider clause |

## Methodology decisions to declare before generation

- **D-M2-1, row contract.** Every row validates against [`schemas/director-data-v1-row.schema.json`](schemas/director-data-v1-row.schema.json). The schema makes the L1 checklist unrepresentable to break: `source_provider` and `terms_tier` are required, excluded and unclear tiers are not valid values, a provider row must carry `terms_verified_at` and the matching tier, and `scrub.gitleaks_clean` and `scrub.redact_pass` are constants set to true. A row that fails validation is not written.
- **D-M2-2, task shape.** The input is the M1 D-M1-1 shape: `objective`, `repository`, `title`, nothing else. The target is the four scored fields (`non_goals`, `constraints`, `acceptance_checks`, `evidence_requirements`). Training on the same shape the shootout scored keeps the M1 and M2 numbers comparable.
- **D-M2-3, leak check.** Before a row is written, its input and target are compared with every item in set A and set B (exact match after the scorer's normalization, plus a title match). The D-M1-0 manifests are recomputed first and the run stops if they differ. Held-out items are never rows (O-027).
- **D-M2-4, source mix (proposal, for the owner to set).** About 800 rows: real history that is outside both held-out sets (at most the handful that exist, PLAN.md 4.3), owner seeds that pass G-M2-4, and the balance as provider exemplars from competing-only-clause providers, split across at least two providers so the adapter does not learn one provider's style (risk R-27). No excluded-tier row, ever.
- **D-M2-5, scrub.** `scripts/scan-dataset.ps1` (gitleaks) runs over the row files, and the existing `redact()` helpers run over every text field. Both must pass before the `scrub` block is written. Personal data is a stop, not a redaction.
- **D-M2-6, training recipe.** QLoRA in the pinned Unsloth image on the M1 winner at its pinned revision, thinking disabled for Qwen3 bases (D-M1-3). v0.1 trains on the first accepted cohort, v0.2 adds critique-and-repair rows for the fields v0.1 failed most, v0.3 is the tuned rerun. Each run writes a report JSON like the M0 smoke runs (base revision, rows, steps, peak reserved VRAM, loss, wall time, GGUF hash). Merged Q4_K_M GGUF per version, served by LM Studio (D-002).
- **D-M2-7, scoring.** The unchanged M1 pipeline: `eval/build_inputs.py`, `eval/generate_candidates.py`, `eval/score_packets.py`, two non-author graders. The same reference manifests, candidate manifest and grade-file digests are recorded. The base model's M1 score on each set is the regression baseline.

## Packets (proposal; authors and reviewers to be confirmed at G-M2-1)

Every packet has one sole author and a reviewer that is neither the author nor from the author's provider family (AGENTS.md rule 3; O-032). Receipts are required for every paid call. A candidate change invalidates earlier review evidence.

| ID | Work | Author | Reviewer | Owned paths | Budget | Timeout, retries | Acceptance and stop |
|---|---|---|---|---|---|---|---|
| M2-S1 | Row validator: `projects/homemade-llm/eval/validate_rows.py` and its test. Validates JSONL against the schema, rejects duplicate `row_id`, enforces `terms_verified_at` on provider rows, runs the D-M2-3 leak check against the held-out manifests. | Cursor | Codex | that script and test | none (subscription) | 45 min, 2 rounds | Review ACCEPT and CI. A planted held-out row and a planted excluded-tier row both fail. |
| M2-D1 | Real-history rows: extract the dispatched assignments that are outside both held-out sets into rows | Codex | Grok (grade only) | `C:\llm\datasets\director-v1\real\` | none | 60 min | Validator clean; count recorded; every row's `receipt_ref` resolves |
| M2-D2 | Owner-seed rows per G-M2-4 | Codex | Grok (grade only) | `C:\llm\datasets\director-v1\seeds\` | none | 30 min | Validator clean; seeds referenced by SHA-256 only |
| M2-G1 | Exemplar generation harness: `projects/homemade-llm/eval/generate_rows.py` and its test. Drives competing-only-clause providers through the existing dispatch path so every call has a receipt; writes schema-valid rows; bounded per call; fails closed on any error | Codex | Cursor | that script and test | none for authoring | 60 min, 2 rounds | Review ACCEPT and CI. Dry run writes zero rows and zero receipts. |
| M2-G2 | Run generation to about 800 rows across at least two providers | Director runs the reviewed harness | validator plus spot grading | `C:\llm\datasets\director-v1\gen\` | **at most USD 10** | per provider batch at most 1 h | Row count and per-provider counts recorded; the ledger figure recorded at each stamp; a batch that would breach the cap is not sent |
| M2-Q1 | Grade a stratified sample of generated rows with two non-author graders; rows below the bar are dropped or routed to critique-and-repair | Director dispatches | two graders from the excluded or unclear tier are allowed here because grading is not training data (O-026) | `C:\llm\reports\m2-grades-*` | **at most USD 6** | 1 h | Acceptance rate per provider recorded; a provider whose rows grade below 50 % is dropped from v0.2 |
| M2-T1 | Training script: `projects/homemade-llm/train/train_adapter.py` and `scripts/train-adapter.ps1`, modelled on the M0 smoke scripts, with the GPU lock, storage guards and report JSON | Cursor | Codex | those scripts and tests | none | 90 min, 2 rounds | Review ACCEPT and CI. A 20-step dry run on the smoke base completes inside the guards. |
| M2-T2 | Train v0.1, v0.2, v0.3 (one at a time, overnight per R-30) | Director runs the reviewed script | report hashes | `C:\llm\runs\m2\`, `C:\llm\models\m2\` | none | at most 8 h per run | Report JSON per run; GGUF hashed; storage guards held |
| M2-E1 | Score each adapter on both held-out sets through the M1 pipeline; run `ifeval` and `gsm8k` | Director runs; two non-author graders | grade receipts | `C:\llm\reports\m2-*` | **at most USD 8** | 2 h per adapter | Field-level acceptance per set; regression versus the base; week-4 early-warning row written for v0.1 |
| M2-R1 | M2 gate report: `project-state-report.md` update with every number `[verified]`, ledger total, and the owner sign-off row | Claude (scaffold, O-003) | non-author reviewer | this folder's record files | none | 60 min | Merged after one exact-head non-author ACCEPT |

Planned paid caps: USD 10 + 6 + 8 = **USD 24, within USD 25**. Anything above the caps is a hard stop, not a rounding.

## Hard stops (in addition to the owner gates)

- v0.1 below 45 % field-level acceptance on either held-out set at week 4 (O-015).
- The M2 envelope (USD 25) would be breached.
- Any excluded-tier row, any unscrubbed row, or any held-out item found in the training set: stop, purge, record (risk R-28).
- Any storage guard crossed: C: below 150 GiB free, or `C:\llm` above 120 GiB.
- More than 3 dispatches in flight.
- A packet's reviewer returns CHANGES_REQUIRED after its allowed revision rounds.
- The GPU lock is held or poisoned.
- Personal data found in any candidate row (D-006).
- Serious-safety stop.

## Not in M2

The local provider lane and any change under `src/director_orchestrator/` (M3, D-007), the COMPANION persona and memory (M4, M5), and any change to the held-out sets after the D-M1-0 pin.

## Coordination with the M1 lane

M1 execution PRs (for example #339, the scorer manifest alignment) keep their authors and evidence. This plan does not reassign them. If the M1 gate changes the winning base, the training packets above rebind to the new base and revision; nothing else changes.

Contribution tags record the author of the plan or artifact. They do not prove the provider identity or satisfy any gate above.
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 00:15 UTC | END]] -->
