<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | BEGIN]] -->
# Edge Oracle isolated operations

Edge Oracle is a research-only sports-market intelligence lane inside
`edwardjones3976/edge_engine`. It is not funded-wager authority and must not start
Edge Engine schedulers, fetch operational odds, generate live picks, settle outcomes,
or mutate bankroll state.

## Isolated Windows profile

The populated `.env.edge-oracle` file is ignored. The committed
`.env.edge-oracle.example` defines a distinct Compose project, loopback ports, SQLite
database, n8n volumes, Slack routing, allowlists, budgets, and merge policy. Never copy
Uppercut's internal, callback, n8n, or database secrets into this profile.

From PowerShell, stage the profile closed and start the isolated API while dedicated
Slack credentials are still being provisioned:

```powershell
.\scripts\windows-edge-oracle-live-validation.ps1 `
  -ApiOnly `
  -SeedProviderCredentialsFromPrimaryEnv
```

The reusable bootstrap generates new internal secrets when it creates the profile. It
does not print them. Before Docker starts, it verifies the selected profile identity,
rejects collisions with `.env` and `.env.uppercut`, validates Compose, and inspects the
isolated task store for recoverable work. API-only activation starts the orchestrator
and its isolated SQLite volume but keeps Slack and n8n stopped. Paid execution, usage
checks, and the merge gate remain closed.

Once a dedicated Slack app and channel are configured in `.env.edge-oracle`, omit
`-ApiOnly` to start the complete isolated stack with paid execution still disabled.

After dedicated Slack and all three provider credentials are configured, an explicitly
supervised provider validation may use `-EnablePaidRuns`. The script starts only the API,
keeps Slack intake stopped, requests one bounded usage-check cycle, and starts Slack last.
It waits for exact-cycle `ok`/`prompted` records for every `scheduled_providers` entry and
also requires each `cooling_down_providers` entry to have a latest `ok`/`prompted` record
inside the shared 15-minute window. A `not_due` response is accepted only after that same
recent-success check against `/v1/usage-checks/latest`; recent `error` or `skipped`
results fail closed and trigger rollback. Any hard failure closes paid and usage switches
and leaves Slack stopped.

## Deterministic exact-SHA merge gate

The Oracle merge gate is a local CLI, not a model tool or public API. Its GitHub
credential and review-authentication secret are read only by deterministic code; never
include their values in a model session, work packet, review prompt, callback, log,
issue, or artifact.
The authenticated Claude callback adapter and authenticated Cursor run poller canonicalize
and HMAC-sign the final artifact only after matching the stored task, assignment sequence,
provider, repository, base ref, review run, and the exact base/head SHAs embedded in the
assignment. Provider JSON must use `artifact_type=edge_oracle_exact_sha_review/v1`, set
`reviewer_provider` to the actual provider, and omit `signature`; model-authored JSON alone
has no merge authority. The reviewer may be OpenAI, Claude, Cursor, or Grok, but it must not be the
implementation provider. OpenAI acceptance runs before the deterministic merge service.
The review assignment must depend on exactly one accepted implementation outcome; the
stored transitive dependency closure, rather than a model assertion, establishes every
provider that materially authored the work and prevents self-review relabeling. Candidate
metadata is accepted only from a draft-PR-capable assignment whose exact scope was approved.
The claimed implementation actor must also belong to the logical producer's configured
GitHub-actor allowlist and must not belong to the reviewer. Configure lowercase logins in
`ORACLE_AUTOMERGE_CLAUDE_ACTORS`, `ORACLE_AUTOMERGE_CURSOR_ACTORS`, and, when used as an
implementation provider, `ORACLE_AUTOMERGE_GEMINI_ACTORS`.

The committed profile keeps both controls closed:

```dotenv
ORACLE_AUTOMERGE_ENABLED=false
ORACLE_AUTOMERGE_KILL_SWITCH=true
```

Use a GitHub App installed only on `edwardjones3976/edge_engine`. Grant exactly:

- **Contents: read and write** for the exact-SHA merge request;
- **Pull requests: read and write** for PR metadata, changed files, review threads, and the
  deterministic draft-to-ready transition after a complete exact-SHA preflight;
- **Checks: read-only** for the required check-run evidence; and
- **Metadata: read-only**, which GitHub adds automatically.

