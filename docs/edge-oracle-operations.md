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
keeps Slack intake stopped, waits for fresh OpenAI, Claude, and Cursor checks, and starts
Slack last. Any failure closes paid and usage switches and leaves Slack stopped.

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
has no merge authority. The reviewer may be Claude or Cursor, but it must not be the
implementation provider. OpenAI acceptance runs before the deterministic merge service.
The review assignment must depend on exactly one accepted implementation outcome; the
stored dependency, rather than a model assertion, establishes which provider authored the
work and prevents self-review relabeling.

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

An immutable Claude or Cursor review artifact must validate as
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
- a conflict, unresolved mergeability, incomplete thread enumeration, or blocking review
  thread; an otherwise eligible draft is marked ready by deterministic code and then fully
  refetched and revalidated before merge;
- missing, pending, failed, ambiguous, wrong-producer, or wrong-SHA checks;
- any current or previous rename path outside `oracle/**`, `docs/oracle/**`, or
  `tests/oracle/**`, or any file whose complete patch is unavailable;
- deterministic serious-safety signals or committed credential material;
- GitHub or branch-protection refusal.

The merge request includes the reviewed SHA, so a head advance is rejected by GitHub even
after the final read. SQLite reserves each artifact, assignment, and provider review run
once, makes
retry delivery idempotent after a terminal result, and records an append-only audit with
the artifact digest, assignment/review identifiers, changed paths, check run/suite IDs,
decision, merge commit, actor, and UTC timestamp.

The initial Edge Engine required check is configured as `test`, matching its pull-request
workflow. A baseline failure is not inherited or waived; the exact Oracle head must be
genuinely green.

Cursor dispatch retries only explicit HTTP 429 pre-creation responses, including its
`resource_exhausted` GitHub-installation lookup response, using a bounded provider
`Retry-After` value. It does not retry timeouts, connection failures, or generic 5xx
responses because an agent may already have been created and a blind retry could duplicate
paid work. Tune only `CURSOR_DISPATCH_MAX_ATTEMPTS` and
`CURSOR_DISPATCH_MAX_RETRY_DELAY_SECONDS`; exhaustion still fails closed for operator
recovery.

## Rollout boundary

Keep the gate disabled through unit tests, fake-GitHub integration, historical no-write
shadow decisions, and an owner-reviewed documentation canary. A serious-safety result is
not overridable by a model. Existing Director rules still prohibit an agent from merging
its own PR; production deployment, credential installation, permission expansion, and
live merge activation require their separately recorded owner gate.
