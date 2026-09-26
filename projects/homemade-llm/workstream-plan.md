<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:03 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:55 UTC | BEGIN]] -->
# Workstream plan — homemade-llm, phase M0 "Foundation"

Do not place secrets, credentials, tokens, or raw private source in this plan.
Workflow skills are procedure overlays, not permission grants. Canonical provider
roles: [`docs/team-operating-charter.md`](../../docs/team-operating-charter.md).
Project-local boundaries: [`provider-roles-contract.yaml`](provider-roles-contract.yaml).

## Phase frame

- **Project:** `homemade-llm` — Homemade LLM
- **Current state:** building; M0 gate report filed in PR #277; M0.9 and M0.11 in PR #276; #275 merged (f0a7752e, 23:31 UTC)
- **Phase outcome:** storage laid out; the Unsloth container trains on the GPU; LM Studio serves a merged GGUF over `/v1`; Open WebUI reaches it over Tailscale; paid-spend metering on; gitleaks available for datasets
- **Stopping point:** M0 gate — `project-state-report.md` with every number [verified]; target 2026-10-01
- **Total phase budget/time:** USD 25; ~1 week; max 3 dispatches in flight
- **Project channel/thread:** pending (no Slack surface; analysis-only)
- **Repository/ref/worktree:** `edwardjones3976/director-orchestrator` @ `1892b84` at PR-0, `f0a7752e` at the M0 gate, worktree `.worktrees\homemade-llm`
- **Rollout packet:** [`rollout-packet.md`](rollout-packet.md) — analysis-only
- **Deployment isolation:** no Slack app or channel; Open WebUI + SearXNG run under compose namespace `homemade-llm` with the ignored profile `C:\llm\secrets\open-webui.env` and state under `C:\llm` plus volume `homemade-llm_open-webui-data` (see rollout-packet.md); no orchestrator instance

## Selected modules

| Module | State | Purpose | Exit evidence |
| --- | --- | --- | --- |
| implementation | enabled | M0 scripts and compose file | PRs 1–3 merged |
| quality_and_security | enabled | gitleaks, Tailscale-only exposure | scan output; `tailscale serve status` |
| review_to_merge | enabled | non-author review of each PR | `ACCEPT at head <sha>` |
| research_and_evidence | enabled | M1 base-model brief | `research/base-models-*.md` |

## Workstreams

