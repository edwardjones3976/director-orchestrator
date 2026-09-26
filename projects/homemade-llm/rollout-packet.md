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
  - **Allowed:** Independent reviews through the owner-approved provider routes, including the set B v2 review with private values redacted and one bounded batch 1 Grok rerun (O-040; completed ACCEPT, 24 OK and pr-45 MINOR; receipt xai/20260926T215607Z-666045-34b3.json; owner authentication and fix decisions pending; Set B excluded from M1 scoring) [verified 2026-09-26 21:56 UTC, receipt]
  - Any new external effect requires a profile revision first (`fallbacks.changed_scope: reshape_profile_before_dependent_work`).
  - **Prohibited:** Any public exposure of Open WebUI or LM Studio (Tailscale serve only, never Funnel)
- **Paid execution:** owner-authorized and gated — USD 25 per milestone ledger; a call that would breach it stops
- **Automerge/deployment/publication:** disabled; #293's non-author reviewer giving final ACCEPT merges after all gates (O-040); neither Claude nor Codex may merge

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

- **Providers enabled:** Grok (xAI, the only eligible #293 reviewer; if unavailable, review waits), Codex (material contributor, ineligible to review #293), Perplexity (public research), Antigravity and Copilot CLI (producers from M1; Copilot out of monthly quota as of 2026-09-23), Cursor (PR bots), Firecrawl (retrieval). Z.ai excluded (trial lane).
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
- **Blocking fields:** owner disposition of the completed Set B batch 1 rerun (ACCEPT, 24 OK and pr-45 MINOR; receipt xai/20260926T215607Z-666045-34b3.json omits the provider response ID; file SHA-256 is only in purpose) [verified 2026-09-26 21:56 UTC, receipt], including receipt authentication and whether to fix pr-45; Set B remains excluded from M1 scoring; one independent full-diff Grok review under owner disposition 5850566834, capped at USD 0.30, after a local cross-file consistency check bound to #293 exact head and base; all six CI checks green at that head. D-019 to D-022 are ratified documented exceptions (O-040/O-041), with crossings, evidence and spend preserved. Every Slack identity is pending by design until M3; no runtime lane is deployed.
- **Next responsible party:** Director records the owner decisions on Set B receipt authentication and pr-45 and coordinates review/CI; Grok (xAI) alone is eligible for #293's final review and merges after ACCEPT and all gates. If unavailable, review waits; neither contributor (Claude or Codex) may review or merge.
- **Next bounded action:** The owner-approved Set B batch 1 rerun completed: ACCEPT batch 1 (25 items), 24 OK and one MINOR (pr-45: constraints omit eligibility and evaluation from the body's Claude-default routing exclusion list). Receipt xai/20260926T215607Z-666045-34b3.json has a unique filename, grok-4.6, HTTP 200, status completed, and USD 0.1406. Its purpose carries frozen packet SHA-256 101c5f2d59b4b94ad0d9f581fc51793af0a07a35123a265cba1394c16d53c663; before the call the Director confirmed all 25 embedded packets byte-identical to the pinned pr-reconstructed-v2 files (manifest 38a93d79…69bc). The wrapper omits the owner-required provider response ID; packet_sha256 is a 16-hex digest of the full sent payload including the preamble, and the owner-required file SHA-256 appears only in purpose. These facts were read by the Director [verified 2026-09-26 21:56 UTC, receipt]. Obtain the owner decision on accepting the completed batch 1 receipt as authentication or requiring a rerun after the wrapper records provider response IDs, and on whether to fix pr-45 (changing the pinned manifest). Set B stays out of M1 scoring pending those decisions; Set A's authenticated ACCEPT remains valid under O-040 scoring rules. MX-011 is committed at `da214e27754f2b7513ac844b0d5c5bec38d8e3a6`; earlier bounded review rounds are completed history. Owner disposition 5850566834 authorizes this record-consistency pass, a local cross-file consistency check, then one independent exact-head/base full-diff Grok review capped at USD 0.30 and all six CI checks. A new substantive finding stops for owner disposition. D-018 is ratified by O-037 in the approved but unmerged M1 plan PR #294 (owner disposition 5850566834); the historical crossing and cost remain recorded. Future M1 dispatches require approved packets, leases, allocation and gates; M1 plan approval is separate (O-038, approved but unmerged PR #294); this record grants no dispatch authority. O-034 downloads remain done and verified.
- **Expected evidence:** Owner disposition accepting the batch 1 receipt as authentication or requiring a rerun with provider response IDs, plus disposition of pr-45 MINOR, the single non-author full-diff review authorized by disposition 5850566834 at exact head and base, and six required CI checks green at that head. Owner law (2026-09-26 ~21:53 UTC, O-040): "Any provider that has reviewed the work thoroughly can merge it. The owner/sole-author (other ME), can never merge their own work." For #293, the non-author reviewer giving final ACCEPT merges after the gates; no contributor merges. These dispositions merge no PR and waive neither review nor CI.
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
