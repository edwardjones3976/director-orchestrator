<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:32 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:20 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:09 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:00 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:47 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:37 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:23 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:12 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:57 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 17:42 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:33 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]] -->
<!-- [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]] -->
<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]] -->
# Contribution attribution in Git

## Rule

No contribution tags are written into file contents any more. Every commit
carries `Contributed-by: <Assistant or Tool> | <Complete Model or System>`,
one trailer per contributor; keep existing `Co-Authored-By` trailers.
Every PR description carries exactly one header line:
`Sole author: <assistant> (<model>)`, followed on that line by
`; assistance: <id>[, <id>...]` when others assisted. Follow the 52% sole-author
rule defined by [PR #284](https://github.com/edwardjones3976/director-orchestrator/pull/284);
this policy does not redefine it.
Existing in-file tags are historical; a later mechanical PR removes them.
Attribution records acknowledge actual contributions only and never imply
inherited authorship. They never authenticate provider identity, grant authority,
or satisfy exact-SHA review, security, CI, budget, approval, merge or deployment gates.

## Canonical tag

Historical contribution envelopes remain recognizable by `TAG_PATTERN` and
`parse_contribution_tag` in `src/director_orchestrator/contribution_tags.py`
for share accounting. They are not required on new file edits.

## Commit trailers and PR header

Put trailers in the final commit-message block, separated from prose by a blank
line. Use complete, nonempty assistant/tool and model/system labels, one
`Contributed-by:` trailer per actual contributor. Preserve `Co-Authored-By`
trailers alongside them. For example:

```text
Move attribution into Git

Contributed-by: OpenAI Codex | GPT-6
```

A PR description has one visible, unquoted header outside comments and code
blocks, with optional assistance on the same line:

```text
Sole author: OpenAI Codex (GPT-6); assistance: reviewer-id
```

Replace template placeholders. This declaration does not establish compliance
with PR #284 by itself; the reviewer checks that rule and the evidence.

## Authorship versus coordination

Work produced or materially authored by a provider remains attributed to its
actual producer. The acting Director does not inherit authorship through
delegation, routing, coordination, QA, review, acceptance, relay, merge eligibility
evaluation, or merge execution; those activities create no code authorship.
The Director may not independently accept or merge code it actually materially
authored. Never rewrite provider identity or record a contributor who did not
contribute.

Contribution tags acknowledge actual contributions only and never imply
inherited authorship. Never rewrite a provider's identity or provenance into the
Director's, and never record a contributor that did not contribute. A Director
tag on a coordination message makes the Director neither the logical producer
nor a material author of the candidate. Current runtime enforcement: The Director
still may not independently accept or merge code it actually materially authored.
Under the sole-author policy below, an assisting contributor may review and
accept when otherwise eligible under that policy; for any review, acceptance or
merge passing through the material-author gates listed below, contributor review
eligibility is prospective until a separately reviewed runtime change aligns
them. The Director may never merge
a pull request to which it contributed, including uncounted maintenance.

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
- **Review eligibility under the owner rule; runtime limits still apply.** Only
  the sole author and its provider family are excluded from reviewing on
  authorship grounds under the policy. An assisting contributor may review in
  Director-routed and manual reviews subject to the runtime limits below.
  Every contributor, including one whose only changes are uncounted maintenance,
  may never merge that PR; this bar never lifts. Excluding maintenance affects
  only the 52 % share calculation, not the merge prohibition. Such an assisting
  provider may review subject to the limits below, but never merges. Any eligible
  reviewer who contributed, including uncounted maintenance or the exactly-48 %
  co-author, hands the merge to a non-contributing reviewer that has thoroughly
  reviewed the work.
  The deterministic gates in `src/director_orchestrator/provider_acceptance_v3.py`,
  `completion_gates.py`, `merge_eligibility.py`, `engineering_workload_ledger.py`,
  and `review_handoff.py::bind_review_handoff` still reject every material
  author. The handoff also binds the assignment producer and every transitive
  dependency outcome provider as material authors and excludes their provider
  families. For any review, acceptance or merge passing through these gates,
  contributor review eligibility is prospective until a separately reviewed
  runtime change aligns them, including Director-routed and manual reviews
  whose verdicts pass through those gates. The runtime Director
  prompt, the charter directive, and its `DIRECTOR_OPERATING_DIRECTIVE_SHA256`
  fingerprint change with that alignment, not before.
- **The logical producer is unchanged.** It is still the producer of the
  assignment under review, which `review_handoff.py` binds to that assignment's
  agent. The pull-request sole author is recorded separately.
- **How the share is measured and bound to review** (owner ruling, 2026-09-27):
  the sole author and shares are computed from the git record at the exact
  reviewed merge-base SHA (`merge_base_sha`) and head SHA:
  `git log <merge-base>..<head> --no-merges --format='%H %an' -p -w --ignore-blank-lines --no-color --no-ext-diff --no-textconv -M -C --find-copies-harder`,
  counting only `+` and `-` lines inside patch hunks (not file headers), per
  commit and authenticated identity. If a PR contains binary changes and more
  than one producer contributed, stop for owner classification unless all binary
  changes come from one producer who also holds the text majority; that case
  proceeds normally. Exclude renames or copies with 100 %
  similarity from both numerator and denominator; count only changed lines
  in edited renames. After removing the `+` or `-` diff prefix,
  skip empty or whitespace-only content: `-w` and `--ignore-blank-lines` do not
  remove those lines inside mixed hunks. Exclude only a line that is exactly one
  contribution envelope in any comment syntax accepted by the [canonical tag
  policy](#canonical-tag): `#`, `//`, `--`, `/* … */`, `<!-- … -->`, or `**…**`.
  Use the canonical recognizer in
  [`src/director_orchestrator/contribution_tags.py`](../src/director_orchestrator/contribution_tags.py)
  (`TAG_PATTERN` and `parse_contribution_tag`);
  do not define a separate tag grammar.
  Tag-like substrings inside strings, fixtures, or examples stay counted.
  Maintenance lines are assistance, excluded from both numerator and denominator
  only when an MX packet receipt or owner directive explicitly identifies the
  commit hash, or the commit message carries a `Contributed-by:` trailer and a
  matching MX-NNN reference listed in the PR's recorded share table. Self-asserted
  prose alone is insufficient.
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
  description. The share record stated at the reviewed head is the authoritative
  artifact: identities, counted and uncounted lines, method, reviewed target-tip
  SHA (`base_sha`), merge-base SHA (`merge_base_sha`), and head SHA. PRs governed by this policy merge with
  `--merge`, never squash (#310), preserving commit-level evidence on main.
  Reviewers recompute from that merge-base/head pair while history is available;
  if history is ever collapsed, use the authoritative record without recomputing.
  A reviewer who can neither recompute nor find that record returns no verdict.
  The authorship computation uses only `merge_base_sha` and head SHA;
  `base_sha` remains the PR current target-tip SHA for runtime exact-candidate
  binding. The review result must record `base_sha`, `merge_base_sha`, head SHA,
  and `share_record_sha256`, the SHA-256 digest of the exact UTF-8 share-record
  text used by the reviewer, including the PR share table, MX references used
  for maintenance exclusions, and human-attribution entries in the PR record.
  Any change to that record or any of the three SHAs, including a retarget that
  changes the base, invalidates the verdict. Once #302 lands,
  the commit trailers carry the record.
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
  the reviewer enforce the threshold from the exact merge-base/head computation or
  authoritative reconstruction record above. The Director
  does not request review of a pull request whose record names no sole author
  with its shares. A reviewer, including one dispatched by the runtime, checks
  that record against the exact reviewed merge-base/head pair, recomputes while
  history is available, and otherwise uses the reconstruction rule above.
  Missing or conflicting evidence, or a share below 52 %, yields no verdict. A
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

## Validation

The CI job remains **Contribution attribution**. The checker selects commits
with `git rev-list base..head --not origin/main`: only commits introduced by
the change that are not already reachable from the fetched default branch.
A zero base (new ref) selects all commits reachable from head, still excluding
default-branch history. This repository's default branch is main; for another
default-branch ref, pass `--default-branch-ref <fetched-ref>`.

Fetch the current default branch before running the checker (CI checks out full
history). A missing or unreadable default-branch ref fails with a setup error,
even for an empty range; the checker never silently skips validation or fetches
on the caller's behalf.

Each selected commit requires at least one well-formed `Contributed-by:` trailer
and no malformed contributor trailers. Git's trailer parser is used, so a
mention in ordinary prose is insufficient. The only merge exemption is exactly
two parents, committer `GitHub <noreply@github.com>`, and either the standard subject
`Merge pull request #N from <source>` or the existing preflight subject
`Merge #N: reviewer <family> after merge-preflight (CI <run-id>)`.
These web-flow merges integrate content
checked on the PR. This metadata is not authentication. Local merges, including
conflict resolutions, and other merge forms still require trailers when selected.

Main push events validate the actual `base..head` range without subtracting
main: the fetched default branch already contains the push head. Legacy history
before the event base is not revalidated. Newly introduced commits still need
trailers; a successful PR check does not exempt unvalidated main-push commits.

CI runs on `pull_request` and pushes to `main` only. On pull-request events,
the checked range ends at `pull_request.head.sha` from the event payload,
overriding `--head`. It must be a 40-hex commit SHA readable from Git; a missing,
invalid or unreadable source head fails closed. Checkout's synthetic merge
commit (`Merge <sha> into <sha>`) is never selected as the PR range head.
The checker also requires exactly one `Sole author:` header in the event-file
PR body. Push events check trailers only. Main pushes do not exclude default-branch history
from the event range; other branch pushes retain that exclusion.
No commit-message BEGIN/END tag envelope is required on any event.
Unreadable revisions or event files fail closed. The checker does not read,
require or validate in-file tags: adding or removing them never fails this
check. The policy still prohibits writing new tags.

```powershell
$env:PYTHONPATH = "src"
python scripts/check_contribution_tags.py --base <base-sha> --head <head-sha>
python scripts/check_contribution_tags.py --base <base-sha> --head <head-sha> --github-event <event-file>
```

Use [the submission template](templates/contribution-submission.md) or the
repository PR template. Keep commit trailers when merging or otherwise
preparing the final commit.

## Later mechanical cleanup

Existing tags remain historical in this PR. The later mechanical PR or an
authorized in-flight branch can run:

```powershell
python scripts/strip-tag-lines.py path/to/file.md path/to/script.py
python scripts/strip-tag-lines.py --all
```

The utility only edits tracked text files in the current worktree, preserves
line endings and is idempotent. It removes standalone contribution-tag lines
in comment forms, plain text and bold PR form. It skips JSON, JSONL, lock files,
binary or non-UTF-8 files, symlinks and paths through symlinked directories.
It does not commit changes. Review its diff before committing with trailers.
Runtime response formatting and existing tag generators are separate migration
work; this change replaces repository policy and CI validation.
<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]] -->
<!-- [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:33 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 17:42 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 21:57 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:30 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:57 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:12 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:23 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:37 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 07:47 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:00 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:09 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:20 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:32 UTC | END]] -->
