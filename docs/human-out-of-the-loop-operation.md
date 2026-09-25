<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:55 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:07 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:17 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:14 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 09:00 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:15 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:01 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:52 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:27 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 17:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 03:44 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | BEGIN]] -->
# Human-out-of-the-loop operation

**Status:** Documented authority only. Default off. No delegated decision is
executable until the implementation phases below are separately built, gated,
and accepted by the owner. The presence of this document grants nothing.

**Schema authority:** `director-delegated-authority/v1`

**Frozen starting point:** `70217f258430843b936df7c13030aff957cfb782`

## Purpose

The Director stops in `OWNER_DECISION_REQUIRED` whenever an action touches
merge, deployment, publication, deletion, purchases, external representation,
sensitive data, credentials, or expanded access. That is correct as a default
and stays the default. It also means that when the owner is away, work halts
even where every deterministic gate has already passed and independent review
is available.

Human-out-of-the-loop operation is the bounded, revocable delegation that lets
an enumerated class of those decisions be resolved without the owner present.

It is defined by one rule, and everything else in this document follows from it:

> Delegation changes **who supplies a verdict**.
> It never changes **whether a gate runs**.

No deterministic gate is removed, relaxed, skipped, or made advisory by any
grant described here. A delegated decision must clear strictly more evidence
than an attended one, because the owner's judgment is no longer present to
catch what the gates do not model.

