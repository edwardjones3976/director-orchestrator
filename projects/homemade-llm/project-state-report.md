<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:36 UTC | BEGIN]] -->
# Project state report — homemade-llm, M0 gate (M0.12)

## EVIDENCE

**Homemade LLM** has finished its M0 foundation and is ready to move from `framing` to `building`. Merging this report is that transition, and it is an owner gate.

The canonical records are:
- [`PLAN.md`](PLAN.md) v1.3
- [`workstream-plan.md`](workstream-plan.md)
- [`decision-log.md`](decision-log.md) O-001…O-036

Report facts:
- **Project ID:** `homemade-llm`
- **Assignments covered:** M0.1–M0.12 and HS-1.
- **Report label:** delivered report. Every number below was measured on 2026-09-25 on `blackbox` against main `f0a7752e`, by Claude and independently by OpenAI Codex (see Evidence).
- **Categorized status:** 10 of 12 items are closed. M0.5's phone check waits on the owner (`tailscale serve`), and M0.9/M0.11 wait on the merge of #276, which is already accepted.

## Checklist

| Item | Result | Evidence |
|---|---|---|
| M0.1 preflight probe | done | #267 (`eee96e16`) |
| M0.2 layout and ACL | done | `C:\llm` layout exists. `C:\llm\secrets` allows only SYSTEM and BlackBox\edwar, with inheritance off |
| HS-1 Unsloth image | done | `unsloth/unsloth@sha256:1644d635…2739` present (33.1 GB); O-029 |
| M0.3 smoke fine-tune | done | #271 (`7c44bf30`) and #275 (`f0a7752e`). 14 smoke reports. Latest run `smoke-20260924-212438-a6dddc` (below) |
| M0.4 serve smoke | done | LM Studio on 127.0.0.1:1234, owned by the LM Studio process. The schema-valid reply passed from Windows and from a container |
| M0.5 chat UI | **owner step left** | #272, #273. Open WebUI 0.11.4 has been healthy for 6 h on 127.0.0.1:3000, with signup disabled. SearXNG runs on 127.0.0.1:8088. Tailscale is up (`blackbox`, 100.75.12.89) but has **no serve config**, so the phone check is not done |
| M0.6 Ollama retirement | done | 0 blobs remain on C:. 48 blobs (93.42 GiB, 100.3 GB) are on E:, hash-verified before deletion; Codex re-hashed one random archive blob and it matched. C: has 355.24 GiB free |
| M0.7 mirror | done | #269. The Scheduled Task last ran 2026-09-25 03:30 with result 0; next run 09-26 03:30. `G:\llm-mirror` holds 8.12 GiB, 1.6 MB less than `C:\llm`, which is the held-out data and reports written after the 03:30 run. The spot hash check matches |
| M0.8 stray key | done | `xo-fleet\ollama\id_ed25519` is absent (moved to `.ssh`) |
| M0.9 dataset scan | **merge pending** | #276, reviewer ACCEPT at `af3626a`. The latest scan of `C:\llm\datasets` has 0 findings |
| M0.10 spend meter | done, with a deviation | #269. It reads the dispatch receipts rather than appending to `spend.jsonl` (O-033). Baseline below |
| M0.11 base-model brief | **merge pending** | #276, `research/base-models-20260925.md`. All four bases are ungated (O-032) |
| M0.12 this report | review | this PR |

**Latest smoke run.** `smoke-20260924-212438-a6dddc` loaded base `e86f5289` and merged onto `db4e90b8`. It peaked at 0.73 GB of torch-allocated VRAM (0.79 GB reserved) and ran at about 97.7 tokens/s. Training took 28 s, the GGUF export 190 s, and the whole run 286 s. The sample output was valid JSON.

**Hot tier.** `C:\llm` holds 8.12 GiB (8.72 GB) of the 120 GB cap, excluding `secrets`. The largest folders are `models` (4.80 GiB), `hf` (1.67 GiB), `state` (0.94 GiB) and `runs` (0.68 GiB). The 13 smoke GGUFs in `models\smoke` can be pruned whenever convenient.

**Spend baseline (7 days to 2026-09-25, all projects, from receipts).** The total is **USD 11.19**:

| Route | Calls | USD | Uncosted calls |
|---|---|---|---|
| Grok CLI | 26 | 7.76 | 0 |
| xAI API | 51 | 3.07 | 13 |
| Perplexity | 18 | 0.36 | 6 |

