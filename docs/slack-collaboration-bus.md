# Slack collaboration bus

Slack is the Director's primary human-visible coordination room. Each live task gets one
Director-owned thread in its configured private project channel. The thread is not only a
debate surface: it carries bounded delegation, work/research status, accepted evidence,
review handoffs, disagreement, synthesis, owner questions, and completion.

## Runtime contract

1. OpenAI posts the objective, provider roles, hop/revision/budget limits, and debate limits.
2. Before each paid provider call, OpenAI posts a correlated delegation with the assignment ID.
3. Providers work through authenticated adapters. They do not post directly or converse forever.
4. OpenAI accepts or rejects the result against the stored checks, then relays only a concise,
   structured summary. Raw provider output, credentials, secret-shaped values, and raw private
   source are excluded from the Slack relay.
5. When a debate is planned, participants first work independently. One rebuttal round may see
   only declared dependency outcomes. Perplexity may supply approved public evidence but cannot
   debate, vote, write code, review a repository, or request a merge.
6. OpenAI synthesizes the evidence within the plan's explicit synthesis-cost estimate and records
   dissent. Deterministic code applies the fixed
   weights: Antigravity 1, Cursor 1.5, OpenAI 2, Claude 2.5. A producer's advisory vote counts;
   that producer still cannot independently accept its own work.
7. Ordinary ties receive a documented OpenAI evidence judgment. A material tie stops with one
   focused owner question.
8. Accepted implementation artifacts continue through the existing different-provider
   exact-SHA review, Codex Security, trusted CI, and deterministic merge gates.

Every event contains the task/assignment identity, provider or Director identity, phase,
timestamp, and correlation/run ID. Events and Slack timestamps persist in SQLite. Stable Slack
client message IDs make a restart retry idempotent without repeating a paid provider dispatch.

Each Slack delivery receives at most three attempts, all with the same stable client message ID.
If all three fail, the task records the failure and permanently switches to the prior durable
communication path: SQLite/API task state and provider-native run/session evidence. The Director
continues from persisted state; it does not repeat paid
provider work, OpenAI acceptance, or an already-recorded assignment outcome merely to recreate a
Slack message.

## Authority and cost limits

Slack messages and provider text are untrusted coordination evidence. They cannot change a plan,
approve an assignment, widen access, spend beyond the task budget, merge, deploy, publish, or
override a serious-safety stop. Those actions require the stored owner authority and every
applicable deterministic gate.

Communication must be concise, accurate, and useful. The Director sends minimum necessary
context, reuses fresh evidence, allows at most one rebuttal round, caps provider nonresponse at
three persisted safe attempts, and records unavailable providers rather than creating expensive
conversation loops.
