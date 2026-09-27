<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]] -->
# Site coordination

This implements the work-board protocol described in the owner packet for
issue #299, sections 1 and 3. The issue itself was unavailable during authoring;
the packet is the source for this description.

Claim an authorized assignment with a draft PR on its isolated branch. Include
a standalone `Session: <name>` line in the PR body and describe the intended
file scope. Keep the draft and scope current as work changes. The board reads
actual changed files, so a claim covering files not yet in the diff also needs
the foreman's coordination; a draft PR is not a file lock.
Renames claim both the previous and current paths.

Before editing, check each intended repository-relative file path:

```powershell
python scripts/site-board.py --check src/example.py docs/example.md
```

Exit 0 means no open PR currently touches those exact paths; exit 1 lists
conflicts; exit 2 means collection failed and must not be treated as clear.
Use exact file paths, not directory names or globs. Paths are case-sensitive;
Windows separators and a leading `./` are accepted. Existing claims, including
your own PR, are listed. Resolve overlapping ownership with the foreman before
editing; do not take over another writer's assignment. A clear snapshot cannot
prevent a concurrent claim.

The acting Director serves as foreman for this work order: reconcile claims,
sequence overlapping edits, and route work and independent review within the
owner's existing authority. The board is coordination evidence, not assignment,
acceptance, merge, deployment, or spending authority. All deterministic gates
and the one non-author exact-head reviewer requirement remain in force.

```powershell
python scripts/site-board.py
python scripts/site-board.py --post 299
```

The first command prints all open PRs, draft flags, branches, short head SHAs,
sessions, changed files, the latest Actions run for each exact head, and shared
files under `OVERLAPS`. CI means the latest run of any workflow for that SHA,
not an aggregate required-check verdict. Missing runs or sessions are `unknown`.
Queue position is the latest run's ordinal among repository-wide `queued` runs,
sorted by creation time then run ID; GitHub does not promise this execution
order. A nonqueued or absent run has position `n/a`; a queued run missing from
the earlier queue snapshot has position `unknown`.

With posting authority, `--post` updates the issue comment containing the exact
standalone marker `<!-- site-board -->` via PATCH, or creates one via POST.
Only comments authored by the authenticated GitHub identity qualify; foreign
markers are ignored. Multiple qualifying marker comments fail without a write.
Untrusted title, branch, path and session mentions are neutralized before posting.
Use one foreman as the posting
writer: concurrent first-time posters could otherwise create duplicate comments.
Writes are never retried automatically, including ambiguous timeouts. Inspect
the issue before repeating a failed post. No posting was performed during this
implementation.

The script uses Python's standard library and authenticated `gh`, targeting
`edwardjones3976/director-orchestrator`. Each invocation caches reads. For N open
PRs and H distinct heads, a board uses one list call, N file-detail calls, H
latest-run calls, and at most 10 queue-page calls. Check mode uses only 1 + N
calls, plus at most 10 REST file-page calls and one head/state check per PR with
a rename. The CLI file list exposes `changeType` but no previous path; REST
supplies `previous_filename`. Posting adds one cached identity read, at most
10 comment-page calls and one write. Each process
call has a 60-second timeout and no automatic retry. Open PRs are capped at
1,000; paginated reads stop after 10 pages of 100. Hitting a bound, truncated
file results, or a changed head/state during collection fails instead of
reporting a partial board as clear. `gh` may make multiple HTTP requests inside
a single CLI call. This is a per-invocation snapshot, not a background monitor.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | END]] -->
