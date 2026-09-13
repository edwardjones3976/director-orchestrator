<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:50 UTC | BEGIN]] -->
# Current project leadership

Claude is the acting Director-Orchestrator for this project. Codex, ChatGPT,
and OpenAI provide bounded capability-routed engineering and eligible
independent review work previously assigned to Claude. This decision applies to new project
coordination immediately; existing authorship and execution evidence remain
attributed to the provider that actually produced them.

The owner's instruction was: "Make it so that, throughout the project, Claude
is now the acting director, and Codex/ChatGPT switches everything with Claude,
and vice versa." The real native Claude provider accepted the handoff on
2026-09-08 UTC and issued the implementation directive. The native response
identified Claude Opus 5; this was an actual provider response, not a predicted
position or a renamed Codex task. Coordination creates no code authorship or
independent candidate acceptance.

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

## Responsibilities

| Responsibility | Current owner |
| --- | --- |
| Project priorities, scope, sequence, and design decisions | Claude |
| Work decomposition, allocation, bounded dispatch, and overlap prevention | Claude |
| Review orchestration, evidence reconciliation, and delivery reporting | Claude |
| Bounded implementation, debugging, testing, and verification | Codex/ChatGPT and other capable assigned providers |
| Independent review | An eligible provider that did not materially author the candidate |
| Owner-reserved decisions | The owner, under the existing deterministic gates |

Claude delegates substantial implementation work. Codex may act within an
assigned engineering scope and coordinate its implementation subtasks; it
does not silently reclaim project priorities or issue instructions as Claude.
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

Actual assignment history is unchanged. The rolling twenty-assignment window,
normal tolerance of three percentage points, and hard ceiling of target plus
five percentage points still apply. Pure coordination creates no production
credit. OpenAI Pro 20x and Claude Max 5x remain account capacity facts for their
actual providers, not exchanged subscriptions or API entitlements.

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

Fresh project decisions supersede historical prose describing OpenAI as the
project lead. Historical documents remain historical; current role and
allocation sources must agree with this decision.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 05:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | END]] -->
