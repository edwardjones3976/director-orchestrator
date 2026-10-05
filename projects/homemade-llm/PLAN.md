<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:39 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:55 UTC | BEGIN]] -->
# Homemade LLM — Plan v1.3

**Owner:** Ed · **Project Director:** Claude (decision-log O-001) · **State:** building; M0 closed 2026-09-26 (O-031), M1 entered; M0 record reconciliation pending until PR #293 review, CI and merge gates pass · **Base:** `1892b84` at PR-0; `58fb485` at the M0 gate (main when O-031 was given; `f0a7752e` when the gate report was first filed)

Grades: **[verified]** read or measured by this project · **[relayed]** the owner's words or a document · **[research]** web research on 2026-09-24 with a source.

> **Provenance.** v1.1.0 was written by the owner on 2026-09-24 (~05:15–05:58 UTC) and pasted into a Claude Code session. Its text is not stored on disk and is **not reproduced here**. v1.2 recorded the verified corrections to it; v1.3 adds the launch definition. This file lists both. Where v1.1.0 is silent or superseded, this file governs. If the owner supplies v1.1.0 again, it goes in as an appendix in a later PR.

## 1. Chain of command

Ed → Claude (Director of this project only; recorded in [`project-profile.yaml`](project-profile.yaml) and the `deviations` block of [`provider-roles-contract.yaml`](provider-roles-contract.yaml)) → providers by capability. Repository-level leadership documents are unchanged. [relayed]

## 2. Launch definition (v1.3)

There are two staged launches, each with its own go/no-go checklist. **If a gate misses its bar, the date slips and the bar holds** (O-022).

| Launch | End of | Target date | Headline bar |
|---|---|---|---|
| **L1 DIRECTOR** | M3 | **2026-11-12** | > 60 % of packets accepted as-is on both held-out sets |
| **L2 COMPANION** | M5b | **2026-12-17** | hard-line suite 100 %; judge panel and owner daily-use rating ≥ 4.3/5 |

Launch audience: the owner on blackbox, the owner from phone and other devices over Tailscale, and orchestrator agents via `/v1` (O-011, O-014).

### 2.1 L1 — DIRECTOR go/no-go

- **Quality**
  - > 60 % accepted as-is, field-level, on **both** held-out sets: the real-history answer key (~30 items) and ~50 packets reconstructed from PR bodies. Graded by two non-author providers.
  - The remaining packets need ≤ 10 % of their text changed.
  - 100 % schema-valid via `response_format: json_schema`.
  - ≤ 3-point regression on full `ifeval` and `gsm8k`.
  - Beats the untuned base in a blind comparison.
- **Integration**
  - The local lane passes a readiness function modelled on `zai_trial_readiness()`.
  - One week in shadow mode through the gateway, with edit distance trending down.
  - Automatic fallback to a paid lane when LM Studio is down.
- **Ops**
  - `lms daemon` starts at login; one-line unload and reload work.
  - `tailscale serve` HTTPS works from the phone; signup is disabled; port 3000 is bound to 127.0.0.1.
  - The nightly mirror has been restore-tested.
- **Data and compliance**
  - Every training row carries `source_provider` and `terms_tier`; there are zero excluded-tier rows.
  - gitleaks is clean on the dataset; the dataset holds no personal data.
- **Economics**
  - At least 4 weeks of metered paid-spend baseline.
  - The savings target is set (O-016).

### 2.2 L2 — COMPANION go/no-go

- **Quality**
  - The hand-written hard-line suite passes 100 % in promptfoo.
  - The LLM judge panel rates persona consistency against the Persona Bible at ≥ 4.3/5.
  - The owner's daily-use week rates ≥ 4.3.
  - Memory recalls ≥ 8 of 10 planted facts after 7 days.
- **Privacy**
  - Chats and memory live only in the Open WebUI volume or `C:\llm`.
  - They are mirrored, never committed, and a one-command wipe has been tested.
- **Ops**
  - Everything in L1's Ops list.
  - A documented way to switch between DIRECTOR and COMPANION on 12 GB (JIT loading plus TTL auto-evict).

## 3. Milestones and calendar

