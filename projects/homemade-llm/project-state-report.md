<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:25 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:15 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:07 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:07 UTC | BEGIN]] -->
# Project state report — homemade-llm, M0 "Foundation" gate (closed)

Evidence-first. `[verified]` = read or measured by the Director session that wrote this line on the date given; `[relayed]` = a provider's or a person's claim; `[owner-reported]` = the owner's own account; `[stale]` = true at the stated time. No draft, simulation, or predicted-position label applies: every provider result below has a receipt.

## EVIDENCE

**Homemade LLM** has closed its M0 gate and entered M1. Ed approved closing M0 and entering M1 once this record was reconciled ([#277 comment 5842683418](https://github.com/edwardjones3976/director-orchestrator/pull/277#issuecomment-5842683418), 2026-09-26 03:15 UTC) `[relayed]`. This report and decision-log rows O-031 to O-034 and D-015 to D-018 are that reconciliation. Canonical record: this file, `decision-log.md`, `project-profile.yaml`, `PLAN.md` §3 row "M0", `workstream-plan.md`, and PRs #266, #267, #268, #269, #271, #272, #273, #275, #276, #277 and #279 (all merged).

- **Project ID:** `homemade-llm`
- **Assignment IDs:** HS-0, M0.1 to M0.12, RV-275
- **Report label:** delivered state (authenticated receipts and merged PRs; no prediction)
- **Categorized status:** M0 closed; M1 in progress. This reconciliation PR still needs one non-author exact-head review and current CI.

### What M0 set out to prove, and what the evidence says

| ID | Deliverable | Status | Evidence |
|---|---|---|---|
| HS-0 | PR-0 merged | done | #266 merged 2026-09-24 08:03 UTC; follow-up #268 merged 17:49 UTC `[verified]` |
| M0.1 | `scripts/preflight.ps1` | done | #267 merged; report `C:\llm\reports\preflight-20260924-154657-543-6c99d2.json`: all drive, layout, GPU, Docker, LM Studio checks OK; Tailscale WARN "not installed" at 15:46 UTC on 09-24, since installed `[verified]` |
| M0.2 | `-Init` run; `C:\llm` layout | done | `C:\llm\{hf,datasets,runs,models,seeds,secrets,reports,state,tools}` present; `G:\llm-mirror`, `E:\llm-archive` present `[verified 09-25]` |
| HS-1 | `unsloth/unsloth` pulled | done | image present, 33.1 GB, `unsloth/unsloth:latest` digest `sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739` (image built 2026-09-20) `[verified 09-25]` |
| M0.3 | smoke fine-tune | done | #271 merged; 10 runs on 09-24; latest then `smoke-20260924-212438-a6dddc`: base `unsloth/qwen3-0.6b-unsloth-bnb-4bit` @ `e86f5289`, 20 steps, 50 rows, peak reserved 0.79 GB, train 28.0 s, ~98 tok/s, last loss 1.085, JSON sample valid, GGUF Q4_K_M 396,705,312 bytes, export 190 s, wall 286 s; torch 2.11.0+cu128 on the RTX 5070 `[verified from report JSON]`. Later live runs on 09-26 (for #283 and #291) produced the same GGUF size `[verified]` |
| M0.4 | serve smoke | done | `serve-smoke.ps1` in #271; LM Studio `/v1/models` lists the imported smoke GGUFs on 127.0.0.1:1234 `[verified 09-25 22:58 UTC]`; from inside the running Open WebUI container, `http://host.docker.internal:1234/v1/models` answered HTTP 200 `[verified 09-25 23:23 UTC]`; `serve-smoke: PASS` again on 09-26 (live runs for #283 and #291) `[verified]` |
| M0.3/M0.4 hardening | #275, #279 | merged | #275: xAI grok-4.6 ACCEPT at `ed317a81` (receipt `receipts/xai/20260925T230929Z.json`), CI run 36120719806 green at that head, merged 2026-09-25 23:31 UTC as `f0a7752e`. #279 (fix-forward for Grok's post-merge review of #275) merged 2026-09-26 02:38 UTC as `e3024db` `[verified]`. Later hardening is listed under "After the gate" |
| M0.5 | Open WebUI + SearXNG compose, phone access | done; one runbook step open | #272 and #273 merged. `tailscale serve status` showed `https://blackbox.taile9e808.ts.net` as tailnet-only, proxying `/` to `http://127.0.0.1:3000`, and HTTPS `GET /api/config` returned 200 with `enable_signup=false` (2026-09-26 03:10 UTC) `[relayed, Codex read-only check]`. Ed chatted with web search from his phone at that URL (03:11 UTC) `[owner-reported]`. **Open:** `compose/RUNBOOK.md` §2 step 3 sets `WEBUI_URL=https://blackbox.taile9e808.ts.net` and recreates the container. At 2026-09-26 18:11 UTC both `C:\llm\secrets\open-webui.env` and the running `homemade-llm-open-webui-1` container still had `WEBUI_URL=http://127.0.0.1:3000` `[relayed, read-only check]`. That change is an owner config-change gate, not done here (decision D-016) |
| M0.6 | Ollama retirement | done | robocopy 59 files, 93.415 GB, 0 failed, to `E:\llm-archive\ollama-2026-09\models\` (log `m06-archive-20260924-154719.log`); content verified on 09-25: all 48 blobs hash to their digest filenames, 0 mismatched, 608 s (`m06-hash-verify-20260925-234625.txt`); `xo-fleet\ollama\models\blobs` now 16 KB `[verified]` |
| M0.7 | mirror + Scheduled Task + restore drill | done | `scripts/mirror.ps1` in #269; task `homemade-llm-mirror` Ready; run 2026-09-25 03:30 local mirrored 413 files, 8.119 GB, 0 failed, `secrets` excluded; `restore-drill.sha256` = `474CA9FA…335A` `[verified]` |
| M0.8 | stray `xo-fleet\ollama\id_ed25519` | done | file absent `[verified 09-25]` |
| M0.9 | dataset secret scan | done | `scripts/scan-dataset.ps1` and `scripts/test-scan-dataset.ps1` are on `main` via #276, merged 2026-09-26 03:07 UTC as `58fb485` (reviewed head `b0a4f59`). #276 had two material authors, and the owner granted a one-time mixed-authorship exception and merged it (decision D-015) `[verified; exception relayed]`. gitleaks 8.30.1 at `C:\llm\tools\gitleaks\gitleaks.exe` `[verified]` |
| M0.10 | spend meter | done | #269 merged. The provider-dispatch receipts are the ledger, and `spend_meter.py` totals them. The receipt-audited M0 spend is under "M0 paid spend" below `[verified]` |
| M0.11 | base-model brief | done | `research/base-models-20260925.md` is on `main` via #276. The Perplexity sonar-pro brief returned mostly "not found" (USD 0.03984, receipt `receipts/perplexity/20260925T231241Z.json`), so the Director-research brief is the one used `[verified]` |
| M0.12 | this report; profile `framing` → `building` | done | #277 merged 2026-09-26 00:09 UTC as `79e538f`; this reconciliation PR records the gate `[verified]` |

### M0 paid spend (receipt-audited lower bound)

Read-only audit of the provider-dispatch receipts at 2026-09-26 18:16 UTC. xAI cost is `usage.cost_in_usd_ticks` ÷ 10¹⁰; Perplexity cost is `response.usage.cost.total_cost`; Grok CLI cost is taken from its own receipt. The figure is a **lower bound, not a complete ledger** `[verified]`:

| Group | Receipts | USD |
|---|---|---|
| M0 before the gate: PR #266 review (Grok CLI `homemade-llm/grok-20260924T080222Z.out.json`), M0.11 brief (Perplexity) and its xAI fact-check, #275 review and post-merge review, the eight #277 reviews, and three #279 reviews | 16 costed | **1.43271956** |
| Shared M0/M1: one xAI base-impact check covering #279 and #280, charged conservatively to M0 | 1 | 0.020078 |
| xAI and Perplexity receipts with no project tag (09-24 and 09-25), not assigned to M0 | 12 costed, 1 uncosted | 0.221056 |
| Uncosted xAI attempts (HTTP 0, no recorded cost; not USD 0): `20260925T090508Z` (a #275 review attempt), `20260926T172551Z` (a #291 review attempt that timed out), `20260926T173050Z` (no purpose recorded) | 3 | unknown |

- The first 13 M0 receipts reproduce the independent audit's USD 1.22778956 exactly ([#277 comment 5842706191](https://github.com/edwardjones3976/director-orchestrator/pull/277#issuecomment-5842706191)). The three #279 reviews add USD 0.20493.
- This supersedes the USD 0.55 stamped at 2026-09-26 00:01 UTC, which missed the PR #266 review, the post-merge #275 review and the #279 reviews.
- The M0 lower bound is well inside the USD 25 envelope. Adding the post-gate #283 and #291 hardening, which is M0 work (USD 0.882086, below), and the shared #279/#280 check (USD 0.020078), charged conservatively to M0, gives an **M0 charged total of USD 2.33488356**. That is still a lower bound (D-017).
- Codex, Cursor and Claude subscription usage carries no per-call USD cost and is not in this ledger.

### After the gate (M1 spend, and post-gate M0 hardening charged to M0)

- **M1 before the gate (a hard-stop crossing, D-018):** #280 (field-level scorer) merged 2026-09-26 02:38 UTC as `c3cc8fa`, before O-031 closed M0. It awaits Ed's explicit disposition. Its reviews and the first #281 review cost USD 0.280142 over 5 receipts `[verified]`.
- **M0 hardening after the gate, charged to the M0 envelope:**
  - #283 (fix-forward for connector findings on #279) merged 2026-09-26 16:40 UTC as `052553f`, at head `d6a6d55`.
  - #291 carries the job-object supervision fix that landed on #283's branch after its merge. It is open.
  - Their xAI reviews cost USD 0.882086 over 8 receipts, including the 18:14 UTC review of #291 `[verified]`.
- **Open related PRs:**
  - #281: M1 scorer fail-closed; draft, with its source author.
  - #284: the repository-wide sole author rule.
  - #289: focused CI reruns.
  - #290: a Windows float test fix.
  - These are not M0 gate items.

### Numbers that govern M1

- **C: free:** 371.13 GiB (floor 150 GB) `[relayed, read-only check 2026-09-26 18:11 UTC]`.
- **GPU headroom for the shootout:** the 0.6B smoke peaked at 0.79 GB reserved. 8B and 14B QLoRA numbers are not yet measured on this card; Unsloth's published floors are 6 GB (8B) and 8.5 GB (14B) `[relayed, research brief]`.
- **Turnaround:** one 20-step smoke run including GGUF export is under 5 minutes wall clock, and GGUF export dominates (150–190 s) `[verified from the run reports]`.
- **Held-out sets (local only, never committed):**
  - Set A: 22 real-history packets at `C:\llm\datasets\heldout\set-a`, unchanged.
    - The owner chose the 19 concise items for M1 (`set-a\M1-SELECTION.md`), excluding EW-20260912-0014, 0015 and 0016 for size `[relayed, owner decision 2026-09-26 18:45 UTC]`.
    - Their JSON references are in `set-a-json-v2\`: authored by OpenAI Codex, with a Grok ACCEPT on all 19 (receipt `xai/20260926T193234Z.json`) `[verified 2026-09-26 19:32 UTC, receipt]`.
    - An earlier Cursor conversion (`set-a-json\`) failed three independent reviews and is kept only as evidence `[verified, receipts heldout/codex-20260926T190120Z, …190641Z, …191448Z]`.
  - Set B: the 50 Cursor-reconstructed packets in `pr-reconstructed\` were **rejected** by an independent Codex review (50 substantive findings) and are kept only as evidence `[verified 2026-09-26 19:36 UTC, receipt heldout/codex-20260926T193640Z.setb-review]`.
    - A fresh Codex-authored replacement (`pr-reconstructed-v2\`) is in progress.
    - It is not usable until independently accepted, and its xAI review needs a specific owner approval.
  - The set A file counts and the 22 unchanged originals are `[verified 2026-09-26 18:16 UTC, file counts]`; each later fact carries its own grade above.
- **Concurrency:** several Director sessions work this project. Deconfliction is in the decision log (D-011) `[verified]`. This record's authorship was reassigned by the owner (O-033) `[relayed]`.

### Charter milestone record (`docs/team-operating-charter.md`, "Evidence-first milestone record")

| # | Field | Where, or why not applicable |
|---|---|---|
| 1 | Project and assignment IDs | Top of this report |
| 2 | Objective, scope, categorized status | Top of this report and the M0 table |
| 3 | Starting and current refs, SHAs, digests | Start: PR-0 base `1892b841` (D-008). Gate: `main` at `58fb485` when O-031 was given; current `main` `052553f`. Digests: `unsloth/unsloth` image `sha256:1644d635…` (D-012), restore drill `474CA9FA…335A` (M0.7). No committed dataset artifacts. |
| 4 | Provider participation, unavailable providers, drafts or simulations | Every paid call has a receipt (M0 paid spend table). Unavailable during M0: Grok Build CLI (HTTP 402, D-009) and GitHub Copilot (out of quota, D-005). No draft, simulation or predicted-position content. |
| 5 | Target and actual provider ledgers (assignment and rolling 20) | **Unavailable.** The engineering-workload ledger stops at 2026-09-12, so the rolling-20 mix is UNKNOWN. |
| 6 | Moderator, Participant and Director actions, OpenAI vote | Director actions are recorded in the decision log (O-, D- rows). **Not applicable:** no committee vote was held in M0, and no OpenAI vote was cast. |
| 7 | Dissent, counter-bias, alternatives | **Not applicable:** no committee debate was held. Reviewer disagreements and their resolutions are in the PR threads. |
| 8 | Tests, CI, security scan, independent review | Per PR in the M0 table (for example #275: CI run 36120719806 and xAI ACCEPT receipt). **Security scan not applicable:** no Codex Security scan is configured for this project. |
| 9 | Risks, limitations, blockers, confidence | "Not done, and what remains unverified" below |
| 10 | Cost, tokens, elapsed time | Cost: M0 paid spend (a lower bound). Tokens are per receipt and not aggregated. Elapsed: 2026-09-24 06:05 (O-001) to 2026-09-26 03:15 (O-031), about 45 hours. |
| 11 | Pre-stage packet, workload size, writer lease, reviewer reservation | **Unavailable:** M0 ran analysis-only (`rollout-packet.md`) without pre-stage packets or writer leases. Reviewers are named per PR. |
| 12 | Status-check, duplicate-validation, action and wave counts | **Unavailable:** not measured in M0. |
| 13 | Avoidable-commit baseline | **Unavailable:** not measured in M0. |
| 14 | Scope or authority changes, owner authorization | No authority change. Owner authorizations: O-029 to O-034. |
| 15 | Recovery state and rollback | Nightly mirror and restore drill (M0.7). Rollback is a revert of the merged PRs. Receipts and reports are on disk under `C:\llm\reports` and the provider-dispatch receipts. |
| 16 | Next gate and responsible party | "Next bounded action" below |
| 17 | Owner decisions and their scope | `decision-log.md` O-001 to O-034 |
| 18 | Not done and unverified | "Not done, and what remains unverified" below |

### Not done, and what remains unverified

1. **`WEBUI_URL` runbook step** (M0.5): an owner config-change gate, recorded as open (D-016). The working phone chat is separate evidence and does not depend on it.
2. **Rolling-20 allocation:** the engineering-workload ledger (`~/.director-orchestrator/engineering-workload/assignments.sqlite3`) has 16 rows and stops at 2026-09-12, so the rolling-20 provider mix for this drive is UNKNOWN `[verified]`.
3. **Spend baseline:** the 4-week paid-spend baseline for the L1 checklist starts from the first metered DIRECTOR-style production call. Every call so far is a review or research call.

### Owner gates

- Pre-gate M1 work (D-018): ratify #280, which merged before M0 closed, or have it reverted and redone under M1.
- M1 plan: approve the M1 workstream plan and its pre-stage packets once written. No new M1 provider dispatch happens before that.
- `WEBUI_URL`: set `WEBUI_URL=https://blackbox.taile9e808.ts.net` in `C:\llm\secrets\open-webui.env` and recreate the container (`compose/RUNBOOK.md` §2 step 3), or decide to keep `http://127.0.0.1:3000`.
- Held-out set B: approve the independent review of `pr-reconstructed-v2` once it is ready. That review sends the private PR bodies to xAI.
- Merges: this reconciliation PR once reviewed and green; #281 has its own ready and merge decision (O-031).

### Next bounded action

M1, base shootout on the held-out sets:
0. Ed disposes of D-018 (#280, merged before M0 closed): ratify, or revert and redo. Work that depends on the scorer (#281's merge, candidate generation, scoring) waits for that decision.
0a. The Director writes the M1 workstream plan with immutable pre-stage packets: per-packet budget, timeout, retry limit, owned paths, tests, reviewer reservation and stop conditions. No new M1 provider dispatch happens until it exists **and Ed approves it**. Work already in flight under its own recorded bounds continues: the owner-approved downloads (O-034) and the set A and set B data preparation the owner delegated to Codex.
1. Set B's replacement passes an independent review (set A's references are already accepted).
2. The scorer's fail-closed fix (#281) lands through its own review.
3. The four pinned base weight sets are downloaded one at a time under the owner's approval (O-034) and its storage guards. Qwen3-8B, Qwen3-14B, Phi-4 14B and Mistral Nemo Base 12B are then scored. Nemo Base is the control and excluded from winner selection (O-034); swapping in Nemo Instruct needs a new owner approval. with schema-constrained decoding. The winner is picked by field-level acceptance plus license, and all four bases are ungated per the download audit (`C:\llmeports\m1-base-download-audit-20260926-1850UTC.md`) `[relayed]`.

- **Responsible:** Ed for step 0 (the D-018 disposition) and for approving the M1 plan and the set B review route; then the Director, with providers authoring data, code and runs per O-003.
- **Expected evidence:**
  - per-base scorer output on both held-out sets;
  - a persisted license check of the winner at its pinned revision;
  - Ed's recorded sign-off on the M1 gate;
  - a decision-log row naming the winner.
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:07 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:16 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:07 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:15 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:25 UTC | END]] -->
