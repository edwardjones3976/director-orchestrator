# Staff register, contacts and nightly briefings

Owner direction: 2026-09-30. This is the team's daily coordination procedure.
The objective remains a correct, evidenced result at reasonable, non-wasteful
cost. A status update is not a reason to launch another model or repeat QA.

## Provider roster

Shared reference for owners and all collaborating providers, updated 2026-10-02.
The roster has **11 provider/contact entries across nine grouped providers or
services**. Routes listed here are documented contact options, not a live
availability check or proof of runtime admission.

| # | Provider or contact | Documented contact options | Profile or guidance |
| --- | --- | --- | --- |
| 1 | OpenAI — ChatGPT / Codex | ChatGPT sessions, local Codex CLI, Codex Cloud and OpenAI API | [OpenAI profile](provider-profiles/openai.profile.md) |
| 2 | Anthropic — Claude | Existing Claude session and first-party Claude Code CLI | [Claude profile](provider-profiles/claude.profile.md) |
| 3 | Cursor | Local Cursor Agent CLI and separate Cloud Agents | [Cursor profile](provider-profiles/cursor.profile.md) |
| 4 | Google Gemini / Antigravity | Antigravity application and `agy` CLI | [Antigravity profile](provider-profiles/antigravity.profile.md) |
| 5 | xAI — Direct Grok | Responses API/MCP and Grok Build CLI | [Grok profile](provider-profiles/grok.profile.md) |
| 6 | Grok Bot | Separate owner-authorized GUI contact | [Contact procedures](#staff-sheet-and-contact-directory) |
| 7 | Perplexity | Public research through MCP/API search and ask | [Perplexity profile](provider-profiles/perplexity.profile.md) |
| 8 | Firecrawl | Public web research/retrieval through CLI or connector | [Firecrawl profile](provider-profiles/firecrawl.profile.md) |
| 9 | GitHub Copilot | PowerShell `copilot`, `copilot --resume` and headless CLI | [Copilot profile](provider-profiles/github-copilot.profile.md) |
| 10 | VS Code Coding Bot | Installed editor's Copilot Chat GUI contact | [VS Code contact methods](providers/vscode-contact-methods.md) |
| 11 | Z.ai — GLM | Provider-dispatch and isolated CLI transport | [Z.ai profile](provider-profiles/zai.profile.md) |

Grok Bot is a separate Grok contact route. VS Code Coding Bot is a Copilot
contact, not proof of a separately enrolled provider. Firecrawl provides
retrieval tooling rather than a standalone reasoning model. A route or product
name does not establish the actual served model or backend provider family.

Internal OpenAI teammates include the linked Director and Astra Light sessions,
the cloud ChatGPT teammate (owner-selected Astra High), and the separately
registered Codex Cloud Worker. Codex Security is an OpenAI tool specialization.
These are not additional provider families. Director is a role, not another
provider or bot; removed Director-branded bots are not roster members.

To use the roster, choose the relevant profile and existing contact, confirm
current availability and assignment authority, then follow the contact
procedures below. Preserve the owner preference for external-provider work,
with internal chats providing useful support. Use the
[runtime profiles and route catalog](provider-profiles/README.md) for runtime
contracts; this contact list does not change allocation, budget, access or
review eligibility. Keep secrets and private contact details out of this guide.

## Leadership and succession

| Order | Provider | Responsibility |
| --- | --- | --- |
| 1 | ChatGPT / Codex | Director and site foreman; owns outcomes, priorities, routing, evidence and continuity. |
| 2 | Claude | Deputy; performs the Director's duties while Codex is unavailable. |
| 3 | Cursor | Official third in command; performs **all Director duties when both Codex and Claude are unavailable**. |

All Director duties include Consultant, Investigator, Foreman and Quality Lead,
plus the authorized debate functions. Leadership does not erase a provider's
identity, change advisory weights, grant credentials or budget, or allow an
author to accept or merge its own work. Existing project-specific appointments
remain in force. This is current operating guidance, not evidence of a deployed
runtime-provider change.

Exactly one lead directs shared work. A deputy records the takeover, why the
higher-priority leaders are unavailable, open writer claims, active sessions
and uncertain external actions in the existing coordination record. Do not
infer unavailability from silence or old quota data. Returning leaders read
the handoff and agree the transfer before resuming. In-flight writers and the
one eligible final reviewer remain intact.

## Staff sheet and contact directory

The owner-facing `DIRECTOR-STAFF-AND-ASSIGNMENTS.xlsx` is in the local
`%USERPROFILE%/.codex/artifacts/team-operations-20260930/` handoff directory,
alongside contact/checklist CSV exports, the source register and validation
evidence. This private workbook contains verified local contact locations;
do not commit personal filesystem paths or credential values to the repo.

The workbook has Staff, Contacts, Assignments, Repairs, Completed, Daily Checklist,
Nightly Briefing, Next 7 and Sources tabs, with a Start Here legend. Maintain the
same workbook, preserve user edits, and append completed work. The initial build
script is not an unattended overwrite job. GitHub issues/PRs and exact provider
receipts remain the evidence of record.

Each staff row records stable identity/provider family, role, primary/fallback
contact, model evidence, work location, availability evidence/time, current and
upcoming work, completed evidence and pending repairs. Installation is not live
readiness. Never invent personal contact information, Slack IDs or served models.

| Member or lane | Contact / location | Daily responsibility |
| --- | --- | --- |
| Codex Director | Originating Director chat; native Codex CLI and isolated worktree | Reconcile assignments, unblock delivery, publish briefing. |
| Claude deputy | Verified existing Claude session; otherwise first-party Claude CLI via its skill | Return release/evidence status and unresolved decisions; cover #1 absence. |
| Cursor third in command | Local `cursor-agent` via its skill; separate from Cursor Cloud Agents | Return review/continuity status; cover only when #1 and #2 are unavailable. |
| Astra Light partner | Linked local orchestration chat | Focused assistance and worktree/evidence handoffs. |
| Cloud Astra teammate | Confirm an active reply-capable chat; a share URL is reference only | Assigned analysis and continuity; no assumed local filesystem access. |
| Direct Grok | Existing xAI/Grok provider-dispatch route | Assigned reasoning, implementation or eligible review. |
| Grok Bot | Verified owner-authorized GUI session | Separate contact lane; do not infer enrollment from Direct Grok. |
| Gemini / Antigravity | `agy` and a named isolated worktree | Assigned implementation/verification; route repair #316. |
| Perplexity | Existing public-research search/ask route | Cited external context for technical questions. |
| Firecrawl | Connected search/scrape tools or installed CLI | Source retrieval with URLs and retrieval evidence. |
| GitHub Copilot | `copilot`, or resume a verified session | Assigned coding/review; record actual served provider family. |
| VS Code Coding Bot | Installed editor's Copilot Chat and verified workspace | Assigned editor work; distinguish MCP registration from admission. |
| Z.ai | Existing `zai ask` / `zai run` route | Scoped production work; reconcile planner gap #347. |
| Codex Security | Security tools and immutable candidate artifacts | Report coverage and findings; partial scans are not success. |
| Owner | This Director chat and established project channels | Receive briefings; decide genuinely owner-reserved matters. |

Reuse `%USERPROFILE%/.claude/skills/provider-dispatch/dispatch.py` and the
provider-specific skills under `%USERPROFILE%/.codex/skills/`. Read the selected
skill before dispatch. Use **ask** for read-only questions/reviews and **default
mode** for authorized execution; **never provider plan mode**. Where an old
wrapper defaults to plan, select a supported explicit ask or no-plan/default
route while retaining the assignment's boundaries.

Detailed work belongs in the existing authorized PR/issue or originating
provider chat. [Issue #299](https://github.com/edwardjones3976/director-orchestrator/issues/299)
is the shared coordination record. Slack gets sanitized summaries through the
authorized relay. Tag mentioned providers with verified IDs; never invent an ID
or trigger a task merely for status. Where passive mentions are unverified, use
existing sessions/receipts and the shared coordination record.

Technical/development questions between providers first use Perplexity or
Firecrawl, with `RESEARCH:` queries and sources. Answer process/coordination
questions directly. Return every answer, clarification or explicit blocker
through its originating channel; delegation retains accountability.

## Daily assignment checklist

At the start of an active work period, reconcile the board, open PRs, sessions
and fresh evidence. Each assignment records:

| Field | Required meaning |
| --- | --- |
| Task and source | Existing issue/PR or stable local reference. |
| Coordinator and writer | Both explicit; coordination is not proof that implementation started. |
| Reviewer | One eligible non-author reviewer at the exact candidate. |
| Work location and scope | Named worktree/branch and owned paths; no competing writer. |
| State | Upcoming, in progress, blocked, awaiting review, completed, or UNKNOWN. |
| Deadline / next checkpoint | Pacific date/time; distinguish reporting targets from completion commitments. |
| Next action / acceptance | Concrete result, dependencies and evidence needed to close it. |
| Repairs | Severity, affected provider, evidence, repair owner and next update. |
| Last update / contact | Timestamp and originating channel. |

- [ ] Reconcile actual state before assigning or retrying work.
- [ ] Confirm one writer, one eligible reviewer and shared-resource order.
- [ ] Record upcoming work and its next checkpoint; do not invent finish dates.
- [ ] Return evidence and distinguish implemented, tested, reviewed, merged,
      deployed and live-verified.
- [ ] By **22:45 Pacific**, provide completed/unfinished work, repairs, blockers
      and the next step. Idle providers need no new model call; the acting lead
      may collect existing records.
- [ ] Carry unresolved work forward with its owner and next action.

22:45 and the recommended 23:10 acknowledgement checkpoint are foreman targets.
The owner set the briefing itself to 23:00. Missing inputs remain unavailable
or UNKNOWN; the acting lead still publishes the briefing.

## Nightly briefing: 23:00 America/Los_Angeles

Use the Pacific calendar day, including daylight saving changes. One scheduled
briefing in the Director chat produces one dated file under the handoff
directory's `briefings/` folder. Reuse the workbook, GitHub records and receipts;
do not start reviews, replay paid requests, merge or deploy merely to complete
the briefing. Separate actual spend from unresolved holds. Deliver the recap
every night, including quiet days.

The Codex heartbeat is the configured scheduler and requires the local host/app
and account. It is not an independently installed Claude or Cursor scheduler.
An available appointed deputy uses this same procedure when covering the
Director and records its takeover to avoid duplicate reports.

### Briefing template

```text
Date/time (Pacific):
Acting lead / succession reason if applicable:
Source refresh time / unavailable sources:

1. Completed today
   Task | provider | actual completion stage | evidence | caveat

2. Current projects and unresolved work
   Task/project | accountable owner | writer/worktree | state | next checkpoint
   Repair/blocker | responsible provider | corrective next action | evidence
   Owner-reserved decision, if any | exact request and reason
   Active sessions / uncertain effects that must not be replayed

3. Next seven actions
   1 through 7: action | one accountable owner | deliverable | deadline/checkpoint
   | dependencies | acceptance evidence

Delivery/readback
   Briefing link | recipients reached | acknowledged / not reached / UNKNOWN
```

At the recommended 23:10 checkpoint, record real acknowledgements from active
leaders and any unavailable recipient. Delivery is not acknowledgement. Use
the same dated record for corrections; avoid competing versions or duplicate
provider launches. The next active lead starts with these seven actions and
reconciles changes once.
