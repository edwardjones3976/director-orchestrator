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
two parents, committer `GitHub <noreply@github.com>`, and the standard subject
`Merge pull request #N from <source>`. These web-flow merges integrate content
checked on the PR. This metadata is not authentication. Local merges, including
conflict resolutions, and other merge forms still require trailers when selected.

No date cutoff is needed for the transition: once merged, commits checked under
the old in-file tag rule are reachable from main and excluded, as is the main
push's merge commit. Consequently, a main push with an up-to-date default-branch
ref has no new commits to validate; trailer enforcement happens before merge
on branch/PR checks. An unmerged legacy branch still needs trailers on its
selected commits. This check is not a post-merge audit of main history.

On pull-request events it also requires exactly one `Sole author:` header in
the event-file PR body. Push events on main (and other refs) check trailers only.
Unreadable revisions or event files fail closed. The checker does not read,
require or validate in-file tags: adding or removing them never fails this
check. The policy still prohibits writing new tags.

```powershell
$env:PYTHONPATH = "src"
python scripts/check_contribution_tags.py --base <base-sha> --head <head-sha>
python scripts/check_contribution_tags.py --base <base-sha> --head <head-sha> --github-event <event-file>
```

Use [the submission template](templates/contribution-submission.md) or the
repository PR template. Keep commit trailers when squashing or otherwise
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
