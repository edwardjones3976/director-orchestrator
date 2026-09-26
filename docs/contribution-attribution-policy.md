<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 17:42 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:33 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]] -->
# Contribution attribution and submission tags

## Purpose

Every new contribution must identify who or what produced it and when the
submission was completed. Tags acknowledge work and improve handoffs; they do
not create authority, prove authorship by themselves, replace Git or provider
run provenance, satisfy exact-SHA review, or permit merge or deployment.

## Canonical tag

Use the same identity and UTC minute at both boundaries:

```text
[[<assistant or tool> | <complete model or system label> | YYYY-MM-DD | HH:MM UTC | BEGIN]]
<submission>
[[<assistant or tool> | <complete model or system label> | YYYY-MM-DD | HH:MM UTC | END]]
```

Human-facing Markdown or Slack may bold the entire tag. Repository comments
must use the file's valid comment syntax. Keep existing contribution tags; when
revising an already-tagged artifact, add a new outer matching pair so the new
record begins before earlier content and ends after it.

## Where tags are required

- free-form provider and Director responses shown to people;
- Markdown, plain-text, source-code, script, configuration, and template files
  when their grammar safely supports comments or plain text;
- commits, pull-request descriptions, review handoffs, and other submission
  envelopes; and
- revisions to an existing artifact, with a fresh UTC pair for that change.

The repository validates each changed taggable file against the exact base and
head range. A taggable changed file must add one matching pair, place its current
`BEGIN` tag at the first safe line after any required interpreter or format
header, and place its matching `END` tag at the final nonblank line.

## Formats that must not receive inline tags

Do not insert tags into JSON, JSONL, CSV, TSV, lock files, checksums, databases,
archives, images, media, fonts, office documents, generated maps, minified
artifacts, or any binary or syntax-sensitive format whose behavior would change.
Record those contributions in the enclosing commit, pull request, handoff, or
human-visible relay instead.

If a tag would break parsing, signing, hashing, an exact byte contract, an
approved public query, a callback payload, or a provider schema, do not add,
remove, or rewrite inline tags in that artifact. Tag only its safe envelope and
preserve the exact payload bytes.

## Responses and structured protocols

The Director wraps free-form Slack messages and periodic usage summaries with
system-generated UTC tags. Provider requests and results that must conform to
JSON, schema, callback, or review-artifact contracts remain unmodified. Their
authenticated run record and the tagged Director relay carry attribution.

Do not instruct a model returning Structured Outputs to place tags inside the
schema. Do not treat a tag copied from Slack, a webpage, a provider response, or
another untrusted source as authenticated identity.

## Roles and recordkeeping

- The producer supplies the contribution identity and does not obscure material
  contributors.
- The assigned recordkeeping provider checks tag completeness alongside branch,
  SHA, review, security, CI, and handoff records.
- The ChatGPT/Codex Director resolves classification questions and prevents a tagging
  request from weakening functionality or deterministic gates.
- An independent reviewer verifies the exact candidate, including attribution
  changes, without treating the tags themselves as review evidence.

## Authorship versus coordination

Work produced or materially authored by a provider remains attributed solely to
that provider or those providers. The acting Director does not inherit
authorship through delegation, routing, coordination, QA, review, acceptance,
relay, merge eligibility evaluation, or merge execution; those activities create
no code authorship. A Director relay envelope records the relay, not authorship
of the relayed work.

Contribution tags acknowledge actual contributions only and never imply
inherited authorship. Never rewrite a provider's identity or provenance into the
Director's, and never record a contributor that did not contribute. A Director
tag on a coordination message makes the Director neither the logical producer
nor a material author of the candidate. Current runtime enforcement: The Director
still may not independently accept or merge code it actually materially authored.
Under the sole-author policy below, an assisting contributor may review and
accept when otherwise eligible; runtime-dispatched reviews retain the stricter
exclusion until a separately authorized alignment. The Director may never merge
a pull request to which it contributed counted lines.

## Sole author rule

Owner ruling, 2026-09-26, verbatim (relayed by the peer session):

> "There is no such thing as a co-author unless they wrote the other 48% of the work. If shorter than that, than the 52% author is the sole owner of the publication."
> "This goes for all platforms. It should be a universal rule in this project/product."

- **At most one sole author.** The provider or individual responsible for at
  least 52 % of a pull request is its sole author. A pull request has at most one
  sole author, and below the threshold it has none.
