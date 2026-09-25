<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | BEGIN]] -->
# Decision log — homemade-llm

One line per decision. `[relayed]` marks the owner's words as relayed by the
Director from the Claude Code session; `[verified]` marks a decision grounded in
something this project read or measured. Times are UTC.

| Date | ID | Decision | Source |
|---|---|---|---|
| 2026-09-24 ~06:05 | O-001 | Owner appointed Claude (Claude Fable 5.1) as Director of this project only: "You will lead this specific project; that doesn't mean you'll lead any other project outside it. As the acting Director of this project, you will direct other commercial AI platforms to help build the following project." | owner chat [relayed] |
| 2026-09-24 ~06:05 | O-002 | Owner directed a branch and subsection in director-orchestrator, locally and on GitHub, "so you can cleanly run the program from there to here", and a reviewed and revised plan "with hard stops after major milestones or token-intensive drives". | owner chat [relayed] |
| 2026-09-24 ~06:30 | O-003 | Authoring boundary: "a mix of 1 and 3, start with 3" — Claude authors the M0 foundation scripts plus all scaffold, docs, plan, and work orders; from M1 providers author code, datasets, and training runs and Claude authors scaffold/docs/work orders only. A non-author reviews every Claude PR. | owner answer [relayed] |
| 2026-09-24 ~06:30 | O-004 | Hard stops: every milestone gate, before any paid provider call, before any fan-out larger than three agents. | owner answer [relayed] |
| 2026-09-24 ~06:30 | O-005 | Budget: USD 25 per milestone for commercial-provider calls, no per-call ask. Director reading (open to owner override): paid calls inside the envelope proceed and are ledgered; a call that would breach it is a hard stop. | owner answer [relayed] |
| 2026-09-24 ~06:30 | O-006 | COMPANION keeps its placeholder name until the Persona Bible (M4). | owner answer [relayed] |
| 2026-09-24 ~06:45 | O-007 | Subsection lives at `projects/homemade-llm/` in director-orchestrator; training code runs in the Unsloth container against `C:\llm`; datasets stay on `C:\llm` under DVC. | owner answer [relayed] |
| 2026-09-24 ~06:45 | O-008 | M3 architecture: DIRECTOR is served by LM Studio as a local OpenAI-compatible provider lane inside the existing runtime, gated like Z.ai (absent from production enums until the owner activates it). No new slack-bridge container or Slack app. | owner answer [relayed] |
| 2026-09-24 ~06:45 | O-009 | xo-fleet: retire Ollama; redeploy Open WebUI + SearXNG fresh against LM Studio; the ten Modelfiles become reference material; Ollama blobs archived to E: then deleted from C:. | owner answer [relayed] |
| 2026-09-24 06:10 | D-001 | Plan gap G-1 (repo private, Director role reconstructed second-hand) closed: the local checkout exists and `gh` is authenticated with `repo` scope; plan §4.2 knowledge sources are now the real repo documents. | measured [verified] |
| 2026-09-24 06:35 | D-002 | LM Studio serves merged GGUFs only: `lms load` exposes no adapter option. One merged GGUF per role; adapter hot-swap (llama.cpp `--lora`, Ollama `ADAPTER`) is documented, not built. | measured [verified] |
| 2026-09-24 06:35 | D-003 | Storage tiers: `C:\llm` hot (NVMe, cap 120 GB, refuse jobs below 150 GB free); `G:\llm-mirror` nightly mirror (internal SATA HDD, NTFS); `E:\llm-archive` cold only (USB HDD, exFAT: no git, DVC, or HF cache). | measured [verified] |
| 2026-09-24 06:40 | D-004 | Training runs only inside `unsloth/unsloth` (Unsloth's single image; Blackwell supported per Unsloth docs); WSL has no pip and none is needed; Docker commands run from Windows Docker Desktop. | Unsloth docs + host probe [verified] |
| 2026-09-24 07:00 | D-005 | Reviewer of record for Director-authored PRs: Grok CLI (`dispatch.py grok --role review`, plan mode), Codex as fallback; merge is the owner's gate while the delegation framework is `ATTENDED`. Not eligible: Z.ai (trial), Copilot (out of quota), Antigravity (contract denies merge). | repo settings + dispatch receipts [verified] |
| 2026-09-24 07:00 | D-006 | Privacy: `Life_Coach.Modelfile` (owner's personal profile) and the old `xo-fleet/open-webui/` data directory are never committed; persona seeds live at `C:\llm\seeds\` and are referenced by SHA-256 only. | AGENTS.md rule 2 [verified] |
| 2026-09-24 07:05 | D-007 | M3 opens with an `OWNER_DECISION_REQUIRED` record because a runtime lane edits `src/director_orchestrator/` under the ChatGPT/Codex Director's authority; it is sequenced after PRs #263/#264 merge. | project-leadership.md [verified] |
| 2026-09-24 07:30 | D-008 | PR-0 base is `1892b841a78580b2664236b16dcc61094bdce9c8` (`main` after PR #265). | git [verified] |
| 2026-09-24 ~07:45 | O-010 | (L-1) Launch is staged: **L1 DIRECTOR** at the end of M3 and **L2 COMPANION** at the end of M5b, each with its own go/no-go checklist in `PLAN.md`. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-011 | (L-2) Launch audience: owner on blackbox, owner from phone/other devices, and orchestrator agents via `/v1`. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-012 | (L-3) Headline purposes: briefs the owner barely edits; less paid-API spend; a companion worth talking to. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-013 | (L-4) Timeline ~12 weeks, mid-December 2026. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-014 | (L-5) Remote access via Tailscale (`tailscale serve`, never Funnel); no public exposure. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-015 | (L-6) DIRECTOR bar: >60 % of packets accepted as-is, field-level; remainder ≤10 % text changed. Early warning: adapter v0.1 below 45 % at M2 is a hard stop. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-016 | (L-7) Paid spend is metered from M0; the savings target is set at M3 from that baseline (no spend history exists today). | owner answer [relayed] + repo probe [verified] |
| 2026-09-24 ~07:45 | O-017 | (L-8) COMPANION judged by an LLM judge panel, an automated hand-written hard-line suite (100 %), and one week of owner daily use. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-018 | (L-9) COMPANION is a friend/conversational persona. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-019 | (L-10) DIRECTOR data sources: real orchestrator history, Claude exemplars, other providers' exemplars and critiques, owner seeds — subject to the provider-terms tiers in O-026. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-020 | (L-11) Compute is local only; a missed bar is answered with a smaller model and better data, never rented GPUs. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-021 | (L-12) Owner time ~1–2 h/week; decisions are batched into one weekly packet. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-022 | (L-13) If a gate misses its bar, the date slips and the bar holds. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-023 | (L-14) COMPANION gets a custom memory store, reached by a ladder: Open WebUI native Memory → Auto Memory filter → mem0 (LM Studio backend). | owner answer [relayed] + research |
| 2026-09-24 ~07:45 | O-024 | (L-15) Serving is always-on with a one-command unload for gaming. | owner answer [relayed] |
| 2026-09-24 ~07:45 | O-025 | (L-16) After launch: monthly retrain from feedback through the same gates. | owner answer [relayed] |
| 2026-09-24 ~07:48 | O-026 | (T-1) Owner accepts residual terms risk: providers whose clause bars only *competing* models (Anthropic API, OpenAI, Gemini API, Cursor, Perplexity) may generate DIRECTOR exemplars for this private, never-distributed model. Z.ai, xAI, Google consumer Gemini and Antigravity grade only. Every row carries `source_provider` + `terms_tier`. Not legal advice; blocked-page clauses re-verified before the first M2 run. | owner answer [relayed] + web research |
| 2026-09-24 ~07:48 | O-027 | (G-1 gold) The answer key is real history only (~30 items, held out), reported with a ~50-packet PR-reconstructed held-out set; L1 needs > 60 % on both (the owner's bar in O-015). | owner answer [relayed] |
| 2026-09-24 ~07:48 | O-028 | (D-1) Target dates locked: L1 2026-11-12, L2 2026-12-17. | owner answer [relayed] |
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:36 UTC | BEGIN]] -->
| 2026-09-24 | O-029 | (HS-1) The Unsloth training image is pinned by digest: `unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739` (33.1 GB). Open WebUI `@sha256:8b432fe0…f3cae9` and SearXNG `@sha256:bcfaed40…b149f83` are pinned the same way. | owner consent; director record |
| 2026-09-24 | O-030 | The GGUF merge source is bound, not checked: the export runs in an offline child process (`HF_HUB_OFFLINE=1`) against the local snapshot `unsloth/qwen3-0.6b@db4e90b8359f816f09bf5c944797ce116f26da3c`. Any `-Base` or `-Revision` override must bring its own `-MergeSource`/`-MergeRevision` pin. | director decision after reviewer finding (#271) |
| 2026-09-24 | O-031 | `HF_TOKEN` is never passed to the training or export container, and the bundled llama.cpp converter would send it as a bearer header. Training refuses to start if `<HotRoot>\hf\token` or `stored_tokens` exists, and the container's token paths point outside every mount. | director decision (#271) |
| 2026-09-25 | O-032 | (M0.11) All four M1 candidates are ungated: Qwen3-8B and Qwen3-14B (Apache-2.0), Phi-4 (MIT), Mistral Nemo 12B (Apache-2.0). M1 needs no owner Hugging Face token or license acceptance; this corrects the gating assumption in PLAN §4. | research (#276), Hugging Face API 2026-09-25 |
| 2026-09-25 | O-033 | (M0.10 deviation) The spend meter reads the provider-dispatch receipts as the ledger instead of appending rows to `C:\llm\reports\spend.jsonl`. It reports "uncosted" calls, never zero, and receipts from xAI and Perplexity carry no project tag. | director record (#269) |
| 2026-09-25 | O-034 | (G-1 follow-up) The Z.ai trial packets cannot be recovered, because receipts keep only their hash. Set A therefore holds 22 real items: 16 sha-verified assignment packets and 6 provider-directed launch packets. Owner to confirm, or allow a top-up from other real dispatch packets. | director record; **owner decision pending** |
| 2026-09-25 | O-035 | Triage rule for automated connector findings: fixed in the PR only when they are P1, or could cause false evidence, data loss, secret or network exposure, or an indefinite GPU hold; other hardening is deferred to a follow-up. The merge gate stays reviewer ACCEPT at the exact head plus green CI. | director decision (#271) |
| 2026-09-25 | O-036 | M0 gate: 10 of 12 items closed. `tailscale serve` (owner) and the merge of #276 carry into M1 week 1. The profile moves `framing` → `building` when this report merges. | director report (M0.12) |
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:36 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 23:40 UTC | END]] -->
