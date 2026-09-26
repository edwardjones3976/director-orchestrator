<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:06 UTC | BEGIN]] -->
# Project state report — homemade-llm, M0 "Foundation" gate

Evidence-first. `[verified]` = read or measured by the Director session that wrote this line on the date given; `[relayed]` = a provider's or a person's claim; `[stale]` = true at the stated time. No draft, simulation, or predicted-position label applies: every provider result below has a receipt.

## EVIDENCE

**Homemade LLM** is at the M0 gate. Canonical record: this file, `PLAN.md` §3 row "M0", `workstream-plan.md`, and PRs #266, #267, #268, #269, #271, #272, #273 (merged) #275 (merged 23:31 UTC as `f0a7752e`), #276 (M0.9 and M0.11, awaiting a non-author review) and #277 (this report).

- **Project ID:** `homemade-llm`
- **Assignment IDs:** HS-0, M0.1 to M0.12, RV-275
- **Report label:** delivered state (authenticated receipts and merged PRs; no prediction)
- **Categorized status:** review-ready for this report (PR #277); the M0 gate also waits on a non-author review of PR #276 and on the owner clicks listed under "Owner gates"

### What M0 set out to prove, and what the evidence says

| ID | Deliverable | Status | Evidence |
|---|---|---|---|
| HS-0 | PR-0 merged | done | #266 merged 2026-09-24 08:03 UTC; follow-up #268 merged 17:49 UTC `[verified]` |
| M0.1 | `scripts/preflight.ps1` | done | #267 merged; report `C:\llm\reports\preflight-20260924-154657-543-6c99d2.json`: all drive, layout, GPU, Docker, LM Studio checks OK; Tailscale WARN "not installed" at 15:46 UTC on 09-24, since installed `[verified]` |
| M0.2 | `-Init` run; `C:\llm` layout | done | `C:\llm\{hf,datasets,runs,models,seeds,secrets,reports,state,tools}` present; `G:\llm-mirror`, `E:\llm-archive` present `[verified 09-25]` |
| HS-1 | `unsloth/unsloth` pulled | done | image present, 33.1 GB, `unsloth/unsloth:latest` digest `sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739` (image built 2026-09-20) `[verified 09-25]` |
| M0.3 | smoke fine-tune | done | #271 merged; 10 runs on 09-24; latest `smoke-20260924-212438-a6dddc`: base `unsloth/qwen3-0.6b-unsloth-bnb-4bit` @ `e86f5289`, 20 steps, 50 rows, peak reserved 0.79 GB, train 28.0 s, ~98 tok/s, last loss 1.085, JSON sample valid, GGUF Q4_K_M 396,705,312 bytes, export 190 s, wall 286 s; torch 2.11.0+cu128 on the RTX 5070 `[verified from report JSON]` |
| M0.4 | serve smoke | done | `serve-smoke.ps1` in #271; LM Studio `/v1/models` lists 10 imported smoke GGUFs on 127.0.0.1:1234 `[verified 09-25 22:58 UTC]`; from inside the running Open WebUI container, `http://host.docker.internal:1234/v1/models` answered HTTP 200 `[verified 09-25 23:23 UTC]` |
| M0.3/M0.4 hardening | #275 | merged | xAI grok-4.6 ACCEPT at `ed317a81`, receipt `receipts/xai/20260925T230929Z.json`, USD 0.1639; Director re-ran unittest 14/14 and both PowerShell suites (9 + 14 ok) at that head; CI run 36120719806 green; merged by the owner 23:31 UTC as `f0a7752e` `[verified]` |
| M0.5 | Open WebUI + SearXNG compose | done except phone access | #272, #273 merged; `homemade-llm-open-webui-1` healthy on 127.0.0.1:3000 and `homemade-llm-searxng-1` on 127.0.0.1:8088, up since 09-25 ~18:00 UTC; `ENABLE_SIGNUP=false`; env file `C:\llm\secrets\open-webui.env` `[verified]`. `tailscale serve` not yet published: the Director session's attempt was denied by the auto-mode classifier (Expose Local Services) and is the owner's click; Tailscale is online as `blackbox.taile9e808.ts.net` but reports no cert domains, so HTTPS certificates may still need enabling in the admin console `[verified]` |
| M0.6 | Ollama retirement | done | robocopy 59 files, 93.415 GB, 0 failed, to `E:\llm-archive\ollama-2026-09\models\` in 10:20 at 162 MB/s (log `m06-archive-20260924-154719.log`); content verified on 09-25: every archived blob's SHA-256 equals its digest filename, 48/48 ok, 0 mismatched, 608 s (`m06-hash-verify-20260925-234625.txt`); `xo-fleet\ollama\models\blobs` now 16 KB `[verified]` |
| M0.7 | mirror + Scheduled Task + restore drill | done | `scripts/mirror.ps1` in #269; task `homemade-llm-mirror` Ready; run 2026-09-25 03:30 local mirrored 413 files, 8.119 GB, 0 failed, `secrets` excluded; `restore-drill.sha256` = `474CA9FA…335A` `[verified]` |
| M0.8 | stray `xo-fleet\ollama\id_ed25519` | done | file absent `[verified 09-25]` |
| M0.9 | dataset secret scan | in PR #276 (mixed authorship, see Not done) | gitleaks 8.30.1 at `C:\llm\tools\gitleaks\gitleaks.exe` `[verified]`; `scan-dataset.ps1` + `test-scan-dataset.ps1` materially authored by Cursor (cursor-agent, dispatched by the peer Director session); PR #276 opened 23:22 UTC at head `af3626a3` `[verified]` |
| M0.10 | spend meter | done | #269 merged; the provider-dispatch receipts are the ledger and `spend_meter.py` totals them, counting any call without a cost field as `uncosted` `[verified]`. **homemade-llm M0 paid spend: USD 0.55 across seven metered calls, none uncosted, at 2026-09-26 00:01 UTC (Perplexity brief 0.0398; xAI fact-check 0.0253; xAI reviews 0.1639 for #275 and 0.097 + 0.0799 + 0.0323 + 0.1134 for #277)** of the USD 25 envelope; every review of this PR adds to it, so the decision log carries the figure at each stamp |
| M0.11 | base-model brief | in PR #276 | Perplexity sonar-pro at 23:12 UTC returned mostly "not found" (USD 0.0398, receipt `receipts/perplexity/20260925T231241Z.json`); the Director-research fallback `research/base-models-20260925.md` is in PR #276 `[verified]` |
| M0.12 | this report; profile `framing` → `building` | this PR | — |

### Numbers that govern M1

- **C: free:** 356 GB (floor 150 GB) `[verified 09-25 22:58 UTC]`; `C:\llm` = 8.2 GB of the 120 GB cap (models 4.9 GB, hf 1.7 GB, runs 0.7 GB).
- **GPU headroom for the shootout:** the 0.6B smoke peaked at 0.79 GB reserved; 8B and 14B QLoRA numbers are not yet measured on this card. Unsloth's published floors are 6 GB (8B) and 8.5 GB (14B) `[relayed, research brief]`.
- **Turnaround:** one 20-step smoke run including GGUF export is under 5 minutes wall clock; GGUF export dominates (150–190 s) `[verified from the ten run reports]`.
- **Concurrency:** two Director sessions worked M0 on 09-25; deconfliction is recorded in the decision log (D-011) `[verified]`.

### Not done, and what remains unverified

1. Phone access over the tailnet (`tailscale serve --bg 3000`, then `WEBUI_URL=https://blackbox.taile9e808.ts.net` and a container recreate) — owner's click; acceptance is a chat with web search from the phone.
2. #275 merged at 2026-09-25T23:31:37Z as `f0a7752e` `[verified]`. PRs #276 and #277 are not merged; the M0 gate closes when both are on `main`, each with one non-author ACCEPT at its head. #276 currently has two material authors (Cursor for `scan-dataset.ps1` and its test; the peer Claude session for `research/base-models-20260925.md`), which the one-author, one-reviewer protocol (AGENTS.md rule 3) does not allow; it must be split into single-author candidates, or its attribution corrected, before it is merge-eligible. Noted on #276.
3. The 4-week paid-spend baseline required by the L1 checklist starts from the first real metered call; as of this report every call in the project ledger is a review or a research call, none from DIRECTOR-style production; the count and total are those in row M0.10 at its stamp, and each further review of this PR adds one receipt.

### Owner gates

- #275: merged (owner, 23:31 UTC). No action.
- Publish Open WebUI to the tailnet (never Funnel): `tailscale serve --bg 3000` then `tailscale serve status` must show `https://blackbox.taile9e808.ts.net` → `http://127.0.0.1:3000` and no `Funnel on` line. If it errors on certificates, enable HTTPS certificates and MagicDNS in the Tailscale admin console first.
- Merge #277 (this report) once CI is green at its reviewed head; merge #276 only after it is split into single-author candidates and each carries a non-author ACCEPT.

### Next bounded action

Finish M0 before any M1 work: (1) owner merges #277 when CI is green at its reviewed head; (2) #276 is split into single-author candidates, each reviewed by a non-author and merged; (3) owner publishes Open WebUI with `tailscale serve --bg 3000` and completes the phone acceptance (a chat with web search over the tailnet). Then the Director records the gate decision as a decision-log row and the profile moves to the M1 phase. Responsible: owner for (1) and (3), the peer Director session for (2). Expected evidence: `main` contains this report and the M0.9/M0.11 artifacts; `tailscale serve status` shows the tailnet URL; a decision-log row "M0 gate closed" with the final ledger figure. M1 (base shootout, held-out sets first; raw material already at `C:\llm\datasets\heldoutaw`) starts only after that row exists.
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:06 UTC | END]] -->