The one pre-execution class, `execute_small_read_only_task`, is stated here as
an explicit carve-out rather than left to be read as a relaxation. It resolves
no decision about a candidate, because none exists yet. The attended act it
replaces is the per-task execution button, which reads no evidence at all; the
grant replaces that click with the pre-execution bar in
[Standing grant for small read-only tasks](#standing-grant-for-small-read-only-tasks),
and every gate the attended task passes afterwards still runs unchanged. That
is how this class also clears strictly more evidence than the attended act. The
carve-out is itself a tier-0 change and holds only with the owner's explicit
sign-off.

## Operating states

Exactly one state is in force per deployment profile at any time.

| State | Meaning |
| --- | --- |
| `ATTENDED` | Default. Every `OWNER_DECISION_REQUIRED` waits for the owner. No delegation exists. |
| `DELEGATED` | A live grant may resolve an enumerated tier-1 decision class when the full evidence bar holds, or start the one pre-execution class when its pre-execution bar holds. Everything else still waits. |
| `SUSPENDED` | Entered automatically on any trip condition. Behaves exactly as `ATTENDED` until the owner clears it explicitly. |

A deployment starts in `ATTENDED` and returns to it whenever configuration is
absent, invalid, expired, or unreadable. There is no path from `SUSPENDED` to
`DELEGATED` that does not pass through an explicit owner action, except the
pre-execution class's process-level ledger hold described next, which is not
recorded as `SUSPENDED`.

For the pre-execution class, `SUSPENDED` is recorded per grant: a durable
`delegation_trip` record holds that `grant_id` in every process until the owner
writes a new one. An absent, unreadable, or unwritable audit ledger holds the
process attended without such a record, because the ledger is what failed; a
restart re-arms the grant only if the ledger then reads, verifies, and writes
cleanly. Accepting that restart behaviour is part of the owner's sign-off on
the class.

## Decision classes

### Tier 1 - delegable, only under a live grant that names the class

- Executing an eligible merge of a pull request into a non-protected branch.
- Marking a pull request ready for review.
- Posting to the project's own designated Slack channel.
- Executing a small read-only task that `policy.small_read_only_eligible()` admits.

Tier 1 is exhaustive and every entry names a specific act. There is no
residual or catch-all delegable class, and none may be added by describing a
decision as routine: a category whose membership is a judgement call is not a
closed enumeration, and it would silently reopen the fail-closed rule below.

### Tier 0 - never delegable, under any configuration

- Production deployment, activation, or runtime cutover.
- Publication, external representation, or any message to a party outside the
  project's own designated channel.
- Deletion of data, force-push, or history rewrite.
- Purchases, billing changes, or raising a budget ceiling.
- Credential, secret, token, or access-scope changes.
- Merging into a protected branch.
- Editing this document, a grant, an allowlist, or any deterministic gate.
- Anything AGENTS.md rule 10 names that is not explicitly listed in tier 1.

Tier 0 is enumerated, and the enumeration fails closed: a decision that is not
plainly a listed tier-1 class is tier 0. A novel decision class is tier 0 until
the owner adds it deliberately, which is itself a tier-0 act.

## The evidence bar

A tier-1 decision may be resolved without the owner only when **every** item
below holds for the exact candidate commit. These are conjunctive. There is no
weighting, no majority, and no discretion.

1. Every required CI check is green at the exact candidate head.
2. Independent security evidence exists at that same head.
3. An independent exact-SHA review returns `ACCEPT` from a provider that is
   neither the producer nor a material author of the candidate.
4. That `ACCEPT` is the whole verdict requirement. Owner ruling 2026-09-12,
   verbatim: "it's a one-non-author-reviewer project-wide". A second verdict
   is never required and never substitutes for any other item.
5. The author and the reviewer are two distinct identities, and the reviewer
   that accepts is the identity that merges. Owner protocol, 2026-09-12, verbatim: "each PR should be authored by one person and reviewed by a single reviewer. The reviewer then either accepts and merges the PR or rejects it and returns it to the author with notes, including but not limited to reasons, suggestions, or coding feedback."
6. The change touches only paths inside the assignment's `owned_paths` and none
   of its `prohibited_paths`.
7. No tier-0 class is implicated, directly or as a consequence.
8. Budget, attempt, and hop ledgers are all within their configured bounds.
9. A grant is live, unexpired, unrevoked, and scoped to this repository, this
   branch, this decision class, and this assignment.
10. The audit record for the decision has been durably written before the
    decision is executed.

Any element that is missing, stale, ambiguous, unavailable, or unreadable sends
the decision to `OWNER_DECISION_REQUIRED`. **Absence of evidence is never
consent.** A provider that is out of quota, unauthenticated, or silent has not
approved anything, and its silence may not be counted, inferred, or waited out.

**The pre-execution carve-out.** The one pre-execution class is not resolved
against this bar at its decision point, because no candidate commit and no
verdict exist yet, so items 1 to 5 cannot hold there. It is resolved against the
pre-execution bar in
[Standing grant for small read-only tasks](#standing-grant-for-small-read-only-tasks),
which evaluates items 6 to 10 in their pre-execution form and never records
items 1 to 5 as passing. Items 1 to 5 are not waived: the task's own exact-SHA
review and CI/security gates must still hold, exactly as for an attended task,
and the class authorizes no merge, publication, or other effect. This is the
only carve-out.

## Why one independent verdict

A single agent verdict is a single point of failure, and this repository's own
record shows it is a fragile one. Historical delegation failures included
ambiguous results, authentication and usage limits, and CLI permission denials
misreported as provider outages; the limitations below preserve that correction.
The owner has ruled that one non-author `ACCEPT` is the bar, project-wide;
what this section protects is that the one verdict is genuinely independent.

The reviewer must be distinct from the producer and every material author.
A reviewer and a producer that share a backing
model are not distinct for this purpose, and neither are two runs of the same
provider. Independence is a property of the failure mode, not of the label.

## Grants

A grant is the exact form the charter's "owner may explicitly authorize a
designated agent" takes. It is data, written by the owner, and it is bounded on
every axis:

- exact repository, and the branch pattern it applies to;
- the enumerated tier-1 decision classes it covers, and no others;
- the named providers eligible to supply verdicts;
- a wall-clock expiry;
- a maximum number of exercises before it must be renewed;
- a revocation switch that takes effect before the next decision, not after.

A grant that is expired, exhausted, revoked, malformed, or silent on the class
being decided does not apply. Grants do not compose, inherit, or extend by
analogy: authority for one repository, class, or assignment is never authority
for another.

## Trip conditions

Any of these moves the deployment to `SUSPENDED` immediately, before the
decision in flight completes. For the pre-execution class two are narrower, as
its carve-out: a failed pre-authorized task is recorded against that `grant_id`
at the grant's next exercise attempt and suspends that grant only, and an audit
ledger that cannot be written holds only the process attended, as described
under operating states:

- a gate result changes after a verdict was recorded;
- a new commit appears on the candidate, which invalidates every prior review,
  security, CI, and merge evidence item;
- any reviewer returns `SERIOUS_SAFETY_STOP`;
- the owner reverses two consecutive delegated decisions;
- a provider authentication or quota failure occurs mid-decision;
- a tier-0 class is implicated at any point;
- a task a pre-execution grant started ends other than `SUCCEEDED`, is left
  with an ambiguous slot, or cannot be read (recorded against that grant);
- the audit ledger cannot be written.

The last one is not a formality. A decision that cannot be recorded cannot be
reviewed afterward, and an unreviewable autonomous action is exactly what this
framework exists to prevent.

## Audit record

Every delegated decision, and every refusal to make one, writes an immutable
record containing the grant identifier, the decision class, the exact candidate
commit, each verdict with its provider and run identifier, every gate result
consulted, the merging identity (the accepting reviewer), and timestamps. The record is written
before the action, not after it. For the pre-execution class, a refusal made
before the ledger is consulted (no grant for the class, delegation off or
suspended, a workload hold, a deployment that cannot execute, or a ledger that
is absent or latched) and an exercise lost to a concurrent writer write no
record; nothing runs without the button in either case. Neither does an
exercise whose audit history is too long or too slow to read within its bound
(10,000 records or five seconds, the deadline enforced inside the ledger's
own verifying scan, and in the verification every audit append repeats; the
wait for the store's lock and SQLite's busy wait spend the same deadline, which
is checked again before the audit anchor advances, and every anchor read and
advance is given only what is left of it; an anchor read that runs out refuses
cleanly, while an advance that runs out is an anchor failure, since the
checkpoint may have moved): it keeps the button.

## Standing grant for small read-only tasks

The tier-1 class `execute_small_read_only_task` lets a live grant start one
small read-only task without the per-task execution button. It is the only
pre-execution class: it authorizes starting that task and nothing else, never a
merge, a publication, or any other effect.

**Eligibility.** `policy.small_read_only_eligible()` alone decides membership,
deterministically, from the owner's intake text, the frozen plan, and
configuration. A task is eligible only when no deterministic approval reason
matches the intake or any generated plan string; the plan is low risk and
carries no approval requirement, approval reason, or debate; the intake carries
no public research query or required capability; the plan has exactly one
producer and one independent reviewer from another provider family; neither
assignment requires approval, opens a draft pull request, carries a public
query or a closed directive, or runs on the Perplexity, Firecrawl, or Gemini
lane; both lanes' configured transports show the model only the compact
provider view described below; the producer is not implementation work; the
intake and the producer's task are each a read-shaped request, meaning every
sentence opens with a read verb (summarize, explain, describe, review, list,
compare, answer, analyse), every clause of it does too (a sentence is split at
any character that is not part of a word or a path, such as a comma, colon,
dash, bracket, quote or bullet, and at joining words such as and, then, so,
but or or;
a clause after a split may otherwise be only paths or identifiers, the second
object of "compare A and B"), every "to ..." and "...ing it" names a read
action (every term check below also reads identifiers split into words, so
`openai_api_key`, `db-password`, `auth.token` or `authToken` count as their
words), no raw identifier of the kinds listed under Bounded provider traffic
appears, no verb that asks for a change (improve, revise, make, fix, update,
rename, format, set, open, file, post and a long list more, in any
inflection) appears anywhere in it, it names at least one path (a
repository-wide request would reach the model with an empty inventory), and
no shell syntax, dotfile, home, parent or
absolute path, secret-like word or variable name, write-shaped word, or outward
or mutating verb appears (the gateway's `DIRECTOR` command word and a leading
`Project:` value are not sentences, while every `Outcome:` and `Constraints:`
sentence is); nothing in the request names personal or sensitive data: the
people a record describes (customers, clients, employees, users, patients and
the like), an identifying attribute (a name, address, e-mail, phone, birth
date, ID, or health, financial or genealogy data), or a tabular or database
file or data-like directory, and wording that plainly names personal
identifiers is also a deterministic `sensitive data` approval reason for every
task; and the frozen plan total is at most $1 and within the task
budget, with the reviewer inside its own cap, and the plan's frozen hop and
revision limits are within the current `MAX_AGENT_HOPS` and `MAX_REVISIONS`.
Model or plan output can only remove eligibility. Every plan, assignment, and
intake model forbids extra fields, so nothing a model writes can assert it.
"Read-only" means the packet grants only `READ_REPOSITORY`: no test
execution, no write tool and no effect. The router grants the class only that
tool, and a class packet carrying any other tool or effect is refused at
dispatch (`small_task_packet_not_read_only`). It does not
mean a sandboxed process: native and bridge CLIs still run in a host
workspace. Membership is a lexical filter over the owner's words, not a
semantic guarantee: a keyword scan cannot show that no tier-0 class is
implicated, so evidence-bar item 7 is not evaluated before execution, and
whether this filter is enough to replace the per-task button is part of the
owner's sign-off on the class.

**Exercise.** The execution button is skipped only when all of these hold at
intake: `DELEGATION_ENABLED` is true and `DELEGATION_SUSPENDED` is false; a live
grant, unexpired and unrevoked, names this class, the task's repository as a
lowercase `owner/name`, its branch, and both of its provider lanes; the grant is
not exhausted; no `delegation_trip` record exists for its `grant_id` and no task
it pre-authorized must write one (see Trips); both lanes send the compact view;
and the intake is a Slack-gateway-attested message from the configured owner in the
configured channel that is not a replay. Only the request that created the
intake claim evaluates a grant; a retry that finds the claim before its task
exists keeps the button. A bearer intake never evaluates a
grant. Any other case stops for the button exactly as before.

**What is evaluated, and when.** At the decision point no candidate change and
no verdict exists yet, so evidence-bar items 1-5 cannot hold. The decision is
made by `hotl_execution.decide_pre_execution`, never by `H(a)`, over its own
closed terms: `authorization` (item 9, including an attested owner intake and
an untripped, unexhausted grant), `eligibility` (the lexical membership test
above; not item 7, which is not evaluated before execution), `bounds` (item 8),
`verification` (the exact starting commit and snapshot digest),
`lane_independence` (two distinct lanes from different provider families),
`safe_stopping`, `owned_paths_declared` (item 6 before any change), and `audit`
(item 10). `H(a)` holds this class, and no record uses the `gates` or
`independence` names for it, so nothing ever records items 1-5 as passing.
Items 1-5 are carried by the task's own unchanged graph
gates, `exact_sha_review` and `ci_security_completion`, which this class
never skips or relaxes.

**Audit.** A `PRE_AUTHORIZED` record is written before execution. It names the
grant, carries its snapshot and the exact starting commit, lists every
evaluated pre-execution term as `PASS`, and holds no verdict. A refusal writes a
`REFUSED` record naming the failed terms; a task outside the class is refused on
`eligibility` alone, without reading history, and claims no other term, and an
eligible intake that is not an attested owner message, or that uses a lane the
grant does not name, is refused on `authorization` alone, also without reading
history. The refusals that write no record are listed under Audit record. The
record that spends an exercise is appended only if the ledger still holds the
history it was counted from, so concurrent writers cannot overspend a grant.
`PRE_AUTHORIZED` is valid only for this class, and this class can never record
`DELEGATED` or `WOULD_DELEGATE`.

**Trips.** Trips are derived from durable state at every exercise, not from the
path a task happened to finish on. Before a grant is exercised again, every task
it pre-authorized is read: one that ended other than `SUCCEEDED`, one whose
parallel aggregate ended failed before the graph classified it, one left with
an ambiguous slot, or one that cannot be read writes a `delegation_trip` record,
which suspends that grant. That covers a task settled through a provider
callback and one whose process stopped after its terminal was persisted. A task
whose intake failed after its `PRE_AUTHORIZED` record never started; it spends
an exercise and trips nothing. The trip is also recorded as soon as a
pre-authorized task's failed terminal is persisted, without waiting for another
intake; that write is best effort, so the derivation at each exercise and at
each recovery stays the authority. An absent, unreadable, or unwritable audit ledger
holds the process attended, as described under operating states. Clearing a
trip takes an explicit owner action, a new grant_id.

**Bounded provider traffic.** Providers on the native CLI work-packet lanes,
the OpenAI Responses lane, and the Claude and Grok host-bridge lanes receive a
compact view of the work packet: its commit, digest, and file count, plus only
the snapshot entries under owned paths or named in the objective; an
attended assignment that names no path keeps the first 64 entries outside
prohibited paths, as the full packet gave it. The view is
defined by `docs/schemas/director-compact-work-view-v1.schema.json`, and every
view is validated against it before any transport sends it. For a small
read-only task the view also carries the text of every path the request names
(a path-shaped token, a conventional extensionless file such as `Makefile` or
`Dockerfile`, or any token that is exactly a path in the snapshot, each of which
must resolve to a snapshot entry),
read from the git blob its snapshot binds at the packet's commit (never a
working tree; the blob's size is checked before it is read, and the read is
streamed and stops at the cap) and bound by that blob hash, so a tool-less lane such as OpenAI
Responses can read them; the reviewer's objective names the same paths, so
both models see the same text. The OpenAI Responses lane may produce a small
task but never review one: it reports every acceptance check NOT_RUN, which a
review can never pass. With native lanes excluded and the Claude bridge lane
not attestable, the one pair that could complete a small task is an OpenAI
Responses producer with an attested Grok bridge reviewer, whose result must
carry structured acceptance evidence with every check PASS; readiness reports
armed only when such a pair exists and, by the same budget and token predicate
eligibility applies, could be eligible: the costs the deployment's plan
builder would freeze for that pair (each lane's configured attempt budget
times three when live, so the default equal split of a $2 task budget never
qualifies) are within $1 and the task budget, the reviewer's within its cap,
and `SMALL_TASK_REVIEW_MAX_TOKENS` is at least 1. A review accepts only when its result
disposes of exactly the review packet's `acceptance_checks`: each required
check once, none missing, no extra and no duplicate, all PASS, and no risk,
blocker or requested decision. Any other result is a rejection.

**Post-hoc reviewer cap.** Owner ruling, 2026-09-24: "Accept post-hoc cap".
The attested Grok host-bridge reviewer is admitted even though the Grok CLI
cannot enforce a token or dollar cap before launch. The reviewer's dollar and
token cap is checked after the call: the bridge result's reported usage is
compared with the attempt's limits as soon as it returns, an overage fails the
attempt and so the task, and that failure suspends the grant through the
ordinary trip; a result that reports no usage fails as
`small_task_usage_unmeasured`. In practice the spend is bounded by the attested
configuration: one turn, no tools, read-only, and a bounded compact input. Each file is at most 4 KB, the total at most
8 KB, and at most eight files. A named path missing from the snapshot,
prohibited, binary, not UTF-8, over a cap, matching the sensitive-data or
credential approval gate or the request text's personal-data exclusion (as
written or with identifiers split into words), holding a secret-shaped value,
or holding a credential-bearing value (a provider, chat or cloud token such as
`sk-`, `pk-`, `rk-`, `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`, a fine-grained
`github_pat_`, `xox?-` or `AKIA`, one list the public-research query block
shares; URI user information, an Authorization
or Bearer value, a JWT, an AWS access key, a connection-string password, or a
high-entropy value assigned in an env or config line), or holding a raw
identifier with no label (an e-mail address, social security
number, phone number, card, IBAN or routing number that passes its checksum,
IP address, street address, a date next to a birth label, or a passport-like
identifier) keeps the task out
of the class: the grant is refused on `eligibility` at intake, and a dispatch
fails with `small_task_named_contents_unavailable`. Dispatch builds and
validates the view once, contents required, and keeps it by the packet's
digest; every transport sends exactly those bytes. A transport that must
rebuild it and cannot read the files refuses before any provider request, a
hold that sends nothing and does not trip the grant, never a view carrying
hashes only. Every blob read spends the caller's deadline: an assignment's
reads spend its readiness selection's absolute deadline (a small task with no
selection is held before any read), intake's check one bounded window for all
files, and a read that reaches the deadline ends its process within what is
left, never a fresh cleanup window. The cursor,
github-copilot, native Codex, and Antigravity host-bridge lanes still post the
full packet or a closed directive, so a task using any of them is outside the
class and keeps the button. Every native CLI lane (Claude, Grok, Cursor, GitHub
Copilot) is outside the class as well although it sends the compact view, by
the owner's ruling of 2026-09-24 ("Exclude native lanes"): a native CLI runs in
the host workspace with its own tools and web access this deployment does not
control, and native Cursor and GitHub Copilot also never refuse a result whose
reported usage exceeds the admitted attempt. Native lanes stay available for
every other task; the small class runs only on the OpenAI Responses and
host-bridge lanes, and `/health` counts only those toward readiness.

