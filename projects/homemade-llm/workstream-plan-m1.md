<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 02:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:38 UTC | BEGIN]] -->
# Workstream plan: homemade-llm, phase M1 "Base shootout" (approved under O-038 and O-039; M1 gate closed 2026-09-28)

Do not place secrets, credentials, tokens, or raw private source in this plan. Workflow skills are procedure overlays, not permission grants. Canonical provider roles are in [`docs/team-operating-charter.md`](../../docs/team-operating-charter.md), and project-local boundaries in [`provider-roles-contract.yaml`](provider-roles-contract.yaml).

**Status:** APPROVED by the owner on 2026-09-26 (O-038 plan, O-039 method, O-037 for G1; see `decision-log.md`), at head `9875dca`. The gate table below records each decision. Execution ran 2026-09-26 to 2026-09-27 and the **M1 gate closed on 2026-09-28** with the owner's signature (O-052); the outcome is in "Status at the M1 gate" at the end of this file and in `project-state-report.md`. Each packet was immutable once approved; no bound was changed.

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
| G1 | **Decided, O-037 (2026-09-26):** the owner kept #280 and ratified its early merge as a documented exception; the pre-gate cost stays recorded | Every scored packet depends on the scorer |
| G2 | **Decided, O-038 (2026-09-26):** plan approved at head `9875dca`, including the USD 25 ceiling, each packet's cap, at most three dispatches in flight, and the storage guards | No dispatch under the plan before approval |
| G3 | **APPROVED by the owner, 2026-09-26 ~20:40 UTC, in the Claude Code session.** **Storage:** after conversion, move the bf16 safetensors from `C:\llm\hf` to `E:\llm-archive\hf-bases-2026-09\` (a copy with hash verification, then a delete from C:), The approved option is the move to E: (not raising the cap). The copy on E: must hash-verify before anything is removed from C:, and each removal is logged with its hash. | With all four weight sets present, `C:\llm` will be about 104 GiB (81.22 GiB measured after the first three). The GGUFs add about 30 GiB, which crosses the cap. |
| G4 | **Decided, O-039 (2026-09-26):** D-M1-1 to D-M1-4 approved as written below, with the owner's caveat that temperature 0 measures greedy output and is not a claim of byte-identical runtimes | They must be declared before any scoring (research brief, `C:\llm\reports\m1-quant-research-perplexity-20260926T2000Z.md`) |

## Methodology decisions (declared before scoring)

- **D-M1-1, candidate input.** A held-out item is the answer key, so the model needs a separate *task input* that contains no answer. Proposal:
  - For each item, the input is the packet's `objective` and `repository`, plus the item's title (set A: the source title; set B: the PR title), with nothing else.
  - The model must produce the other four scored fields: `non_goals`, `constraints`, `acceptance_checks` and `evidence_requirements`.
  - `objective` is excluded from field scoring, because the model is given it.
  - The inputs are generated mechanically from the accepted references into a separate `inputs/` folder, and are hash-mapped and reviewed.
- **D-M1-2, quantization.** Proposal: one common level for the primary ranking, Q4_K_M for all four bases, since it is the level that fits the 14B models in 12 GB. Qwen3-8B is also scored at Q8_0 as a quantization-sensitivity check, and that result is reported but not used for ranking.
- **D-M1-0, pinned held-out data.** Both reference sets are frozen by manifest.
  - **Method:** SHA-256 over lines of `<folder>/<file>\t<sha256 of file bytes>\n`, one line per top-level `*.json` packet, file names sorted lexicographically.
  - **Set A** (`set-a-json-v2`, 19 files): `14fa95291e61c7e6335681078d3224fdace4768271ed4012a33c4e4ca256e7f5`.
  - **Set B** (`pr-reconstructed-v2`, 50 files): `38a93d79ecd989b171b28a0bf98fb7eaf57dcec2b9ed2c32a66ff760338469bc`.
  - Every generation and scoring run re-computes these digests, stops if they differ, and writes the reference manifest digest, the candidate manifest digest and any grade-file digest into its output.
  - DVC tracking (O-007) of both sets is added before M2 training starts; the manifests above are the M1 pin.
- **D-M1-3, decoding.** LM Studio `/v1/chat/completions` with `response_format` `json_schema` (the schema is `model-work-packet-v1`, with no `$defs` added), temperature 0 and one sample per item. Fixed seed, where the runtime allows one.
  - **Qwen3 non-thinking:** Qwen3-8B and Qwen3-14B run with thinking disabled, `enable_thinking=False` in the chat template. `/no_think` still leaves an empty `<think></think>` block, so it is not enough (`research/base-models-20260925.md`); `train/smoke_train.py` already applies the same setting.
  - Each run records the exact chat template and tokenizer revision used, and the harness (M1-G1) fails an item whose raw output contains a `<think>` block.
- **D-M1-4, Nemo control.** Mistral-Nemo-Base-2407 is a base model with no chat template. It is scored as a control through the same endpoint, with a fixed three-shot prompt whose examples come from outside both held-out sets, and it is excluded from winner selection (O-034). If LM Studio cannot serve it usefully, that is recorded, and the control is dropped instead of improvised.

## Packets

Every provider packet has one sole author (O-032 once #284 merges; the one-author rule until then) and a reviewer that is neither the author nor from the author's provider family. Receipts are required for every paid call. A candidate change invalidates earlier review evidence.

| ID | Work | Author | Reviewer | Owned paths | Budget | Timeout, retries | Acceptance and stop |
|---|---|---|---|---|---|---|---|
| M1-D1 | Held-out set A references | OpenAI Codex (done; receipt `heldout/codex-20260926T192125Z.author`) | Grok (done, ACCEPT 19/19; receipt `xai/20260926T193234Z.json`; verdict saved at `C:\llm\datasets\heldout\REVIEW-SET-A-JSON-V2-GROK.md`) | `C:\llm\datasets\heldout\set-a-json-v2\` | spent: USD 0.1881 | done | Accepted `[verified from the receipt and saved verdict]` |
| M1-D2 | Held-out set B references (v2) | OpenAI Codex (done) | Grok, 2 batches (owner-approved; done, ACCEPT 25/25 and 25/25; batch 1 re-run authenticated, receipt `xai/20260926T225645Z-268799-d4c6.json`) | `C:\llm\datasets\heldout\pr-reconstructed-v2\` | spent: USD 0.2910 plus the USD 0.1508 authenticated re-run | done | Accepted, **with the pr-45 disclosure (O-042):** item pr-45 lacks eligibility and evaluation terms and is retained as a known MINOR reference imperfection, disclosed with every set B result |
| M1-W1 | Stage four pinned base weight sets | Director script (owner-approved, O-034) | receipt SHA-256 verification | `C:\llm\hf` | none (no paid call) | 6 h per model | In progress. Verified with receipts: Qwen3-8B (`C:\llm\reports\m1-download-Qwen3-8B-20260926T195311Z.json`), Qwen3-14B (`…Qwen3-14B-20260926T195536Z.json`) and Phi-4 (`…phi-4-20260926T201502Z.json`). Mistral Nemo Base is downloading. Done when all four receipts verify and the storage guards hold. |
| M1-C1 | Convert to GGUF (Q4_K_M, plus Q8_0 for 8B) with the pinned Unsloth image's llama.cpp, **one model at a time in this order**: (1) convert, reading the bf16 from `C:\llm\hf` and writing intermediates and the final GGUF to `G:\llm-convert-tmp\`; (2) copy that model's bf16 to `E:\llm-archive\hf-bases-2026-09\`, hash-verify it, then remove it from C: (G3); (3) only then copy the GGUF to `C:\llm\models\m1\` and hash-verify it. `C:\llm` never holds both the bf16 and the GGUF of more than the model in progress. | Cursor (`composer-2.5` pinned) writes `projects/homemade-llm/eval/convert_bases.ps1` and its test | Grok | that script and test; outputs `C:\llm\models\m1\` | ≤ USD 3 | 60 min authoring, 2 revision rounds | Script review ACCEPT and CI. Every GGUF loads in LM Studio, and each output is hashed. Stops before any storage guard is crossed. |
| M1-I1 | Build task inputs per D-M1-1 | Cursor (`composer-2.5`) writes `projects/homemade-llm/eval/build_inputs.py` and its test | Grok | that script and test; outputs `C:\llm\datasets\heldout\inputs\` | ≤ USD 2 | 45 min, 2 rounds | Review ACCEPT and CI. Inputs contain no scored-field text (a mechanical check). |
| M1-G1 | Candidate generation harness (D-M1-3) | OpenAI Codex writes `projects/homemade-llm/eval/generate_candidates.py` and its test | Grok | that script and test; outputs `C:\llm\runs\m1\candidates\<base>\` | ≤ USD 3 | 60 min, 2 rounds | Review ACCEPT and CI. Output passes the scorer's schema check. It is bounded per item (timeout) and fails closed on any server error. It verifies the D-M1-0 manifests before running and records them, the chat template and the tokenizer revision in its output. |
| M1-R1 | Run generation for 4 bases × 2 sets, one base loaded at a time | Director runs the reviewed harness | output hashes | `C:\llm\runs\m1\` | none | per base ≤ 2 h | Every item produces a candidate or a recorded failure |
| M1-S1 | Score with the scorer (after G1) | Director runs it | two non-author graders (L1 checklist) | `C:\llm\reports\m1-*` | ≤ USD 12 (grading) | per run ≤ 1 h | Field-level acceptance per base and set. Every result records the D-M1-0 reference manifest, the candidate manifest and the grade-file digests, and is reproducible from them. |
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

## Status at the M1 gate (2026-09-28)

Every number is `[verified]` by the project Director from files under `C:\llm\reports\m1-s1\`, `C:\llm\runs\m1\`, `C:\llm\reports\m1-license\` and the provider-dispatch receipts, as recorded in PR #294 comments 5859345713, 5859906898 and 5860810226; the gate record in `project-state-report.md` carries the same figures.

| Packet | State | Evidence |
|---|---|---|
| M1-D1, M1-D2 | done, accepted (pr-45 disclosed) | receipts above; D-M1-0 manifests set A `14fa9529…e7f5`, set B `38a93d79…69bc` |
| M1-W1 | done | four download receipts, hash-verified |
| M1-C1 | done (#296, fix #314) | GGUFs hashed: Qwen3-8B Q4 `8cbfdd55…`, Q8 `3eab3a23…`, Qwen3-14B Q4 `af16f12d…`, phi-4 Q4 `000c3c35…`, Nemo Q4 `166d2415…` |
| M1-I1 | done (#298) | 14 set A inputs (N=14 after five leak exclusions, `set-a-exclusions-primary.json` `cf7fbeb4…`), 50 set B inputs; input manifests `b0c6b8b8…`, `f083246c…` |
| M1-G1, M1-R1 | done (#295; harness at main `4a4a35a`, O-044) | 256/256 ranked and sensitivity items OK, 0 failed (four configurations × 14 set A + 50 set B), plus the Nemo control 64/64: 320 scored items `[verified from the run.json counts cited in PR #294 comments 5859232066 and 5860752427]`; the executing session's "448 of 448" figure covers every r1 run including superseded diagnostics and is `[relayed]` |
| M1-S1 | done | eight `summary.json` with `grades_sha256`; 2,048 grade rows, 0 missing, 0 malformed; rubric sha `b2125559…` |
| M1-L1 | done | LICENSE at pinned revisions: Qwen3-8B and Qwen3-14B Apache-2.0 (sha `832dd9e0…`), phi-4 MIT (sha `c4941961…`); `C:\llm\reports\m1-license\FETCH-RECORD.txt`, 2026-09-27 19:59 UTC |

**Result under the frozen rule O-045** (fixed denominators 14 and 50; four scored fields; graders Grok `grok-4.6` and the Z.ai stand-in after the Cursor lane failed twice):

| Candidate | Accepted as-is A | Accepted as-is B | Primary | Tie-break 1 (joint field acceptance) | Failed packets | Mean similarity |
|---|---|---|---|---|---|---|
| **qwen3-14b Q4_K_M (winner)** | 0/14 | 0/50 | 0.0000 | 0.0089 (one field accepted by both graders) | 0 | 0.1489 |
| phi-4 Q4_K_M | 0/14 | 0/50 | 0.0000 | 0.0000 | 0 | 0.2170 |
| qwen3-8b Q4_K_M | 0/14 | 0/50 | 0.0000 | 0.0000 | 0 | 0.1813 |
| qwen3-8b Q8_0 (reported, not ranked) | 0/14 | 0/50 | 0.0000 | 0.0000 | 0 | 0.1869 |
| Mistral-Nemo-Base control (D-M1-4, never ranked) | not graded | not graded | n/a | n/a | 0 | 0.1838 (95–97 % verbatim example copies) |

**Winner:** Qwen3-14B, Apache-2.0, pinned revision `40c069824f4251a91eefaf281ebe4c544efd3e18`, on tie-break 1. Every candidate has zero packets accepted as-is and identical Wilson intervals (set A [0.000, 0.215], set B [0.000, 0.071]); this is the frozen rule's ordering, not an established quality advantage. The Nemo control shows that similarity near 0.18 to 0.22 is what schema-shaped boilerplate earns, so **similarity is a diagnostic only and every M2 gate is grader-based** (D-M1-4 outcome). **D-M1-F:** M2 attempts Qwen3-14B first behind a local feasibility probe (p95 row length, peak reserved VRAM under 11.0 GiB, three-epoch estimate under 10 h with 20 % contingency); Qwen3-8B is the fallback and phi-4 a named conditional alternate.

**Paid spend charged to M1 (receipt-audited lower bound):** `project-state-report.md` (MX-049) reconciles **USD 1.508940 over 15 receipts** by 2026-09-26 22:56 UTC (pre-gate reviews, set A and set B reviews including the authenticated batch-1 re-run, #294 and #295 reviews, the quantization brief). Add Grok grading USD 0.4726 (2026-09-27): **at least USD 1.9815**. Z.ai grading used about 664 subscription plan credits, not USD. The reviews of #296, #298, #307 and #314 carry their own receipts and are not summed here. The authoritative total is the host meter run with the M1 window that #341 adds (`--purpose-prefix 'homemade-llm M1'`); the gate record adopts that figure at the next update `[unverified here]`. The envelope was USD 25 and no single receipt approaches it. This total is the one gate figure not yet verified: the meter run with the M1 window is scheduled before the first paid M2 call and its result replaces this bound in D-M1-G, here and in the state report.

**Not done at the gate, not blocking it:** #339 (scorer `candidate_manifest` excludes `run.json`) is a draft awaiting review; after it merges the two Nemo control summaries are regenerated with the old outputs kept as evidence. The nine frozen ranked summaries are not touched. The spend meter still defines M1 as open-ended and has no M2 window; draft PR #341 (Codex maintenance) closes M1 at the gate time and opens M2 at 2026-09-28 01:34:11 UTC (D-M2-8), and no paid M2 call is made until it merges.

**Owner disposition of the non-winning bases (O-053, 2026-09-28):** the owner directed removal of the two models that did not win. D-M1-F names Qwen3-8B as the fallback if the 14B feasibility probe fails and phi-4 as a conditional alternate, so the record proposes: archive both weight sets and GGUFs to `E:\llm-archive\m1-bases-2026-09\` with hash verification first (the M0.6 pattern), remove them from `C:\llm` after the Qwen3-14B probe passes, and log every removal with its hash. Execution is a host work order for the executing session under a Go, not part of this record.

Contribution tags record the author of the plan or artifact. They do not prove the provider identity or satisfy any gate above.
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:38 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:40 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:02 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 02:30 UTC | END]] -->
