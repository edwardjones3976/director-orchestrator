<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:33 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:03 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:31 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | BEGIN]] -->
# Project rollout packet — homemade-llm

This packet records the project-specific deployment boundary while the project
is analysis-only. Unknown or unavailable live identities stay `pending`; none
are borrowed from another deployment. No secret values appear here.

## Project and deployment identity

- **Project ID/name:** `homemade-llm` / Homemade LLM
- **Repository/ref/worktree:** `edwardjones3976/director-orchestrator` @ `1892b841a78580b2664236b16dcc61094bdce9c8` / `C:\Users\edwar\director-orchestrator\.worktrees\homemade-llm`
- **Deployment intent:** analysis_only (M0–M2); planned for M3 as a local provider lane, subject to `OWNER_DECISION_REQUIRED`
- **Concurrent with other active Director deployments:** true (director-orchestrator, edge-engine-director, long-table-director, project-uppercut-director stacks are live on this host)
- **Deployment profile:** `C:\llm\secrets\open-webui.env` (outside the repository, ACL owner + SYSTEM; template `compose/.env.homemade-llm.example`)
- **Compose namespace:** `homemade-llm` (Open WebUI + SearXNG only, deployed 2026-09-24 for M0.5; no orchestrator instance)
- **State namespace:** `C:\llm\` on the host (created by `preflight.ps1 -Init`), plus the Docker volume `homemade-llm_open-webui-data`, archived into `C:\llm\state\open-webui` by `scripts/backup-openwebui.ps1`; never a repository path
- **Ports:** all loopback only: LM Studio `127.0.0.1:1234`, Open WebUI `127.0.0.1:3000`, SearXNG `127.0.0.1:8088` (8080 belongs to the orchestrator). Phone access only through `tailscale serve` (see `compose/RUNBOOK.md`)

## Allowlists and execution boundaries

- **Project allowlist:** `homemade-llm` (intent only; no runtime policy entry exists)
- **Repository allowlist:** `edwardjones3976/director-orchestrator`, path `projects/homemade-llm/` only
- **Starting-ref allowlist:** `1892b841a78580b2664236b16dcc61094bdce9c8`
- **Data classification:** mixed — public model weights and public-domain corpora; private owner-authored material for COMPANION stays on the host
- **External effects:** explicitly_scoped
  - **Allowed:** Download of the four pinned M1 base weight sets from Hugging Face, one at a time, with storage guards (O-034; done)
  - **Allowed:** Independent reviews through the owner-approved provider routes, including the owner-approved xAI review of the set B v2 packets with private values redacted (done)
  - **Allowed:** Any further external effect only after its own owner approval, recorded in decision-log.md
  - **Prohibited:** Any public exposure of Open WebUI or LM Studio (Tailscale serve only, never Funnel)
- **Paid execution:** owner-authorized and gated — USD 25 per milestone ledger; a call that would breach it stops
- **Automerge/deployment/publication:** disabled; merge is the owner's gate

## Slack isolation

- **Workspace ID:** pending (owner decides at M3 whether the DIRECTOR lane uses the existing project channel)
- **Slack owner-user allowlist reference:** pending
- **Runtime channel allowlist:** pending
- **Dedicated Slack app display name:** pending (the workspace is at its free-plan app limit; O-008 avoids a new app)
- **Slack app ID:** pending
- **Slack bot user ID:** pending
- **Slack app manifest SHA-256:** pending
- **Installation status:** not_created
- **Socket Mode status:** disabled
- **Dedicated project channel ID:** pending
- **Bot membership:** not_invited
- **App-token secret reference:** `SLACK_APP_TOKEN` in the ignored deployment profile (not created)
- **Bot-token secret reference:** `SLACK_BOT_TOKEN` in the ignored deployment profile (not created)
- **Concurrent-deployment collision check:** not_applicable until M3

## Provider and workflow readiness

- **Providers enabled:** Grok CLI (reviewer of record), Codex (fallback reviewer), Perplexity (public research), Antigravity and Copilot CLI (producers from M1; Copilot out of monthly quota as of 2026-09-23), Cursor (PR bots), Firecrawl (retrieval). Z.ai excluded (trial lane).
- **Budget and timeout:** USD 25 per milestone; three attempts per provider contact; 20-minute review timeout
- **n8n/workflow identity:** not_applicable
- **Callback and internal-secret status:** not_applicable (no runtime instance)
- **Usage-monitor status:** disabled (ledger kept by the Director; envelope and rules in `PLAN.md` §5 "Resources", spend lines in each gate's `project-state-report.md`)

## Activation evidence

- **Profile collision check:** pending
- **Slack app/channel verification:** pending
- **Off-channel event rejection:** pending
- **Non-owner event rejection:** pending
- **Replayed event handling:** pending
- **Cross-deployment isolation:** pending
- **Paid-disabled health check:** pending
- **Slack intake dry run:** pending
- **n8n route check:** not_applicable
- **Provider checks:** dispatch receipts under the provider-dispatch skill's `receipts/` (outside the repository)
- **Restart/recovery check:** pending
- **Exact configuration identity:** pending

Activation may proceed only through the owner-authorized gates recorded in the
project profile.

## Communication fallback

No Slack surface exists for this project. Communication is the owner's Claude
Code session, PR threads on GitHub, and `handoffs/` records. No other
deployment's Slack app, channel, credentials, profile, or state namespace is
used as a fallback.

## Rollback

- **Stop command or action:** from `projects/homemade-llm/compose/`, `docker compose -p homemade-llm -f open-webui.yml --env-file C:\llm\secrets\open-webui.env down` (Open WebUI + SearXNG only; see `compose/RUNBOOK.md` §4); `lms unload --all`
- **Configuration rollback:** the same `down` command (never `-v`, which deletes the chat data volume), then remove `C:\llm\secrets\open-webui.env`; nothing in the orchestrator runtime references this project
- **State restore:** `G:\llm-mirror` → `C:\llm` by robocopy; adapters and datasets also in `E:\llm-archive`
- **Slack rollback:** not_applicable
- **Evidence retained:** PR numbers, exact SHAs, dispatch receipts, `C:\llm\reports\`

## Disposition

- **Current state:** explicitly_scoped
- **Blocking fields:** none for the M0 gate; every Slack identity is pending by design until M3
- **Next responsible party:** owner (D-018 and D-019 dispositions; M1 plan approval; WEBUI_URL), then Director under the approved M1 plan
- **Next bounded action:** Ed disposes of D-018 (#280 merged before M0 closed) and D-019 (held-out sets A and B produced before pre-staging, quarantined for M1 scoring), and approves the M1 workstream plan (proposed separately); no M1 provider dispatch before the plan is approved, and no M1 scoring on sets A or B before D-019 is disposed of. Set B also stays incomplete until its batch 1 review is authenticated. The pre-plan work has stopped; the O-034 downloads are done and verified.
- **Expected evidence:** the recorded D-018 and D-019 dispositions and M1 plan approval; this PR's six required CI jobs green at its reviewed head
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:31 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:33 UTC | END]] -->