| Wk | Milestone | Exit gate (every gate is a hard stop) | Owner time |
|---|---|---|---|
| 1 (→ 10-01) | **M0** Foundation, plus spend meter, gitleaks, and Tailscale/Open WebUI — see [`workstream-plan.md`](workstream-plan.md) | state report; every number [verified] | merge PR-0; Go clicks |
| 2 (→ 10-08) | **M1** base shootout: Qwen3-8B, Qwen3-14B, Phi-4 14B, Mistral Nemo 12B (control); base model + schema-constrained decoding on both held-out sets | winner by field-level acceptance; license check | pick the winner |
| 3–5 (→ 10-29) | **M2** DIRECTOR data v1 (~800 rows, tier-tagged, scrubbed); adapter v0.1 → v0.3 | **wk-4 early warning: v0.1 < 45 % → stop.** Exit: > 60 % on both sets; ≤ 3-point regression | ~1 h/wk |
| 6–7 (→ 11-12) | **M3** local lane: `OWNER_DECISION_REQUIRED` record, after PRs #263/#264; OpenAI-compatible chat adapter; 1 week of shadow mode | **L1 checklist** | L1 go/no-go |
| 8–9 (→ 11-26) | **M4** Persona Bible (friend/conversational); hard-line suite written by hand; memory rung 1 | Bible and suite approved | approve Bible |
| 10–11 (→ 12-10) | **M5a/b** COMPANION Stage B/C plus memory | suite 100 %; judge panel ≥ 4.3 | — |
| 12 (→ 12-17) | Owner daily-use week | **L2 checklist** | daily use and rating |
| post | **M6** hardening, runbooks, restore drill; monthly retrain loop (O-025) | fresh-machine restore < 1 day | — |

## 4. Research findings (2026-09-24)

### 4.1 Feasibility on blackbox [research]

- **Training on 12 GB**
  - Unsloth QLoRA minimums: 8B needs 6 GB, 14B needs 8.5 GB. Llama-3.1-8B reaches about 21.8K context on 12 GB.
  - RTX 50 cards are officially supported. Pin the image digest.
  - Default to 8–9B; use 14B only at 2–4K context.