No webhook subscription or event delivery is required. Store its App ID, installation
ID, and base64-encoded private key only in ignored `.env.edge-oracle` fields named
`ORACLE_AUTOMERGE_GITHUB_APP_ID`, `ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID`, and
`ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64`. The Director signs a short-lived app JWT,
mints an installation token only when needed, caches it for less than its expiry, and
refreshes once after an authentication failure. A fine-grained personal access token is
not sufficient because personal-repository fine-grained tokens do not expose the Checks
permission required by this gate. `ORACLE_AUTOMERGE_GITHUB_TOKEN` remains a legacy
static-token fallback; configuring it together with any GitHub App field fails closed.

Branch protection and required checks remain authoritative. Installing the app and
enabling the feature do not authorize executing a live merge.

Perplexity assignments are separately owner-gated. The Slack approval prompt shows the
exact bounded `public_research_query`, and the stored approval is bound to a SHA-256 digest
of that complete assignment. Any query or scope mutation invalidates the approval and asks
again. Content filters remain defense in depth; they are not treated as proof that arbitrary
model-generated text is public.

If a provider dispatch times out after transmission, ordinary approval cannot retry it.
Reconcile the exact pending assignment through
`POST /v1/tasks/{task_id}/dispatch-reconciliation`: record either an existing provider run
(`created`, with only its run ID and required session ID) or provider-dashboard evidence that
no run was created (`not_created`). A caller cannot supply status, result, or URL as provider
evidence: Cursor and Gemini are polled through their authenticated APIs, while Claude must
complete through its authenticated callback. Synchronous Perplexity searches cannot be adopted
by run ID. Only `not_created` permits one new dispatch. Initial and revision requests use this
same rule, preventing ambiguous paid work from being duplicated by recovery or generic approval.
Owner-supplied manual recovery text remains operational evidence only and cannot authorize an
exact-SHA review handoff or merge.

An immutable OpenAI, Claude, Cursor, or Grok review artifact must validate as
`OracleReviewArtifact`. A secret-free shape is available in
`docs/oracle-review-artifact.example.json`. When rollout gates are separately accepted:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe scripts\oracle_merge_gate.py `
  docs\oracle-review-artifact.example.json `
  --database .\data\edge-oracle-merge-audit.sqlite3