Directly attributable to homemade-llm: USD 0.33 (Grok CLI). M0's own paid research on 2026-09-25 cost USD 0.065 (Perplexity 0.040 and xAI 0.025), and the xAI and Perplexity receipts carry no project tag. Codex, Cursor, Antigravity and Copilot run on subscriptions and record no per-call cost. The L1 checklist needs 4 weeks of this baseline, and the first week ends on 2026-10-02.

## Current shape

- **Purpose:** two local models on one RTX 5070: DIRECTOR (L1, target 2026-11-12) and COMPANION (L2, target 2026-12-17).
- **Team routing on 2026-09-25:**
  - **Claude:** director; author of the brief and this report.
  - **Cursor:** author of M0.9 and of held-out set B.
  - **Gemini via Antigravity:** reviewer of record for #275 and #276.
  - **Perplexity and xAI:** research and fact-checking.
  - **Firecrawl:** primary-source retrieval.
  - **OpenAI Codex:** independent evidence collection and reviewer of this report.
  - **Unavailable:** Codex during its outage, and GitHub Copilot (monthly quota).
- **Provider roles contract:** [`provider-roles-contract.yaml`](provider-roles-contract.yaml).

## What changed since PR-0

- Every M0 script is merged, with unit tests. The live train, serve, interrupt and lock checks passed on the final code.
- The GGUF merge source is **bound**, not merely checked: the export runs offline against the pinned local snapshot (O-030).
- M1's held-out sets are drafted, local only and never committed, and gitleaks finds nothing in them:
  - **Set A:** 22 real-history packets, awaiting owner confirmation (O-034).
  - **Set B:** 50 packets reconstructed from merged PRs. Cursor revised them to remove hindsight, and each was checked against the schema.

## Boundaries

- **Scope changes:** none.
- **Authority changes:** none. No `src/` changes. M3 still needs its OWNER_DECISION record.
- **External actions:** none beyond merges the owner performed. `tailscale serve` is left to the owner because it changes network exposure.

## Evidence

- **Independent verification:** OpenAI Codex (GPT-6) measured the same facts read-only on 2026-09-25, without seeing Claude's values.
  - **Both measured and agree:** C: free space (355.0 vs 355.2 GiB, a few minutes apart); 0 C: blobs and 48 E: blobs (100,303,837,184 bytes), with one random blob re-hashed; the stray key absent; the `C:\llm` and mirror sizes; 13 GGUFs and 14 smoke reports; every metric of the latest smoke run; 0 scan findings; Open WebUI signup disabled; the held-out counts 22 and 50.
  - **Corrected after the cross-check:** Claude had labelled binary GiB as "GB"; this report now states both units.
  - **Measured by Claude only**, because Codex's sandbox refused Docker, the network-listener API, the Task Scheduler, the Tailscale pipe and GitHub: the image and container state, the 1234/3000/8088 bindings, the mirror task's run history, the Tailscale state, PR merge states and the spend totals.
- **Independent review:** pending. OpenAI Codex will review this report at its exact head.
- **Trusted CI:** #275 was 6/6 green at `ed317a81` before merge. #276 CI is running at `af3626a`.
- **Security:** the latest gitleaks dataset scan has 0 findings. `HF_TOKEN` is never passed to the export container, and a stored token in the mounted cache blocks training (O-031).
- **Risks:**
  - L1 needs 4 metered weeks, so the date slips if paid calls stop flowing through receipts.
  - Set A is below its ~30-item target.
  - M3 waits on #263 and #264, which are still open.
  - The repository's local git config sets `user.name = cursor[bot]`, which mislabels the git author of recent commits. The contribution tags remain the record.
- **Explicitly not done:** `tailscale serve`, the merge of #276, owner confirmation of set A, and any M1 training or evaluation.

## Current handoff

- **State:** owner decision required.
- **Next responsible party:** the owner.
- **Next actions:**
  1. Merge #276 when CI is green.
  2. Enable HTTPS certificates in the Tailscale admin console, then run `tailscale serve --bg 3000` and open the printed URL from the phone.
  3. Confirm set A: its 22 items, whether to top it up to ~30, and whether to keep the two oversized packets.
  4. Merge this report, which moves the profile to `building`.
- **Next gate:** M1 base shootout, by 2026-10-08: Qwen3-8B, Qwen3-14B, Phi-4 and Mistral Nemo on both held-out sets, with schema-constrained decoding.

Nothing outside `projects/homemade-llm` was written by this report.
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:36 UTC | END]] -->