- **Constrained decoding**
  - LM Studio `/v1/chat/completions` honours `response_format: json_schema`. `/v1/responses` does not (lmstudio bug #2403).
  - So acceptance is measured on **field content**, not parse rate.
  - `model-work-packet-v1` has no `$defs`, which small models handle badly, so it needs no change.
- **Data volume:** 500–2,000 curated rows typically beat base plus prompting on a narrow task.
- **Always-on serving:** `lms daemon up` + `lms server start`; `lms unload` frees the GPU; JIT loading with TTL auto-evict.
- **Tailscale and Open WebUI**
  - Use `tailscale serve`, never Funnel.
  - Set `ENABLE_SIGNUP=false` and bind port 3000 to 127.0.0.1.
  - Admin-panel settings override environment variables (`ENABLE_PERSISTENT_CONFIG`), so lock auth down on day one.
- **Memory ladder (O-023)**
  1. Open WebUI native Memory with background review. The docs cite a ~27B model as the local floor, so this is risky at 8–14B.
  2. Auto Memory filter function.
  3. mem0 with an LM Studio LLM and embedder.
- **Evaluation**
  - promptfoo: `llm-rubric` scoring, a local judge, and hand-written hard-line cases.
  - lm-eval-harness tinyBenchmarks as a quick screen (±2–3 points of noise). Confirm any drop over 3 points on the full benchmarks.

### 4.2 Provider terms and base licenses [research; not legal advice]

No provider exempts personal or non-distributed use. Training-data rules (O-026):

| Tier | Providers | Use |
|---|---|---|
| Excluded | Z.ai, xAI, Google consumer Gemini, Antigravity | Grade and evaluate only; never in training data, including best-of-N or preference pairs |
| Competing-only clause | Anthropic API, OpenAI, Gemini API, Cursor, Perplexity | May generate exemplars; the owner accepts the residual risk |
| Unclear | Anthropic consumer, GitHub Copilot | Grade only until clarified |

The OpenAI, xAI and Perplexity pages returned 403, so their clauses come from search results [relayed]. They are re-verified before the first M2 training run.

Base licenses:
- **No gating:** Qwen3-8B (Apache-2.0), Phi-4 (MIT), Mistral Nemo (Apache-2.0).
- **Gated** (owner stop): Llama 3.x and Gemma 3.

### 4.3 Repository data [verified]

- **Real packets:**
  - 16 real dispatched assignments in `engineering-workload\assignments.sqlite3`.
  - 4 Z.ai trial runs.
  - About 10 hand-written work orders in `Claude outputs\`.
  - Together these form the held-out answer key and are never trained on (O-027).
- **PR history:** about 265 PR bodies and threads, on GitHub only.
- **No paid-spend history:** the L-7 baseline is metered forward from M0.
- **No content secret scanner:** CI `secret-hygiene` checks filenames only.
- **No acceptance or edit-distance metric exists.**
- **M3 reuses:**
  - `zai_lane.py`, `zai_trial.py`, and the `config.py` gating (`zai_trial_readiness()`).
  - There is no Z.ai profile YAML to copy.
  - It needs a new OpenAI-compatible `/chat/completions` adapter; today only the OpenAI Responses API is wired.

## 5. Resources

- **Compute:** blackbox only (RTX 5070 12 GB, local only, O-020). `C:\llm` hot tier capped at 120 GB; jobs refused below 150 GB free on C:. `G:\llm-mirror` nightly; `E:\llm-archive` cold.
- **Money:** USD 25 per milestone, ≤ USD 175 in total. Paid calls inside the envelope are recorded in a ledger. A call that would breach it is a hard stop. M2 exemplar generation is the largest line.
- **People:**
  - Owner: 1–2 h/week, for gates, merges and the daily-use week.
  - Claude: directs and authors scaffold (O-003).
  - Providers: competing-only-tier providers generate exemplars; all providers grade.
- **Tools to add:** gitleaks, promptfoo, lm-eval-harness, Tailscale, and a `difflib` edit-distance scorer.

## 6. Changes versus v1.1.0 (v1.2 corrections, all [verified] unless noted)

1. **Plan gap G-1 closed.** The local checkout exists and `gh` is authenticated. DIRECTOR's knowledge sources are the real repo docs: team charter, `model-work-packet-v1` schema, attribution policy, bootstrap protocol, provider profiles.
2. **Storage tiers.** They follow the measured drives, not the assumed 921 GB free on C:.
3. **Serving.**
   - Merge and GGUF export happen inside the Unsloth container; LM Studio serves merged GGUFs only.
   - Adapter hot-swap is documented, not built. The old §13.1 moves to v1.x.
4. **M3.**
   - The slack-bridge is dropped (the workspace is at the free-plan app limit).
   - DIRECTOR becomes a local lane at `http://127.0.0.1:1234/v1`, gated the way Z.ai is.
   - It opens with an `OWNER_DECISION_REQUIRED` record, after #263/#264.
5. **Chat UI.** Open WebUI and SearXNG are deployed fresh against LM Studio. SearXNG moves to port 8088 because 8080 collides with the orchestrator. Ollama is retired.
6. **Privacy.** `Life_Coach.Modelfile` and the old Open WebUI data are never committed. Persona seeds are referenced by SHA-256 only.
7. **Environment.**
   - Docker runs from Windows Docker Desktop.
   - Training happens only in the container, with `C:\llm` mounted at `/workspace`.
8. **Roster and routing.** They follow the wired providers (see `provider-roles-contract.yaml`).
9. **M0 and decisions.**
   - M0 is rewritten (see `workstream-plan.md`).
   - The old §15/§16 are replaced by [`decision-log.html`](decision-log.html).
   - M0.3's smoke base is now an ungated Qwen3-class model, in line with the M1 shortlist. v1.2 named Qwen2.5-0.5B or Llama-3.2-1B.
10. **v1.3.**
    - §2–§5 added.
    - M2's gate changes from "format compliance ≥ 90 %", which constrained decoding now makes trivial, to field-level acceptance > 60 % (the owner's words, O-015) with the week-4 early warning.

## 7. Risks

| ID | Risk | Mitigation |
|---|---|---|
| R-22 | C: headroom is falling (~24 GB/h under CI runners and Docker) | 150 GB floor enforced by preflight |
| R-23 | Another Claude session is working in the same repo | Changes stay inside the `projects/homemade-llm/` boundary |
| R-24 | Reviewer pool shrinking (Copilot out of quota; Z.ai trial ends 09-28) | Grok → Codex rotation |
| R-25 | Personal data in xo-fleet artifacts | D-006; gitleaks |
| R-26 | CI attribution checks run against a moving main | Tagged merge commit before each review |
| R-27 | Only 16 real packets; a synthetic-heavy dataset may learn provider style rather than the owner's | Evaluate only on held-out real history |
| R-28 | Provider terms change | Tier tags on every row; purge and retrain |
| R-29 | Memory unreliable on 8–14B models | Memory ladder; custom store only at rung 3 |
| R-30 | One GPU shared by serving, training and gaming | Scripted unload; training overnight |
| R-31 | No spend baseline | Meter from week 1 (M0.10) |
| R-32 | M3 blocked by #263/#264 | L1 slips with them (O-022) |

## 8. Never without an explicit owner Go

- The `codex queue` relay, or any other message to another Director
- Any download
- ACL, Scheduled Task or config changes
- Deleting Ollama blobs
- Any paid call that would breach the milestone envelope
- Fan-out over 3
- Anything outside `projects/homemade-llm/`, including the live stack and PRs #263–#265
- Any merge
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 20:45 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:39 UTC | END]] -->