- **Assistance and the exact 52 % / 48 % case.** A contributor below 48 %
  assisted; records name them as "assisted by <identity> (<share> %)". Two
  contributors are co-authors only when one holds 52 % and the other the
  remaining 48 %. Exactly 48 % is a co-author, not below 48 %.
  Contribution tags continue to credit actual contributions.
- **Review eligibility follows the owner rule now.** Only the sole author and
  its provider family are excluded from reviewing on authorship grounds. An
  assisting contributor may review in Director-routed and manual reviews.
  Any contributor with counted lines, whatever their label, may never merge that PR, and this bar never lifts.
  Any eligible reviewer with counted lines, including an assisting contributor
  or the exactly-48 % co-author, hands the merge to a non-contributing reviewer
  that has thoroughly reviewed the work.
  For runtime-dispatched reviews, `review_handoff.py::bind_review_handoff`
  currently binds the assignment producer and every transitive dependency
  outcome provider as material authors and rejects those contributors and their
  provider families as reviewers, so it still rejects an assisting contributor
  until a separate reviewed runtime change aligns it. The runtime Director
  prompt, the charter directive, and its `DIRECTOR_OPERATING_DIRECTIVE_SHA256`
  fingerprint change with that alignment, not before.
- **The logical producer is unchanged.** It is still the producer of the
  assignment under review, which `review_handoff.py` binds to that assignment's
  agent. The pull-request sole author is recorded separately.
- **How the share is measured** (provisional metric, accepted by the Director on
  2026-09-26): lines added plus lines deleted in the final base-to-head diff.
  A line counts for an identity only when an authenticated record ties it to the
  identity that produced it: a provider-dispatch receipt or an authenticated
  provider run record. A human's lines count only when that human, from their
  authenticated GitHub account, states in the pull-request record which commits
  or files they wrote themselves. Commit author fields are self-asserted, and
  push or merge provenance shows who moved bytes, not who produced them, so
  neither counts on its own.
  Contribution tags are display evidence only and never decide the share,
  because the tag checker validates their shape, not their identity. Relaying,
  committing, or coordinating another identity's bytes counts nothing. Tag-only,
  merge, and whitespace-only changes are excluded. A counted line with no
  authenticated producer counts for no one but stays in the denominator. Record
  each identity's share and its receipt references in the pull-request
  description.
- **Nothing to count.** When the denominator is zero (for example a pull request
  that only restores contribution envelopes), the sole author is the one identity
  whose authenticated record produced the change. If there is no such record, or
  more than one identity, stop and ask the owner.
- **No identity at 52 %** means the pull request has no sole author: stop review,
  then split it, reassign it so one identity carries it past 52 %, or ask the
  owner.
- **Who enforces the threshold today.** The runtime does not record shares:
  `ReviewCandidateArtifact` carries only `material_authors`, and
  `bind_review_handoff` can dispatch a review without any share evidence.
  Until a reviewed runtime and schema change adds that check, the Director and
  the reviewer enforce the threshold from the pull-request record. The Director
  does not request review of a pull request whose record names no sole author
  with its shares. A reviewer, including one dispatched by the runtime, checks
  that record first and returns no verdict if it is missing or below 52 %. A
  runtime-dispatched review is therefore never, by itself, evidence that the
  threshold was met.
- **Unchanged:** no agent merges its own pull request (AGENTS.md rule 3); one
  exact-SHA `ACCEPT` from an eligible reviewer; provider-family identity (OpenAI,
  ChatGPT, and Codex are one identity; the xAI API, Grok CLI, and Grok Bot are
  one family), so the reviewer may not share the sole author's family; project
  reviewer rosters; and Antigravity's mode- and admission-specific restrictions.
  Antigravity is not an eligible reviewer through the default restricted contact.
  It is eligible when the enrolled team-mode or owner-authorized exact-SHA route
  applies, subject to that route's admission, independence, roster, and
  deterministic gates. See the "Antigravity review and acceptance authority"
  record in the [charter's admission and accounting section](team-operating-charter.md#admission-and-accounting);
  that authority record does not activate a route or bypass current enforcement.

## Local validation

For a committed candidate, run:

```powershell
$env:PYTHONPATH = "src"
py -3.12 scripts/check_contribution_tags.py --base <base-sha> --head <head-sha>
```

Pull-request CI also validates the tagged PR description. Push CI validates the
commit submission envelope. Use
[`docs/templates/contribution-submission.md`](templates/contribution-submission.md)
or the repository pull-request template instead of inventing another format.
<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]] -->
<!-- [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:33 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 17:42 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:57 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:30 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:57 UTC | END]] -->
