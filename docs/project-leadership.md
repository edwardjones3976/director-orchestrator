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
Firecrawl keep explicit public-research lane descriptions. Other lanes do not
carry standing job assignments. This decision applies to new project
coordination immediately; existing authorship and execution evidence remain
attributed to the provider that actually produced them.

Historical coordination decisions remain recorded as evidence only. The current
owner objective is immediate ChatGPT/Codex Director authority for priorities,
decomposition, routing, review orchestration, completion reporting, and owner
escalation. This policy update does not claim that a deployed runtime provider
changed in production.

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

## Runtime coordination status and responsibilities

| Responsibility | Current owner |
| --- | --- |
| Project priorities, scope, sequence, and design decisions | ChatGPT/Codex Director lane |
| Work decomposition, allocation, bounded dispatch, and overlap prevention | ChatGPT/Codex Director lane |
| Review orchestration, evidence reconciliation, and delivery reporting | ChatGPT/Codex Director lane |
| Bounded implementation, debugging, testing, and verification | Any capable assigned non-exempt lane under bounded authority |
| Independent review | An eligible provider that did not materially author the candidate |
| Owner-reserved decisions | The owner, under the existing deterministic gates |

The Director delegates substantial implementation work. OpenAI/Codex may also
act within a separately assigned engineering scope, but coordination duties do
not imply material authorship or independent acceptance authority.
Other providers keep their identities and verified capabilities. Non-exempt
providers do not carry standing jobs; assignments are capability- and
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