**Attested bridge lanes.** Owner ruling, 2026-09-24: "Bridge with attested
sandbox". A host-bridge lane joins the class only when the bridge's
HMAC-authenticated challenge reports the exact argv it runs for that lane, the
Director re-checks that argv against the lane's read-only restriction set in
`bridge_attestation.py`, and its digest equals the owner's pin in
`LANGGRAPH_PRIMARY_HOST_BRIDGE_ATTESTED_ARGV_JSON`. A small task's dispatch
re-challenges the bridge before admission, and again immediately before every
bridge send, each producer retry and reviewer attempt included; if the
attestation is gone, changed, or cannot be authenticated, nothing is sent
(`small_task_bridge_attestation_stale`). The signed dispatch request itself
carries the pinned argv digest, and the bridge compares it, in constant time,
with the digest of the argv it is about to run, immediately before starting it;
a mismatch runs nothing and returns a signed refusal the Director also treats as
`small_task_bridge_attestation_stale`. The bridge settles that attempt's claim
as `REFUSED`, never `AMBIGUOUS`, and a replay of the same request returns the
same signed refusal without running anything, so recovery treats it as a no-run
refusal and it never trips the grant. The same signed request carries the
attempt's dollar budget and token limit; the bridge passes them to the host
command as `DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD` and
`DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT` and records them with the attempt, and
refuses a small task's request that lacks them. The lane's attestation reports
whether its command enforces them before inference (today only
`scripts/xai-responses-command.py` does; the Grok CLI does not), and by the
ruling above the Director admits an attested lane either way. The restriction sets use only flags this repository
already passes to each CLI. Grok needs plan permission mode, `--max-turns 1`,
`--disable-web-search`, `--no-subagents`, the canonical `--disallowed-tools`
list and `--deny` for Bash, Edit, Write and MCPTool. Claude needs plan
permission mode, an empty `--tools` value, `--strict-mcp-config` with an empty
MCP configuration, and `--safe-mode`; the bridge refuses empty argv items, so
the Claude bridge lane cannot currently be attested. An argv that carries the
whole set is still refused if it also carries any argument in
`FORBIDDEN_ARGS`, alone or as `--flag=value` and in any letter case:
`--yolo`, `--dangerously-skip-permissions`,
`--allow-dangerously-skip-permissions`, `--allowedTools`/`--allowed-tools`,
`--add-dir`, `--enable-web-search`, `--resume`, `--continue`, `--api-key`,
`--base-url`, `--api`, `--json-schema`, or the `acp` and `exec` subcommands.
Any permission mode other than plan, such as `bypassPermissions` or
`acceptEdits`, already fails the set. This attests the CLI's
configured command line, not an operating-system sandbox: the CLI still runs as
a host process in the bridge workspace, and the restrictions are the CLI's own
flags. The argv is also bound into each lane's configuration digest, which the
bridge's `/v1/provider/configuration-digest` endpoint reports with the per-lane
verdicts. The Director keeps the full packet and rebinds
every result to it by digest. The compact view a small task's reviewer is sent
is at most 16 KB; its transport's fixed instructions are extra. The reviewer is
also capped by `SMALL_TASK_REVIEW_MAX_USD` (default 0.50) and
`SMALL_TASK_REVIEW_MAX_TOKENS` (default 40,000), one allowance for the whole
review: only a transport failure is ever retried and its usage is unmeasured,
so a small task's reviewer is never retried. Every producer and reviewer result
of a small task must report complete usage, integer token counts and a cost;
the OpenAI Responses lane, which refuses any reply over its token allowance
before returning it and is priced by its meter, reports tokens only. A result
without that usage fails, which trips the grant. The producer's packet states the
outcome size that fits, and a producer result whose review would exceed 16 KB is
refused when it is accepted, before it is recorded and before any reviewer
work. A result refused there still spent the producer's attempt. A
pre-authorized task, one whose persisted graph skips the execution button, is
refused at dispatch unless delegation is still enabled and unsuspended under a
live grant for the class whose canonical document is the snapshot recorded
when the task was pre-authorized and which still covers the task's repository,
branch and both lanes (`small_task_pre_authorization_revoked`), the verified
audit chain holds this task's own `PRE_AUTHORIZED` record under that grant's
grant_id, carrying every pre-execution term but `audit` exactly once and each
`PASS` (`small_task_pre_authorization_unaudited`; a record with only some of
the terms, an extra one or a repeat counts as none; an absent, unreadable or
replaced ledger has none, and a history past its bound counts as unread),
no `delegation_trip` for that grant_id exists anywhere in it and no task that
grant pre-authorized has failed, stopped ambiguous, or become unreadable
according to durable task state, whether or not a trip was written yet
(`small_task_pre_authorization_tripped`; that scan of prior tasks is bounded
by one deadline and, when it runs out, refuses as unaudited without tripping),
the packet being dispatched works from the exact commit recorded as the
pre-authorization's candidate (`small_task_pre_authorization_commit_changed`),
and it still qualifies
(`small_task_pre_authorization_lapsed`, for example after a restart under a
lower reviewer cap, different lanes, or a `MAX_AGENT_HOPS` or `MAX_REVISIONS`
below the plan's frozen hop or revision limit), rather than run without the class's
bounds; a task whose graph keeps the button is never affected. That failure
trips the grant. The same check runs again immediately before every provider
send of a pre-authorized task, each retry included, and inside the transport
before each provider request it makes (the OpenAI Responses token count and
its generation alike); a lapse sends nothing. Those rechecks spend what is left
of the dispatch's shared deadline, never a fresh window, and with nothing left
refuse before any read; the first check at dispatch, and the bridge
re-attestation before admission, spend it too. Every identifier, personal-data and credential screen
ignores letter case except where the format itself is case-sensitive (token
prefixes such as `AKIA`, JWTs, upper-case variable names).

**Prerequisites.** The frozen plan total must be at most $1, for example by
setting `LANGGRAPH_PRIMARY_PROVIDER_ATTEMPT_BUDGETS_USD_JSON`; the default task
budget splits above that. The ledger also needs a durable, separately protected
audit anchor. With no production anchor, every exercise holds and the button is
unchanged. Roster selection ranks lanes through the existing
`TRUSTED_PROVIDER_COST_RANKS`; a small-task deployment is expected to rank
grok < openai < claude, e.g. `{"grok":1,"openai":2,"claude":3}`; this change
does not set it.

Adding this class was a tier-0 act; it takes effect only with the owner's
explicit sign-off on the pull request that adds it.

## Relationship to the existing rules

- **AGENTS.md rule 3 is unchanged.** No agent merges its own pull request.
  Delegation never makes a producer its own merger; it supplies a different
  agent, and evidence-bar item 5 enforces the separation.
- **AGENTS.md rule 10 is unchanged as the default.** It still stops for every
  tier-0 class, and for every tier-1 class where no live grant applies.
- **The deterministic gates in `policy.py` are unchanged and remain
  authoritative.** Model output may add approval requirements and may never
  remove them. That includes output from a delegated reviewer.
- **The charter's review-to-merge handoff is unchanged.** Delegation supplies
  the single reviewer identity, who also merges; it does not alter what
  a review must contain or when evidence is invalidated.
- **The charter's Director and provider authorship boundary supplies the
  identity terms used here.** "Producer" and "material author" mean whoever
  actually produced or materially authored the candidate. Delegation, routing,
  coordination, QA, review orchestration, relay, merge eligibility evaluation,
  and merge execution create no authorship, so performing them neither makes an
  identity the producer for evidence-bar items 3, 4, and 5 nor makes an actual
  author eligible under them.

## How the existing gates are satisfied, not substituted

Two objections have to be answered explicitly, because on a fast reading the
existing rules look like they forbid this framework outright. They do not, and
the reasons matter more than the conclusion.

### The independent exact-SHA review gate

The charter says owner authorization "never substitutes for applicable
deterministic approval, exact-SHA review, security, CI, budget, allowlist, or
rollback gates." That sentence stays exactly as written, and this framework does
not weaken it. It governs what an authorization may *skip*, and the answer is
nothing.

It is not in tension with a delegated decision, because the exact-SHA review
gate has never consumed an owner verdict. The charter's own review-to-merge
handoff assigns review to "a rotating different-provider review assignment,
choosing the least-recent eligible non-author," and that reviewer returns
`ACCEPT`, `CHANGES_REQUIRED`, or `SERIOUS_SAFETY_STOP`. A named provider's
exact-SHA `ACCEPT` is therefore the gate being **satisfied in its ordinary
form**, not an authorization substituting for it.

What delegation changes is narrower than it first appears. Previously the gate
could be satisfied while the *merge decision that follows it* still waited for
the owner. Under a live grant, that following decision may proceed, and only
then, and only when every other item on the evidence bar also holds. The
framework requires the same one non-author provider verdict the charter
requires, so a delegated decision clears the same review bar as an attended
one, never a lower one.

### Rule 3's no-self-merge bar

AGENTS.md rule 3 forbids an agent from merging its own pull request. That bar
never lifts, and this framework does not create an exception to it.

"Your own" is a property of identity, not of species. Two agents are not one
agent. Evidence-bar item 5 requires the author and the reviewer to be two
distinct identities, so a delegated merge is
by construction never a self-merge. An agent that produced or materially
authored a candidate is permanently ineligible to review or merge it, and no
grant can make it eligible: delegation names who *may* act, and never widens
*what* an ineligible actor may do.

This has a consequence the owner should expect rather than be surprised by.
When only one agent is available, a delegated merge cannot proceed at all,
because two distinct identities cannot be found. That is the framework
working, not failing. It is also the present condition of this repository.

## Implementation phases

Each phase is separately built, reviewed, and accepted. No phase begins before
its predecessor is accepted, and acceptance of one grants nothing to the next.
Row 4S names its own predecessor below.

| Phase | Content | Executes a decision? |
| --- | --- | --- |
| 0 | This document. Authority definition only. | No |
| 1 | Grant schema, settings, validation, `/health` readiness booleans, all default off. | No |
| 2 | Audit ledger and record format. | No |
| 3 | Shadow mode: record `would_delegate` and `would_stop` over a bounded set of decisions, changing nothing. | No |
| 4 | Enable tier-1 merge only, one repository, owner observing. | Yes |
| 5 | Broaden tier-1 classes only after a clean shadow record and a clean live record. | Yes |
| 4S | The pre-execution class `execute_small_read_only_task` only, one repository, live grant, owner-signed PR. | Yes |

Row 4S is a separate track, not a step after phase 5. Its predecessor is
phase 2, and it does not depend on phase 4. For this one class only, it waives
phase 3's shadow record and phase 5's clean-shadow and clean-live
preconditions. That waiver is part of the tier-0 act the owner's sign-off on
its pull request accepts; without that sign-off, row 4S does not exist.

## The evaluated form

Phase 0 now has an executable form in `src/director_orchestrator/hotl_execution.py`,
described in [`hotl-execution-predicate.md`](hotl-execution-predicate.md). It
implements the rules above and adds none. The rules above include the
pre-execution carve-out, so `decide_pre_execution`, which decides only
`execute_small_read_only_task` over its own closed terms, is part of that form
and not an addition. Where the two disagree, this document wins and the code
is wrong.

Every other action, which is candidate-bound, runs without returning to the
owner only when every term holds:

```
H(a) = A(a) and G(a) and B(a) and V(a) and I(a) and S(a) and P(a) and D(a)
```

authorization, gates, bounds, verification, independence, safe stopping, path
scope, durable audit. It is a conjunction, not a score; one unmet term is
decisive.

`P` and `D` were absent from this formula while items 6 and 10 of the evidence
bar above were not. An implementation following the six-letter form returned
`execute` for a change reaching a prohibited path with no audit record, and an
independent review found the omission here rather than only in the code. This
page is authoritative, so leaving the short form would have recreated the same
gap in the next implementation to read it.

Two properties carry the weight, and both are pinned by tests that assert the
negative:

- **The tier check runs before the conjunction, and fails closed.** No
  combination of satisfied terms can carry a tier-0 act. Evaluating the terms
  first would let eight green terms authorise a production deployment.
- **A hold names its blocker and is never a permission question.** Missing
  evidence routes to verification; a temporary failure routes to its authorised
  recovery; only a blocker needing authority outside the standing grant reaches
  a person. A system that renames an interruption has not removed one.

The measure is the autonomous completion rate over a denominator fixed before
execution, so blocked or failed work cannot leave it by later reclassification.
An empty population reports no rate rather than a flattering one.

## Provider activation evidence does not stop expiring at LIVE_OK

There is no expiry-window constant. `expires_at` is supplied per record.

`ProviderActivationRegistry._surface_readiness` scans **every** record in the
five-stage chain and reports the surface not ready if any one has expired. Two
consequences follow, and both differ from the intuitive reading:

- Reaching `LIVE_OK` does not settle the question. The earliest record,
  `INSTALLED`, keeps the surface live only while it is unexpired; when it
  lapses, `live_ready` goes false and live dispatch stops however recent
  `LIVE_OK` is. The condition is continuing, not a race to activation.
- Letting the prior stage lapse mid-chain does not cost a retry. Advancing then
  raises *the prior activation stage expired; use a new binding version*, which
  discards the whole chain for that binding.

So advances belong immediately before activation, and the expiry chosen at
advance time must cover the lane's intended operating life rather than the gap
to `Activate`.

## Known limitations

These are stated plainly because a framework that hides them is worse than none.

- An agent verdict is not equivalent to human judgment on novel risk. The
  evidence bar is good at catching what it models and blind to what it does not.
- Providers that share a base model can fail in correlated ways. The non-author
  rule reduces independent error, not common-mode error.
- Reviews read untrusted material. A diff, an issue, or a CI log can carry
  instructions aimed at the reviewer, and a delegated reviewer has no owner
  looking over its shoulder.
- Delegation had not produced a verdict when this was written, and the failures
  were attributed to quota, authentication, or availability. That diagnosis was
  wrong for most of them. Provider CLIs read permissions from a shared settings
  file with no `gh` rule, and a denied command surfaces as a cancelled session,
  which reads exactly like a broken provider. Measured on 2026-09-08:
  `git rev-parse` returned `end_turn`, `gh pr view` returned `cancelled`, and
  the same prompt with `--allow 'Bash(gh pr view:*)'` returned the answer. Four
  dispatches were abandoned on the misreading, and the delegation pool was
  reported to the owner as empty on the strength of it. Cursor's CLI is
  separately blocked by usage quota, which is a real availability failure.
  Phases 3 and 4 still exist before any broadening of the candidate-bound
  classes (row 4S is the stated exception); the reason is caution, not this
  evidence.
- This document was drafted by an agent. An agent writing the rules that govern
  agents is a conflict of interest, and the owner should read it as a proposal
  to be checked rather than as settled policy.

<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:11 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 02:33 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 03:44 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-04 | 17:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:16 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-12 | 02:27 UTC | END]] -->

<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:05 UTC | END]] -->

<!-- [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:52 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:25 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:27 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 21:18 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:01 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:38 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:15 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 09:00 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:14 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:17 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:45 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:02 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:44 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:21 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:07 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 03:23 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 04:07 UTC | END]] -->
