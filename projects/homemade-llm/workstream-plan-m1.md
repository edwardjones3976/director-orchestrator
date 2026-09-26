<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:38 UTC | BEGIN]] -->
# Workstream plan: homemade-llm, phase M1 "Base shootout" (DRAFT for owner approval)

Do not place secrets, credentials, tokens, or raw private source in this plan. Workflow skills are procedure overlays, not permission grants. Canonical provider roles are in [`docs/team-operating-charter.md`](../../docs/team-operating-charter.md), and project-local boundaries in [`provider-roles-contract.yaml`](provider-roles-contract.yaml).

**Status:** this is a DRAFT. No provider is dispatched under it until the owner approves it (decision O-031 follow-up; `project-state-report.md` "Owner gates"). Each packet below is immutable once approved: changing any bound needs a new owner approval.

## Phase frame

- **Project:** `homemade-llm` (Homemade LLM).
- **Entry:** M0 closed (O-031). The M0 record is in `project-state-report.md`.
- **Phase outcome:** one DIRECTOR base model chosen by field-level acceptance on both held-out sets. Its license is checked at its pinned revision, and the owner signs off on the M1 gate.
- **Stopping point:** the M1 gate report, with every number `[verified]`: per-base scores, the license check, and the owner's sign-off row. The target is 2026-10-08. Under O-022, the date slips if needed; the bar holds.
- **Budget:** USD 25 for M1 paid calls (O-005). Spent in M1 so far, from receipts (a lower bound):
  - #280 and first #281 reviews before the gate: USD 0.280142.
  - Set A v2 Grok review: USD 0.1881 (`xai/20260926T193234Z.json`).
  - Set B v2 Grok review: USD 0.2910 over two batches. Batch 2's receipt is `xai/20260926T203729Z.json`. Batch 1 (USD 0.1494) lost its receipt to a same-second name collision; its output is kept at `C:\llm\datasets\heldout\REVIEW-SET-B-V2-GROK-BATCH1.md`, and a reconstruction receipt is pending.
  - Perplexity quantization research: USD 0.03146 (`perplexity/20260926T200001Z.json`).
  - This plan's first Grok review: USD 0.0648 (`xai/20260926T204108Z.json`).
  - **Total USD 0.855502.** Codex and Cursor subscription work carries no per-call USD.
  - Planned caps below: USD 3 + 2 + 3 + 12 = **USD 20**. Spent plus caps is **USD 20.855502, within USD 25**.
- **Concurrency:** at most 3 dispatches in flight (O-004).
- **Compute:** local only, on the RTX 5070 12 GB (O-020).

## Owner gates before any packet runs

