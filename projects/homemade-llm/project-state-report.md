<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:35 UTC | BEGIN]] -->
# Project state report — homemade-llm, M0 "Foundation" gate

Evidence-first. `[verified]` = read or measured by the Director session that wrote this line on the date given; `[relayed]` = a provider's or a person's claim; `[stale]` = true at the stated time. No draft, simulation, or predicted-position label applies: every provider result below has a receipt.

## EVIDENCE

**Homemade LLM** is at the M0 gate. Canonical record: this file, `PLAN.md` §3 row "M0", `workstream-plan.md`, and PRs #266, #267, #268, #269, #271, #272, #273 (merged) #275 (review complete, awaiting the owner's merge), #276 (M0.9 and M0.11, awaiting a non-author review) and #277 (this report).

- **Project ID:** `homemade-llm`
- **Assignment IDs:** HS-0, M0.1 to M0.12, RV-275
- **Report label:** delivered state (authenticated receipts and merged PRs; no prediction)
- **Categorized status:** review-ready for the M0 gate; blocked only on owner clicks listed under "Owner gates"

### What M0 set out to prove, and what the evidence says

| ID | Deliverable | Status | Evidence |
|---|---|---|---|
| HS-0 | PR-0 merged | done | #266 merged 2026-09-24 08:03 UTC; follow-up #268 merged 17:49 UTC `[verified]` |
| M0.1 | `scripts/preflight.ps1` | done | #267 merged; report `C:\llm\reports\preflight-20260924-154657-543-6c99d2.json`: all drive, layout, GPU, Docker, LM Studio checks OK; Tailscale WARN "not installed" at 15:46 UTC on 09-24, since installed `[verified]` |
| M0.2 | `-Init` run; `C:\llm` layout | done | `C:\llm\{hf,datasets,runs,models,seeds,secrets,reports,state,tools}` present; `G:\llm-mirror`, `E:\llm-archive` present `[verified 09-25]` |
| HS-1 | `unsloth/unsloth` pulled | done | image present, 33.1 GB, `unsloth/unsloth:latest` digest `sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739` (image built 2026-09-20) `[verified 09-25]` |
| M0.3 | smoke fine-tune | done | #271 merged; 10 runs on 09-24; latest `smoke-20260924-212438-a6dddc`: base `unsloth/qwen3-0.6b-unsloth-bnb-4bit` @ `e86f5289`, 20 steps, 50 rows, peak reserved 0.79 GB, train 28.0 s, ~98 tok/s, last loss 1.085, JSON sample valid, GGUF Q4_K_M 396,705,312 bytes, export 190 s, wall 286 s; torch 2.11.0+cu128 on the RTX 5070 `[verified from report JSON]` |
| M0.4 | serve smoke | done | `serve-smoke.ps1` in #271; LM Studio `/v1/models` lists 10 imported smoke GGUFs on 127.0.0.1:1234 `[verified 09-25 22:58 UTC]`; from inside the running Open WebUI container, `http://host.docker.internal:1234/v1/models` answered HTTP 200 `[verified 09-25 23:23 UTC]` |
| M0.3/M0.4 hardening | #275 | review complete | xAI grok-4.6 ACCEPT at `ed317a81`, receipt `receipts/xai/20260925T230929Z.json`, USD 0.1639; Director re-ran unittest 14/14 and both PowerShell suites (9 + 14 ok) at that head; CI run 36120719806 green `[verified]`. Merge is the owner's. |
| M0.5 | Open WebUI + SearXNG compose | done except phone access | #272, #273 merged; `homemade-llm-open-webui-1` healthy on 127.0.0.1:3000 and `homemade-llm-searxng-1` on 127.0.0.1:8088, up since 09-25 ~18:00 UTC; `ENABLE_SIGNUP=false`; env file `C:\llm\secrets\open-webui.env` `[verified]`. `tailscale serve` not yet published: the Director session's attempt was denied by the auto-mode classifier (Expose Local Services) and is the owner's click; Tailscale is online as `blackbox.taile9e808.ts.net` but reports no cert domains, so HTTPS certificates may still need enabling in the admin console `[verified]` |
| M0.6 | Ollama retirement | done | robocopy 59 files, 93.415 GB, 0 failed, to `E:\llm-archive\ollama-2026-09\models\` in 10:20 at 162 MB/s (log `m06-archive-20260924-154719.log`); `xo-fleet\ollama\models\blobs` now 16 KB `[verified]` |
| M0.7 | mirror + Scheduled Task + restore drill | done | `scripts/mirror.ps1` in #269; task `homemade-llm-mirror` Ready; run 2026-09-25 03:30 local mirrored 413 files, 8.119 GB, 0 failed, `secrets` excluded; `restore-drill.sha256` = `474CA9FA…335A` `[verified]` |
| M0.8 | stray `xo-fleet\ollama\id_ed25519` | done | file absent `[verified 09-25]` |
| M0.9 | dataset secret scan | in PR #276 | gitleaks 8.30.1 at `C:\llm\tools\gitleaks\gitleaks.exe` `[verified]`; `scan-dataset.ps1` + `test-scan-dataset.ps1` authored via Cursor for the peer Director session; PR #276 opened 23:22 UTC at head `af3626a3`, awaiting a non-author review `[verified]` |
| M0.10 | spend meter | done | #269 merged; receipts are the ledger; `spend_meter.py --days 7`: all projects USD 11.19 (grok-cli 7.76, xai 3.07, perplexity 0.36) `[verified]`. **homemade-llm M0 paid spend: USD 0.20** (xAI review 0.1639; Perplexity brief 0.0398; xAI fact-check receipt carries no cost field) of the USD 25 envelope |
| M0.11 | base-model brief | in PR #276 | Perplexity sonar-pro at 23:12 UTC returned mostly "not found" (USD 0.0398, receipt `receipts/perplexity/20260925T231241Z.json`); the Director-research fallback `research/base-models-20260925.md` is in PR #276 `[verified]` |
| M0.12 | this report; profile `framing` → `building` | this PR | — |

### Numbers that govern M1

- **C: free:** 356 GB (floor 150 GB) `[verified 09-25 22:58 UTC]`; `C:\llm` = 8.2 GB of the 120 GB cap (models 4.9 GB, hf 1.7 GB, runs 0.7 GB).
- **GPU headroom for the shootout:** the 0.6B smoke peaked at 0.79 GB reserved; 8B and 14B QLoRA numbers are not yet measured on this card. Unsloth's published floors are 6 GB (8B) and 8.5 GB (14B) `[relayed, research brief]`.
- **Turnaround:** one 20-step smoke run including GGUF export is under 5 minutes wall clock; GGUF export dominates (150–190 s).
- **Concurrency:** two Director sessions worked M0 on 09-25; deconfliction is recorded in the decision log.

### Not done, and what remains unverified

1. Phone access over the tailnet (`tailscale serve --bg 3000`, then `WEBUI_URL=https://blackbox.taile9e808.ts.net` and a container recreate) — owner's click; acceptance is a chat with web search from the phone.
2. PRs #275, #276 and #277 are not merged. The M0 gate closes when all three are on `main`, each with one non-author ACCEPT at its head.
3. The 4-week paid-spend baseline required by the L1 checklist starts from the first real metered call; as of this report the project ledger holds two calls.

### Owner gates

- Merge #275: `gh pr merge 275 -R edwardjones3976/director-orchestrator --merge --match-head-commit ed317a813f85d2405738461f9f40924ff94ea28b`
- Publish Open WebUI to the tailnet (never Funnel): `tailscale serve --bg 3000` then `tailscale serve status` must show `https://blackbox.taile9e808.ts.net` → `http://127.0.0.1:3000` and no `Funnel on` line. If it errors on certificates, enable HTTPS certificates and MagicDNS in the Tailscale admin console first.
- Merge the M0 close-out PR(s) once a non-author review is on record.

### Next bounded action

M1 base shootout (PLAN.md §3, week 2 → 10-08): build the two held-out sets first (real-history answer key ~30 items; ~50 PR-reconstructed packets; raw material already at `C:\llm\datasets\heldout\raw`), then download the four bases and run schema-constrained decoding. Responsible: Director (sets), providers (grading). Expected evidence: `research/m1-shootout-<date>.md` with field-level acceptance per base and the license check.
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:35 UTC | END]] -->