| ID | Module | Provider | Workflow skill | Role | Scope/surface | Depends on | Acceptance | Stop |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| HS-0 | review_to_merge | owner | none | operator | merge PR-0 | none | `main` contains `projects/homemade-llm/` | **hard stop** |
| M0.1 | implementation | claude | none | producer | `scripts/preflight.ps1` (read-only probe; `-Init` creates `C:\llm`, `G:\llm-mirror`, `E:\llm-archive`) | HS-0 | exit 0; parser clean; summary in PR | review |
| M0.2 | implementation | owner | none | operator | run `-Init`; ACL on `secrets`; decide old Open WebUI volume | M0.1 | dirs exist; logged | owner click |
| HS-1 | implementation | owner | none | operator | consent to `docker pull unsloth/unsloth` (pinned digest) | M0.2 | digest in decision log | **hard stop** |
| M0.3 | implementation | claude | none | producer | `scripts/train-smoke.ps1` + `train/smoke_train.py`: 20-step QLoRA, ungated `Qwen/Qwen3-0.6B`-class base, merged Q4_K_M GGUF | HS-1 | GGUF present; VRAM, tokens/s, wall time in PR | Go for base download |
| M0.4 | implementation | claude | none | producer | `scripts/serve-smoke.ps1`: `lms daemon up`, import, load, `/v1/chat/completions` with `response_format` json_schema, from host and container | M0.3 | HTTP 200 both ways; schema-valid reply | none |
| M0.5 | implementation | claude | none | producer | `compose/open-webui.yml` + `.env.homemade-llm.example` (3000 on 127.0.0.1, SearXNG 8088, `ENABLE_SIGNUP=false`) + `tailscale serve` runbook | M0.4 | `docker compose config --quiet`; chat with web search from phone over tailnet | Go (config change) |
| M0.6 | implementation | claude → owner | none | producer | Ollama retirement: robocopy blobs to `E:\llm-archive\ollama-2026-09\`, hash-verify; owner deletes | M0.2 | hashes match; ~94 GB recovered | **Go (destructive)** |
| M0.7 | implementation | claude → owner | none | producer | `scripts/mirror.ps1` + Scheduled Task; restore one file | M0.2 | restore hash matches | Go (task) |
| M0.8 | quality_and_security | owner | none | operator | move/delete stray `xo-fleet\ollama\id_ed25519` | none | logged | Go |
| M0.9 | quality_and_security | claude | none | producer | `scripts/scan-dataset.ps1`: gitleaks over `C:\llm\datasets` + existing `redact()` helpers | M0.2 | clean run on a planted-secret fixture fails, clean fixture passes | Go (gitleaks download) |
| M0.10 | research_and_evidence | claude | none | producer | spend meter: `scripts/spend_meter.py` totals the provider-dispatch receipts, which are the ledger; weekly total | HS-0 | first week's ledger in the M0 report | none |
| M0.11 | research_and_evidence | perplexity | perplexity-research-workflow | researcher | M1 brief: Qwen3-8B/14B, Phi-4, Mistral Nemo on structured output under 12 GB QLoRA | HS-0 | `research/base-models-YYYYMMDD.md` with provenance; Director evaluates | ledger (~USD 0.01) |
| M0.12 | design_and_decision | claude | none | producer | `project-state-report.md`; profile `framing` → `building` | all above | report merged | **milestone gate** |

Reviews rotate Grok → Codex. Gated bases (Llama, Gemma) are an M1 owner stop, not M0.

## Assignment packet (M0.11 — the only non-Claude M0 dispatch)

### M0.11: Base-model landscape brief

- **Starting state:** shortlist from Director web research 2026-09-24 (see `PLAN.md` §4.1–§4.2)
- **Target outcome:** one cited brief comparing four bases on structured-output quality, QLoRA VRAM on 12 GB, license, and GGUF support
- **Scope:** public web only; no repository context
- **Provider:** Perplexity
- **Contributor/system identity:** Perplexity API (model label recorded from the response)
- **Workflow skill:** `perplexity-research-workflow`
- **Authority granted:** public search and citation
- **Authority denied:** repository access, verdicts, training-data generation beyond O-026
- **Context supplied:** the four model names and the 12 GB constraint
- **Dependencies:** HS-0
- **Acceptance checks:** every claim cited; publication dates present
- **Evidence required:** URLs and retrieval dates
- **Budget/timeout/attempts:** ≤ USD 0.10; 10 min; 2 attempts
- **Fallback:** Director web research
- **Handoff target:** Director evaluation before it influences M1

## Coordination sequence

`BOOTSTRAP` → `PLAN` → (`WORKING` | `EVIDENCE`)\* → `REVIEW_READY` →
(`DECISION` | `OWNER_DECISION_REQUIRED`) → (`COMPLETE` | `PAUSED` | `RESHAPED`),
per [`project-bootstrap-protocol.md`](../../docs/project-bootstrap-protocol.md).
With no Slack surface, events are recorded as PR comments and in the state report.

## Owner and deterministic gates

- Every merge (owner, `gh pr merge --merge --match-head-commit`)
- HS-0, HS-1, the M0 gate (hard stops)
- Any download, deletion, ACL, Scheduled Task, or config change (Go)
- Any paid call that would breach USD 25; any fan-out over 3
- Serious-safety stop

## Status at the M0 gate (2026-09-25)

| ID | State | Where |
| --- | --- | --- |
| HS-0, M0.1, M0.2, HS-1, M0.3, M0.4, M0.6, M0.7, M0.8, M0.10 | done | PRs #266-#273; numbers in `project-state-report.md` |
| M0.3/M0.4 hardening (#275) | merged at ed317a81 (f0a7752e, 23:31 UTC) | main |
| M0.5 | containers up on 127.0.0.1:3000 and :8088; tailnet publish is an owner click | `compose/RUNBOOK.md` section 2 |
| M0.9, M0.11 | in PR #276 (peer Director session), awaiting a non-author review | #276 |
| M0.12 | this report | PR #277 |

Contribution tags record the author of the plan or artifact. They do not prove
the provider identity or satisfy any gate above.
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:03 UTC | END]] -->
