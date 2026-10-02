<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-23 | 01:32 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-23 | 00:02 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 21:21 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 20:59 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:50 UTC | BEGIN]] -->
# Current project leadership


The project uses capability-based routing with assignment-scoped authority.
ChatGPT/Codex keeps the explicit Director lane description; Perplexity and
Firecrawl keep explicit public-research lane descriptions. Outside the deputy
appointments below, other lanes do not carry standing implementation
assignments. This decision applies to new project
coordination immediately; existing authorship and execution evidence remain
attributed to the provider that actually produced them.

Historical coordination decisions remain recorded as evidence only. The current
owner objective is immediate ChatGPT/Codex Director authority for priorities,
decomposition, routing, review orchestration, completion reporting, and owner
escalation. This policy update does not claim that a deployed runtime provider
changed in production.

## Administrators and chain of command

Owner clarification, 2026-10-02: **the three provider administrators are
ChatGPT/Codex, Claude and Cursor. Cursor is an administrator, not a non-admin
provider.** The owner sits above the administrator team.

| Order | Position | Authority and responsibility |
| --- | --- | --- |
| Owner | Overall authority | Approves overall/macro plans and changes to owner-reserved scope or limits; appoints the administrators and project Directors. |
| 1 | ChatGPT/Codex — overall Director and administrator | Owns overall outcomes and coordination; may overrule and delegate to Claude and Cursor within owner direction. |
| 2 | Claude — second-in-command and administrator | May overrule and delegate to Cursor; carries all Director duties when Codex is unavailable, and leads explicitly appointed projects. |
| 3 | Cursor — third-in-command and administrator | Performs delegated administrative and project work; carries all Director duties when both Codex and Claude are unavailable. |
| Assigned providers | Non-administrator specialists and workers | Execute, research, test or independently review their assigned work and return evidence to its responsible administrator. |

Administrators make individual assignment and subordinate plan decisions within
the owner's approved macro plan and existing delegation. They keep one accountable
lead for each scope, answer provider questions, assign work once, reconcile
handoffs, and arrange QA and independent review. Technical questions still require
Perplexity or Firecrawl research before an answer. Leadership and approval routing
do not grant operating-system elevation or bypass deterministic policy, security,
budget, exact-head review or merge gates.

"Non-admin" means outside these three appointed administrator positions; it
does not mean "not currently acting as Director". Grok and Z.ai are non-admin
providers; Perplexity and Firecrawl are non-admin research providers. Separately
assigned workers, including Astra Light, Codex Cloud Worker and Copilot, retain
their own identities and assignments rather than gaining an administrator seat
from a provider-family name. Project participation and provider availability are
separate from administrator status.

### Current Director-Orchestrator completion appointment

The owner appointed **Claude as Director for completing Director-Orchestrator**
through 20/20 plus issue #81. Claude directs that completion plan, assignments,
sequence and integration. Codex remains overall Director and supports Claude's
completion work; Codex and its assigned helpers do not run a competing completion
plan. Cursor remains administrator #3 and accepts delegated work in that plan.
The general hierarchy remains in force; overrides are communicated through Claude
so the team still has one completion lead. This assignment excludes Homemade LLM
and every other project; their appointments are unchanged.

