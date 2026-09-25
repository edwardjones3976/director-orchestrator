<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:29 UTC | BEGIN]] -->
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
| 2026-09-24 ~08:00 | O-029 | Owner, after the Director stopped for a Go on the PR-0 relay, push, PR and review: "You were already authorized. Please limit bypass requests to high-level safety concerns only." Steps an approved plan covers run through to the next real owner gate (merge). | owner chat [relayed] |
| 2026-09-25 ~22:56 | O-030 | Owner: "Close out M0, start with #275 review." | owner chat [relayed] |
| 2026-09-25 23:12 | D-009 | #275 reviewer of record is the xAI direct API lane (grok-4.6): Grok Build CLI was exhausted (HTTP 402 at 19:07 UTC) and Codex is a material author of the candidate (commits ee06801, ed317a8), so the Codex ACCEPT in the PR body does not count. Verdict ACCEPT at ed317a81, no findings, USD 0.1639, receipt provider-dispatch receipts/xai/20260925T230929Z.json. The Director re-ran unittest (14/14) and both PowerShell suites (9 + 14 ok) at that head. | receipts + CI [verified] |
| 2026-09-25 ~23:16 | D-010 | Publishing Open WebUI with `tailscale serve --bg 3000` is an owner click: the auto-mode classifier denied it to the Director session (Expose Local Services). Tailscale is online as blackbox.taile9e808.ts.net with no cert domains yet, so HTTPS certificates may need enabling in the admin console first. | tailscale status [verified] |
| 2026-09-25 ~23:18 | D-011 | Two Director sessions worked M0 on 09-25: this one and the Claude Desktop session "Local LLM setup Opus 5.5 Med" (worktrees hl-m0-close with the uncommitted M0.9 script and M0.11 brief, and hl-pr275-review). Proposed split by cross-session message: the peer keeps M0.9 and M0.11; this session files M0.12 and the record updates. Exactly one verdict was posted on #275. Before any homemade-llm drive, check ListAgents, recent dispatch receipts and dirty hl-* worktrees for a peer. | ListAgents + git worktree list [verified] |
| 2026-09-25 ~23:23 | D-012 | `unsloth/unsloth:latest` pinned at digest sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739 (image built 2026-09-20). LM Studio answers HTTP 200 at host.docker.internal:1234 from inside the running Open WebUI container. | docker [verified] |
| 2026-09-25 ~23:19 | D-013 | The paid-spend ledger is the provider-dispatch receipts folder, read by scripts/spend_meter.py; no separate spend.jsonl exists or is needed. homemade-llm M0 paid spend: USD 0.20 of 25 (xAI review 0.1639, Perplexity brief 0.0398). | spend_meter.py [verified] |
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:29 UTC | END]] -->
