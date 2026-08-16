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

From PowerShell, stage the profile closed and start the isolated stack:

```powershell
.\scripts\windows-edge-oracle-live-validation.ps1 `
  -SeedProviderCredentialsFromPrimaryEnv
```

The reusable bootstrap generates new internal secrets when it creates the profile. It
does not print them. Before Docker starts, it verifies the selected profile identity,
rejects collisions with `.env` and `.env.uppercut`, validates Compose, and inspects the
isolated task store for recoverable work. Paid execution and usage checks remain false.

After dedicated Slack and all three provider credentials are configured, an explicitly
supervised provider validation may use `-EnablePaidRuns`. The script starts only the API,
keeps Slack intake stopped, waits for fresh OpenAI, Claude, and Cursor checks, and starts
Slack last. Any failure closes paid and usage switches and leaves Slack stopped.

## Deterministic exact-SHA merge gate

The Oracle merge gate is a local CLI, not a model tool or public API. Its GitHub token and
review-authentication secret are read only by deterministic code from
`ORACLE_AUTOMERGE_GITHUB_TOKEN` and `ORACLE_AUTOMERGE_REVIEW_SECRET`; never include either
value in a model session, work packet, review prompt, callback, log, issue, or artifact.
The authenticated Claude callback adapter canonicalizes and HMAC-signs the final artifact
only after matching the stored task, assignment sequence, repository, base ref, review run,
and the exact base/head SHAs embedded in the assignment. Claude callback JSON must use
`artifact_type=edge_oracle_exact_sha_review/v1` and must omit `signature`; model-authored
JSON alone has no merge authority.

The committed profile keeps both controls closed:

```dotenv
ORACLE_AUTOMERGE_ENABLED=false
ORACLE_AUTOMERGE_KILL_SWITCH=true
```

Enabling the feature requires a repository-scoped credential with the minimum contents
and pull-request permissions needed to read PR evidence and merge. Branch protection and
required checks remain authoritative. Landing the implementation does not authorize
installing that credential, enabling the feature, deploying it, or executing a live
merge.

An immutable Claude review artifact must validate as
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
head repository, implementation actor, assignment, and Claude review run. It then
refetches GitHub state and fails closed on:

- disabled or killed policy;
- an invalid HMAC, non-`ACCEPT` review, self-review, or reused artifact, assignment, or run;
- head/base SHA, PR, repository, project, base, branch, fork, or author mismatch;
- a draft, conflict, unresolved mergeability, incomplete thread enumeration, or blocking
  review thread;
- missing, pending, failed, ambiguous, wrong-producer, or wrong-SHA checks;
- any current or previous rename path outside `oracle/**`, `docs/oracle/**`, or
  `tests/oracle/**`, or any file whose complete patch is unavailable;
- deterministic serious-safety signals or committed credential material;
- GitHub or branch-protection refusal.

The merge request includes the reviewed SHA, so a head advance is rejected by GitHub even
after the final read. SQLite reserves each artifact, assignment, and Claude review run
once, makes
retry delivery idempotent after a terminal result, and records an append-only audit with
the artifact digest, assignment/review identifiers, changed paths, check run/suite IDs,
decision, merge commit, actor, and UTC timestamp.

The initial Edge Engine required check is configured as `test`, matching its pull-request
workflow. A baseline failure is not inherited or waived; the exact Oracle head must be
genuinely green.

## Rollout boundary

Keep the gate disabled through unit tests, fake-GitHub integration, historical no-write
shadow decisions, and an owner-reviewed documentation canary. A serious-safety result is
not overridable by a model. Existing Director rules still prohibit an agent from merging
its own PR; production deployment, credential installation, permission expansion, and
live merge activation require their separately recorded owner gate.
