<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:48 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:44 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:28 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:11 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:00 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:44 UTC | BEGIN]] -->
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
  - **Allowed:** Independent reviews through owner-approved provider routes, including the redacted Set B v2 review and authenticated batch-1 ACCEPT on the frozen packet (`xai/20260926T225645Z-268799-d4c6.json`, response ID `473bbadc-7d1a-93af-9b73-6299017e9b97`). The prior incomplete rerun receipt remains historical evidence. pr-45 is a known MINOR reference imperfection to disclose with Set B results; M1 scoring gates remain separate [verified, receipts; joint disposition in PR #294 comment 5850665622].
  - Any new external effect requires a profile revision first (`fallbacks.changed_scope: reshape_profile_before_dependent_work`).
  - **Prohibited:** Any public exposure of Open WebUI or LM Studio (Tailscale serve only, never Funnel)
- **Paid execution:** owner-authorized and gated — USD 25 per milestone ledger; a call that would breach it stops
- **Automerge/deployment/publication:** disabled; Cursor is #293's eligible merger only after its own thorough exact-head/base ACCEPT and all gates (O-040). Grok supplies a separate bounded review and cannot operate GitHub; neither Claude nor Codex may review or merge #293.

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

- **Providers enabled:** Grok (xAI, bounded independent full-diff review for #293), Codex (material contributor, ineligible to review or merge #293), Perplexity (public research), Antigravity and Copilot CLI (producers from M1; Copilot out of monthly quota as of 2026-09-23), Cursor (independent final reviewer and eligible merger for #293), Firecrawl (retrieval). Z.ai excluded (trial lane).
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

- **Current state:** analysis_only
- **Blocking fields:** M0 is closed but #293 record reconciliation remains pending. D-018 and D-019 to D-022 are ratified documented exceptions, preserving crossings and spend. Set B batch 1 has an authenticated ACCEPT for all 25 frozen items (xai/20260926T225645Z-268799-d4c6.json, response 473bbadc-7d1a-93af-9b73-6299017e9b97, file SHA-256 101c5f2d59b4b94ad0d9f581fc51793af0a07a35123a265cba1394c16d53c663, sent-payload SHA-256 b248ce563e9e9c0e5e820080baa6ecdf22c415ac05e3989525c37c290efa81bb, USD 0.15078); batch 2 has a separate ACCEPT. The original pr-45 constraint omission is retained as a known MINOR reference imperfection and must be disclosed with Set B results. Set B scoring still requires the M1 pipeline gates. #293 still requires this bounded record correction, a local cross-file check, one independent exact-head/base full-diff Grok review capped at USD 0.30 under Ed's 2026-09-27 approval, all six current-head CI checks, and Cursor's own eligible exact-head review and merge. Claude and Codex contributed and cannot review or merge. Slack identities remain pending until M3; no runtime lane is deployed.
- **Next responsible party:** Codex Director maintains this record and coordinates review/CI. Grok supplies the bounded independent full-diff review. Cursor, with no counted contribution, performs its own thorough exact-head review and eligible guarded merge after all gates. Claude and Codex cannot review or merge #293.
- **Next bounded action:** Preserve the prior unauthenticated batch-1 receipts as historical evidence. The later authenticated ACCEPT is xai/20260926T225645Z-268799-d4c6.json, response ID 473bbadc-7d1a-93af-9b73-6299017e9b97, frozen file SHA-256 101c5f2d59b4b94ad0d9f581fc51793af0a07a35123a265cba1394c16d53c663, full sent-payload SHA-256 b248ce563e9e9c0e5e820080baa6ecdf22c415ac05e3989525c37c290efa81bb, USD 0.15078. Batch 2 has separate ACCEPT. Retain pr-45 as a known MINOR imperfection and disclose it in Set B results. Under Ed's 2026-09-27 D-022 disposition, complete one local cross-file check, one exact-head/base Grok review capped at USD 0.30, six CI checks, and Cursor's own eligible review before merge. #294 remains approved but unmerged; this record grants no M1 dispatch authority. O-034 downloads remain verified.
- **Expected evidence:** Local cross-file check; Set B receipts including xai/20260926T225645Z-268799-d4c6.json and batch-2 ACCEPT; disclosed pr-45 caveat; one independent full-diff Grok review on current head/base within USD 0.30; all six CI checks green; Cursor's own current-head review, no counted contribution, no conflicts or draft state, and an expected-head guarded merge. No contributor merge or gate waiver.
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:31 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-26 | 00:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:33 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:44 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:00 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:11 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:28 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:44 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:30 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 00:48 UTC | END]] -->