| Gate | Decision needed | Why it blocks |
|---|---|---|
| G1 | **D-018:** ratify #280 (the scorer merged before M0 closed), or revert and redo it | Every scored packet depends on the scorer |
| G2 | **Approve this plan**, including every packet's bounds | No dispatch under the plan before approval |
| G3 | **APPROVED by the owner, 2026-09-26 ~20:40 UTC, in the Claude Code session.** **Storage:** after conversion, move the bf16 safetensors from `C:\llm\hf` to `E:\llm-archive\hf-bases-2026-09\` (a copy with hash verification, then a delete from C:), The approved option is the move to E: (not raising the cap). The copy on E: must hash-verify before anything is removed from C:, and each removal is logged with its hash. | With all four weight sets present, `C:\llm` will be about 104 GiB (81.22 GiB measured after the first three). The GGUFs add about 30 GiB, which crosses the cap. |
| G4 | **Methodology:** approve the shootout decisions D-M1-1 to D-M1-4 below, or change them | They must be declared before any scoring (research brief, `C:\llm\reports\m1-quant-research-perplexity-20260926T2000Z.md`) |

## Methodology decisions (declared before scoring)

- **D-M1-1, candidate input.** A held-out item is the answer key, so the model needs a separate *task input* that contains no answer. Proposal:
  - For each item, the input is the packet's `objective` and `repository`, plus the item's title (set A: the source title; set B: the PR title), with nothing else.
  - The model must produce the other four scored fields: `non_goals`, `constraints`, `acceptance_checks` and `evidence_requirements`.
  - `objective` is excluded from field scoring, because the model is given it.
  - The inputs are generated mechanically from the accepted references into a separate `inputs/` folder, and are hash-mapped and reviewed.
- **D-M1-2, quantization.** Proposal: one common level for the primary ranking, Q4_K_M for all four bases, since it is the level that fits the 14B models in 12 GB. Qwen3-8B is also scored at Q8_0 as a quantization-sensitivity check, and that result is reported but not used for ranking.
- **D-M1-3, decoding.** LM Studio `/v1/chat/completions` with `response_format` `json_schema` (the schema is `model-work-packet-v1`, with no `$defs` added), temperature 0 and one sample per item. Fixed seed, where the runtime allows one.
- **D-M1-4, Nemo control.** Mistral-Nemo-Base-2407 is a base model with no chat template. It is scored as a control through the same endpoint, with a fixed three-shot prompt whose examples come from outside both held-out sets, and it is excluded from winner selection (O-034). If LM Studio cannot serve it usefully, that is recorded, and the control is dropped instead of improvised.

## Packets

Every provider packet has one sole author (O-032 once #284 merges; the one-author rule until then) and a reviewer that is neither the author nor from the author's provider family. Receipts are required for every paid call. A candidate change invalidates earlier review evidence.

| ID | Work | Author | Reviewer | Owned paths | Budget | Timeout, retries | Acceptance and stop |
|---|---|---|---|---|---|---|---|
| M1-D1 | Held-out set A references | OpenAI Codex (done; receipt `heldout/codex-20260926T192125Z.author`) | Grok (done, ACCEPT 19/19; receipt `xai/20260926T193234Z.json`; verdict saved at `C:\llm\datasets\heldout\REVIEW-SET-A-JSON-V2-GROK.md`) | `C:\llm\datasets\heldout\set-a-json-v2\` | spent: USD 0.1881 | done | Accepted `[verified from the receipt and saved verdict]` |
| M1-D2 | Held-out set B references (v2) | OpenAI Codex (done) | Grok, 2 batches (owner-approved; done, ACCEPT 25/25 and 25/25) | `C:\llm\datasets\heldout\pr-reconstructed-v2\` | spent: USD 0.2910 | done | Accepted `[verified from the two saved verdicts; batch 1's receipt reconstruction pending]` |
| M1-W1 | Stage four pinned base weight sets | Director script (owner-approved, O-034) | receipt SHA-256 verification | `C:\llm\hf` | none (no paid call) | 6 h per model | In progress. Verified with receipts: Qwen3-8B (`C:\llm\reports\m1-download-Qwen3-8B-20260926T195311Z.json`), Qwen3-14B (`…Qwen3-14B-20260926T195536Z.json`) and Phi-4 (`…phi-4-20260926T201502Z.json`). Mistral Nemo Base is downloading. Done when all four receipts verify and the storage guards hold. |
| M1-C1 | Convert to GGUF (Q4_K_M, plus Q8_0 for 8B) with the pinned Unsloth image's llama.cpp, **one model at a time in this order**: (1) convert, reading the bf16 from `C:\llm\hf` and writing intermediates and the final GGUF to `G:\llm-convert-tmp\`; (2) copy that model's bf16 to `E:\llm-archive\hf-bases-2026-09\`, hash-verify it, then remove it from C: (G3); (3) only then copy the GGUF to `C:\llm\models\m1\` and hash-verify it. `C:\llm` never holds both the bf16 and the GGUF of more than the model in progress. | Cursor (`composer-2.5` pinned) writes `projects/homemade-llm/eval/convert_bases.ps1` and its test | Grok | that script and test; outputs `C:\llm\models\m1\` | ≤ USD 3 | 60 min authoring, 2 revision rounds | Script review ACCEPT and CI. Every GGUF loads in LM Studio, and each output is hashed. Stops before any storage guard is crossed. |
| M1-I1 | Build task inputs per D-M1-1 | Cursor (`composer-2.5`) writes `projects/homemade-llm/eval/build_inputs.py` and its test | Grok | that script and test; outputs `C:\llm\datasets\heldout\inputs\` | ≤ USD 2 | 45 min, 2 rounds | Review ACCEPT and CI. Inputs contain no scored-field text (a mechanical check). |
| M1-G1 | Candidate generation harness (D-M1-3) | OpenAI Codex writes `projects/homemade-llm/eval/generate_candidates.py` and its test | Grok | that script and test; outputs `C:\llm\runs\m1\candidates\<base>\` | ≤ USD 3 | 60 min, 2 rounds | Review ACCEPT and CI. Output passes the scorer's schema check. It is bounded per item (timeout) and fails closed on any server error. |
| M1-R1 | Run generation for 4 bases × 2 sets, one base loaded at a time | Director runs the reviewed harness | output hashes | `C:\llm\runs\m1\` | none | per base ≤ 2 h | Every item produces a candidate or a recorded failure |
| M1-S1 | Score with the scorer (after G1) | Director runs it | two non-author graders (L1 checklist) | `C:\llm\reports\m1-*` | ≤ USD 12 (grading) | per run ≤ 1 h | Field-level acceptance per base and set; reproducible from hashes |
| M1-L1 | License check of the winner at its pinned revision | Director (web read) | persisted page hash | `C:\llm\reports\m1-license-*` | none | 15 min | The license text is stored with its hash |

## Hard stops (in addition to the owner gates)

- The M1 envelope (USD 25) would be breached.
- Any storage guard would be crossed: C: below 150 GiB free, or `C:\llm` above 120 GiB.
- More than 3 dispatches in flight.
- A packet's reviewer returns CHANGES_REQUIRED after its allowed revision rounds.
- The GPU lock is held or poisoned (`C:\ProgramData\homemade-llm\gpu-poisoned.txt`).
- Serious-safety stop.

## Not in M1

DIRECTOR data generation and adapter training (M2), the local provider lane (M3), and any change under `src/director_orchestrator/`.

Contribution tags record the author of the plan or artifact. They do not prove the provider identity or satisfy any gate above.
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:38 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:40 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:45 UTC | END]] -->