```

The example intentionally has a zero signature and cannot merge a real PR. For a real
artifact, the gate binds the project, repository, PR, branch, exact base and head SHAs,
head repository, implementation actor, assignment, and authenticated provider review run. It then
refetches GitHub state and fails closed on:

- disabled or killed policy;
- an invalid HMAC, non-`ACCEPT` review, self-review, provider mismatch, or reused artifact, assignment, or run;
- head/base SHA, PR, repository, project, base, branch, fork, or author mismatch;
- an explicit conflict, incomplete thread enumeration, or blocking review thread; an
  otherwise eligible draft is marked ready by deterministic code and then fully refetched
  and revalidated before merge;
- missing, pending, failed, ambiguous, wrong-producer, or wrong-SHA checks;
- any current or previous rename path outside `oracle/**`, `docs/oracle/**`, or
  `tests/oracle/**`, or any file whose complete scan evidence cannot be proven.
  Every snapshot binds changed-file scan evidence from the immutable compare
  diff for the snapshot's exact 40-hex base and head SHAs
  (`GET /repos/{repo}/compare/{base}...{head}` with
  `Accept: application/vnd.github.diff` and `Accept-Encoding: identity`),
  not from number-addressed `/pulls/{number}/files` patch bodies. Those REST
  patches are not exact-SHA evidence and can race A-to-B-to-A. The pull is
  re-read after that fetch; any drift in title, body, draft, state, merged flag,
  base/head refs and SHAs, head repository, author, changed-file count, or file
  set fails closed. Compare evidence is accepted only on HTTP 200; a 206 prefix
  is terminal incomplete evidence. Each streamed chunk is checked against the
  remaining size cap before it is copied. The compare response `Content-Type`
  base media type must be `application/vnd.github.diff`.
  A charset parameter is omitted, the unquoted token `utf-8`, or a matched
  double-quoted `"utf-8"` only, case-insensitively; single quotes, mismatched
  quotes, escapes, and duplicate or non-UTF-8 charset declarations fail closed. A
  non-identity `Content-Encoding` fails closed so `Content-Length` is the
  uncompressed body. HTTP 429 and 403 with explicit rate-limit evidence
  (`Retry-After` or `X-RateLimit-Remaining: 0`) persist an absolute UTC
  not-before deadline from bounded `Retry-After` seconds / `X-RateLimit-Reset`
  on the live `oracle_merge_attempts` row via an idempotent PRAGMA/ALTER
  migration that is safe for concurrent initializers.   Every pre-mutation GitHub
  read — pull, files, exact-SHA compare, check-runs, the review-thread GraphQL
  query, and pre-mutation GETs inside `mark_ready`/`merge` — raises
  `OracleRateLimitError` and takes that durable hold. GitHub can return GraphQL
  primary and secondary rate limits as HTTP 200 with top-level `errors` whose
  `type` or `extensions.code` is `RATE_LIMITED` or `RATE_LIMIT`, and/or with
  exhausted `X-RateLimit-Remaining: 0` or `Retry-After`. Those documented
  GraphQL 200 rate limits also raise `OracleRateLimitError` with the bounded
  persisted deadline. Other GraphQL errors fail closed as incomplete
  review-thread evidence with sanitized text; GitHub messages and payloads are
  never copied into the exception, reasons, or audit. A later `process()`
  returns a nonterminal deferred ERROR without rearming or calling GitHub until
  that deadline, including zero compare calls. Concurrent `release()` calls
  serialize the deadline read so a later window cannot be overwritten by an
  earlier one. Mutation responses (`PUT /merge` and the ready-for-review
  GraphQL mutation) stay fail-closed even on 429. A 429 or limited 403 without
  a usable value uses a
  conservative fallback window. Header values are not echoed. The generic
  two-read loop is not spent and the provider request does not sleep. HTTP 408,
  compare 404, and an exhausted compare 401 (the first static-token 401, or a
  GitHub App 401 after one refresh) stay on the bounded read-retry path as
  `RuntimeError` so the artifact is released; response bodies are not copied
  into the error. Ordinary permission 403, 406, 206/204/3xx, and malformed
  bodies remain deterministic `OracleDiffError`. An unparseable nonempty stored
  `retry_not_before`, including lexically high garbage and low garbage such as
  `"0"`, is replaced inside the reservation transaction with a
  conservative UTC now+60s deadline via compare-and-swap, audited without the
  raw value, and held as the same deferred nonterminal ERROR without calling
  GitHub. A valid deadline is never rewritten or moved backward.
  `changed_files` must be present as
  a non-bool integer `>= 0` and equal the file list length. Missing REST
  filename/status, a list of non-object REST file entries, REST
  additions/deletions that are missing, bool, string, fractional, or negative,
  oversized hunk
  counts, and other malformed evidence raise
  `OracleDiffError` with no raw content. Incomplete file enumeration and
  non-40-hex pull SHAs are terminal evidence failures. Content-bearing sections
  require exactly one valid `index` line whose object IDs are lowercase SHA-1
  hex (each side independently 4–40 characters); edited renames also require
  canonical similarity metadata. Pull head/base identity remains exactly 40 hex.
  Unavailable, oversized, truncated, binary, combined, quoted, mode-only, or
  mismatched diffs fail closed. Bound compare additions and deletions must
  equal the REST counts. `patch=None` remains incomplete when no bound evidence
  is supplied;
- deterministic serious-safety signals or committed credential material;
- GitHub or branch-protection refusal.

Immediately before the merge mutation, the gate takes one complete final snapshot and
runs the full `OracleMergePolicy` evaluation. A changed review thread, review decision,
check, title/body safety signal, path/file evidence, or SHA blocks and consumes the
artifact. Unresolved `mergeable: null` on that mutation-boundary read is terminal; the
gate does not start another mergeability poll. A lagging REST `draft: true` after a
gate-owned ready-for-review transition is not a verdict: it stays on the same bounded
stale-draft wait already used after `mark_ready`, and merge does not run until a fresh
complete snapshot is non-draft and fully eligible. An unowned draft flag on that read
remains terminal. Bounded read-failure retries and rate-limit holds still apply to that
snapshot. The gateway then re-GETs the pull and rechecks base and head SHAs
as defense in depth before `PUT /merge`. The merge request includes the reviewed SHA, so
GitHub rejects a head advance even after the final read. Residual platform limitation:
GitHub's REST merge endpoint binds the expected head SHA but has no atomic expected-base
parameter, so repository rules or a merge queue are required for mutation-boundary
enforcement of the base. SQLite reserves each artifact, assignment, and provider review run
once, makes
retry delivery idempotent after a terminal result, and records an append-only audit with
the artifact digest, assignment/review identifiers, changed paths, check run/suite IDs,
decision, merge commit, actor, and UTC timestamp.

### Unresolved and unconverged GitHub state

GitHub applies the gate's own mutations and recalculates derived fields asynchronously, so
a read taken immediately afterwards can lag. Because every artifact, assignment, and review
run is reservable exactly once, reading lag as a verdict would permanently spend a correctly
reviewed change. The gate classifies the state instead:

- **Transient.** Three conditions are transient, and each has its own bounded allowance so
  that none can exhaust another's:
  - `mergeable: null` — five unresolved reads;
  - a read that raised and produced no snapshot — two failed reads, because a read that
    returned nothing proves nothing about the pull request;
  - `draft: true` on a pull request this gate just transitioned, after `mark_ready` already
    confirmed `isDraft: false` through GraphQL — three stale-draft reads. A draft flag is
    only ever forgiven on a transition the gate performed itself.

  Backoff is one second doubling to an eight-second cap. Each read spends the allowance for
  every transient condition it exhibits, so overlapping conditions shorten the wait. The
  poll performs at most eight reads over about thirty-nine seconds. The first read and the
  complete final snapshot immediately before merge each carry the same two-read failure
  allowance. After a gate-owned ready transition, that final snapshot may continue the same
  three-read stale-draft wait instead of consuming the artifact; merge still waits for a
  non-draft eligible snapshot. Every successful read
  revalidates the
  repository, pull-request identity, open and unmerged state, expected base SHA, exact head
  SHA, checks, review threads, paths, and safety signals before the wait continues. The
  first read of the pull request carries the same read-failure allowance as every later one.
- **Terminal.** An explicit `mergeable=false`, a draft flag on a pull request the gate did
  **not** transition, any drift observed during those re-reads (head or base advance, close,
  merge, a new blocking thread or non-green check), and any serious-safety signal record a
  terminal decision and consume the review artifact. Each condition is classified from the
  snapshot itself, so a `false` conflict is terminal on the read that reports it and can
  never be forgiven as an unresolved `null`.
- **Recoverable.** Exhausting any allowance — five unresolved reads, two failed reads, or
  three stale-draft reads — fails closed as `ERROR` with a reason containing
  `remains retryable` that names which allowance ran out. The attempt keeps its artifact,
  assignment, and review-run reservation, so no other review can claim them, but it records
  no terminal result: redelivering the identical artifact resumes the gate. The audit shows
  `merge_gate_recoverable` and then `review_artifact_retried`. Nothing was merged, so the
  only operator action is to re-run the gate with the same artifact once the transient
  condition clears.
- **Ambiguous.** The merge call itself is never retried. If it raises or is refused, the
  result is a terminal `ERROR`, and a redelivery returns that recorded result without
  calling GitHub again.

The initial Edge Engine required check is configured as `test`, matching its pull-request
workflow. A baseline failure is not inherited or waived; the exact Oracle head must be
genuinely green.

Cursor dispatch retries only explicit HTTP 429 pre-creation responses, including its
`resource_exhausted` GitHub-installation lookup response, using a bounded provider
`Retry-After` value. A separate one-shot retry applies when create or follow-up fails
because a Cursor `cursor/...` working branch was deleted after merge: create retries
once against the first `ALLOWED_STARTING_REFS` value, and follow-up starts a new agent
from that durable ref. It does not retry timeouts, connection failures, generic 5xx
responses, or a missing durable ref such as `main`, because an agent may already have
been created and a blind retry could duplicate paid work. Tune only
`CURSOR_DISPATCH_MAX_ATTEMPTS` and `CURSOR_DISPATCH_MAX_RETRY_DELAY_SECONDS`;
exhaustion still fails closed for operator recovery.

## Rollout boundary

Keep the gate disabled through unit tests, fake-GitHub integration, historical no-write
shadow decisions, and an owner-reviewed documentation canary. A serious-safety result is
not overridable by a model. Existing Director rules still prohibit an agent from merging
its own PR; production deployment, credential installation, permission expansion, and
live merge activation require their separately recorded owner gate.
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 23:18 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:25 UTC | END]] -->
