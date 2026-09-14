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
  co-authors.
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
nor a material author of the candidate, and the Director still may not
independently accept or merge code it actually materially authored.

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