This specific appointment governs completion even while Codex is available and
supersedes general deputy-only or no-standing-assignment wording for that scope.
The [completion appointment record](https://github.com/edwardjones3976/director-orchestrator/issues/299#issuecomment-5957464094)
and the owner's subsequent direct clarification establish this arrangement.

## Owner-appointed succession and daily briefing

Owner direction, 2026-09-30: **Codex is #1, Claude is #2, and Cursor is the
official #3.** Claude carries the Director's duties while Codex is unavailable;
Cursor carries **all Director duties when both Codex and Claude are unavailable**.
This includes Consultant, Investigator, Foreman and Quality Lead responsibilities,
and the Director's authorized debate functions. The active provider keeps its
own identity and advisory vote. These scoped appointments supersede general
no-standing-assignment wording for leadership only and remain effective until
the owner changes them. Explicit project-specific appointments remain intact.

Keep exactly one acting lead. Record a takeover and known availability evidence
in the existing coordination record before directing shared work; reconcile
active sessions and uncertain effects instead of replaying them. Returning
leaders read the handoff and agree the transfer before resuming. An old quota
report or a quiet session alone is not proof of unavailability.

The three leaders use one [staff register and briefing procedure](team-staff-and-briefings.md).
Each night at **23:00 America/Los_Angeles**, the active lead reports completed
work, ongoing work and unresolved issues, and exactly seven next actions with
owners and deadlines or dependency checkpoints. The foreman's input checkpoint
is 22:45. Succession grants no extra credentials or budget and changes no review
independence, policy gate, or deployed provider selector.

The owner-visible handoff is recorded in Codex task
`01a077dc-2e5c-7652-9ae7-1e5aa0e692e5`, evidence set
`claude-role-transition`, native session
`a2b16990-30d6-4470-b702-51ec0eedcee7`. Its local `receipt.json` SHA256 is
`bfb2dd0fb4c5822b17df44c41a2a6467bcab2edb2b33631642e63222f32392c9`;
the captured `stdout.jsonl` SHA256 is
`080f619365f9b0f6620d0608fec1bda85e20feebf3e5ef73e7315604fd2aaa55`.
The receipt records one completed native turn with the existing login, no
observed tool use, and completed process cleanup. These are referenceable
host observation artifacts for the coordination handoff, not a signed
provider-v2 acceptance receipt or authority to activate a runtime.

## INVESTIGATOR responsibility

OpenAI/Director members also act as **INVESTIGATOR** under the
[canonical charter](team-operating-charter.md#openaidirector--investigator): identify and document
issues and suggested solutions, then hand them off through the acting Director's
task thread or an existing PR/issue, when the assignment authorizes communication.
Do not directly fix an investigated issue unless separately assigned that fix,
and do not take over the ongoing assignment. Record disposition with the finding;
The Director relays sanitized summaries only to Slack; detailed findings and disposition stay in the existing authorized PR/issue.
If none exists, retain details locally and request an authorized destination;
never post them to Slack.

## Director maintenance routing

Every Director (the ChatGPT/Codex Director, Claude as second in command, Cursor
as third in command when both are unavailable, and any
project Director) sends maintenance, meaning review-finding fixes, record and
document inconsistencies, CI fixes, and provider connection or plumbing
problems, to OpenAI Codex, handed over through the Codex CLI (`codex exec` with a
written packet) or through the ChatGPT/Codex Director thread, with no limit on
either channel. The Director still verifies, pushes,
and replies on the review threads. Codex's edits are maintenance assistance and
do not change authorship: the PR's sole author is whoever wrote at least 52% of
it (the sole-author rule, PR #284), and only that author's provider family is
excluded from review. This applies to all
maintenance from 2026-09-26, including work in flight; it adds no authority and
removes no gate. The canonical rule is the charter's
[Director maintenance routing](team-operating-charter.md#director-maintenance-routing) section.

## Runtime coordination status and responsibilities

| Responsibility | Current owner |
| --- | --- |
| Director-Orchestrator completion priorities, scope, sequence, and design within the approved macro plan | Claude, completion Director; Codex retains overall accountability |
| Completion work decomposition, allocation, dispatch, and overlap prevention | Claude and explicitly assigned administrators/helpers |
| Completion review orchestration, evidence reconciliation, and delivery reporting | Claude with assigned Codex/Cursor support; one eligible final reviewer per candidate |
| Bounded implementation, debugging, testing, and verification | Any capable assigned non-exempt lane under bounded authority; maintenance goes to OpenAI Codex (see Director maintenance routing) |
| Independent review | An eligible provider that did not materially author the candidate |
| Owner-reserved decisions | The owner, under the existing deterministic gates |

The Director delegates substantial implementation work and routes maintenance
to OpenAI Codex as described above. OpenAI/Codex may also
act within a separately assigned engineering scope, but coordination duties do
not imply material authorship or independent acceptance authority.
Other providers keep their identities and verified capabilities. Outside the
scoped leadership succession, specialists do not carry standing implementation
jobs; assignments are capability- and
authority-scoped for each candidate. Codex
Security remains the named security product and gate.

All provider-led activities keep one active writer per file, isolated
worktrees, exact candidate identity, current CI and security evidence, bounded
attempts, and the ban on accepting or merging one's own material work.
Historical approvals never transfer to a new candidate or a different author.

## Allocation

The prospective OpenAI and Claude duty allocations exchange places in the
non-engineering profiles. The owner's subsequent explicit engineering decision
sets OpenAI to 40% and Claude to 25%; the other lanes keep their existing targets.
On 2026-09-27 the owner enrolled Z.ai (GLM) at 18% in every profile and the other
nonzero lanes were reduced proportionally (engineering: OpenAI 33%, Claude 21%);
see `docs/provider-allocation-profiles.md`. Enrollment grants Z.ai no standing role.
Claude's advisory vote is 2.0 and OpenAI's is 2.5, with total
committee weight unchanged. The canonical numbers are in
`src/director_orchestrator/allocation_profiles.py` and `team_charter.py`.

Actual assignment history is unchanged. The rolling twenty-assignment window and
normal tolerance of three percentage points still apply as an advisory routing
preference; the owner retired the hard ceiling of target plus five percentage
points on 2026-09-21, and a zero target remains ineligible regardless of
history. Pure coordination creates no production credit. OpenAI Pro 20x and
Claude Max 5x remain account capacity facts for their actual providers, not
exchanged subscriptions or API entitlements.

## Runtime and historical compatibility

Project leadership and the executing Director provider are recorded separately.
The leadership handoff alone does not replace a deployed model, launch a native
process, or enable paid execution. The legacy OpenAI implementation remains an
explicit runtime compatibility path while the Claude adapter is disabled.
Selecting Claude with an unavailable transport must stop; it must never call
OpenAI and label the resulting evidence Claude.

New plan generations bind their executing provider, model, and transport.
Historical OpenAI records retain their original meaning. The persisted
`openai_acceptance_revision` node name is an opaque compatibility identifier;
it does not authenticate the provider used by an injected acceptance handler.
No checkpoint migration or historical receipt rewrite is part of this change.

The existing Slack and n8n integration remains scoped to Director-Orchestrator.
Sibling projects, their channels, credentials, ports, and services are unchanged.
Source changes, their review, deployment, and real execution must be reported
as distinct states. Do not describe a role-change branch as a live cutover.

## Handoffs

Start Claude sessions in the explicit Director-Orchestrator repository or named
worktree so they load `CLAUDE.md` and `AGENTS.md`. Supply the latest exact head,
active writers, unresolved decisions, and relevant evidence once. Do not
replay an ambiguous dispatch or revive an unavailable route to deliver the
same handoff again. A Codex task cannot become Claude by changing its title.

The immediate ChatGPT/Codex appointment supersedes historical prose assigning
project leadership to Claude. Historical documents remain historical; current role and
allocation sources must agree with this decision.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | END]] -->


Runtime note: some deployments may still report pre-cutover coordinator identifiers.
That status is observational only and does not create standing role authority.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 20:59 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-22 | 21:21 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-23 | 00:02 UTC | END]] -->
<!-- [[Codex | GPT-6 Astra | 2026-09-23 | 01:32 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]] -->
