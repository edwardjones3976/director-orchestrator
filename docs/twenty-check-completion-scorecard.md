<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:36 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:34 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:53 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:10 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:17 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:55 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:09 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:46 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:53 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:18 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:39 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:22 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:03 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:17 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:39 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:50 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:29 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:12 UTC | BEGIN]] -->
<!-- [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:50 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]] -->
# Twenty checks for a completed Director

This scorecard turns the owner's good-run contract into twenty fixed outcomes.
It is a progress report, not an approval mechanism. The required outcome remains
an actual owner request through Slack, a correct result in the same thread in
under 60 seconds, and less than USD 1 of attributable actual charges. Simulated
runs qualify orchestration only; the project is not complete until paid primary
execution is active and a real run meets that outcome.

Every check is mandatory. A verified PASS contributes one point; FAIL and
UNKNOWN contribute zero. Thus `points = sum(PASS)` and `percent = 5 * points`.
Twenty points would establish only this diagnostic contract. Existing exact-SHA,
independence, authorization, security, budget and activation gates still decide
whether any operation is permitted. The report always says
`activation_authorized: false`; no runtime setting is changed by rendering it.

| # | Required outcome | Evidence needed |
|---|---|---|
| 01 | Correct project route and channel | Selected deployment's loopback Director and n8n ports, --channel, correlated route |
| 02 | One active Slack gateway | All containers and host listeners using the same app identity |
| 03 | Correct active intake | Separate 4xx refusal, 5xx-after-reservation and relay transport-failure drills |
| 04 | Status really delivered | Saved successful director-status execution correlated with Slack readback |
| 05 | Primary owns intake | Database-derived ownership, disabled legacy intake, positive fencing generation |
| 06 | Durable restart | Approval task resumed across the current process start without redispatch |
| 07 | Reconciliation clear | In-image read-only consistency command bound to the running generation |
| 08 | Authenticated owner approval | Original scoped instruction, delivered prompt and signed decision; the owner's approval command receipt re-read and matched |
| 09 | Durable final reply | duplicate-input and ambiguous-post drills plus one final result |
| 10 | Ten offline Slack runs | Ten consecutive actual-owner --trial threads (the only top-level owner posts from the first to the last), the committed structural rubric (pipeline proof, not answer correctness) and measured time, zero operational provider calls from task evidence |
| 11 | Every runtime lane used is its real product | Every lane the paid run actually used is attested as its real product (OpenAI/Codex Responses, xAI Grok, Claude) with no substitution (amended by owner decision 2026-09-23; Grok Bot and Antigravity team mode are follow-ups) |
| 12 | Fresh activation captures | Unexpired verified captures bound to provider_bindings_sha256 |
| 13 | Rolling twenty admissions | Authoritative assignment history and recomputed reported resource-bound compliance |
| 14 | Bounded execution | Workload-ledger window: bounds set and a hard USD cap the operator attests (operator-attested, not cryptographically verified; owner ruling 2026-09-25) |
| 15 | Monetary control and usage | Enrolled signed daily cap and holds, usage-monitor accounting, settled request-correlated meter rows |
| 16 | Executed exact-head CI | The six `ci.yml` jobs executed at the exact head on hosted or registered self-hosted runners; only when none executed, a signed local-replica receipt against a main ancestor (amended by owner decision 2026-09-23) |
| 17 | Independent ACCEPT | Adopted owner-local acceptance for the candidate with reviewer ≠ author |
| 18 | Complete Security scan | Sealed complete coverage and disposition bound to exact candidate |
| 19 | Paid primary active | Paid enabled, primary runtime, effect executor and reconciliation worker active |
| 20 | Real paid result | Actual-owner Slack input, independent correctness, one reply, wall time <60s, actual charges <$1 from settled meter rows correlated by request id |

## Run it

From the repository, with its Python environment and `PYTHONPATH=src`:

```powershell
$env:PYTHONPATH = 'src'
python scripts/director-scorecard.py --candidate-sha <full-40-character-sha> --evidence-dir <explicit-evidence-folder> --output-dir <report-folder>
```

The default is offline. It produces `completion-scorecard.json` and
`completion-scorecard.md`, with UNKNOWN for facts it has not observed (0/20).

A full live collection names every source explicitly:

```powershell
python scripts/director-scorecard.py --candidate-sha <sha> --output-dir <report-folder> `
  --live --deployment edge-engine-director --channel <C…> --gateway <gateway-container-name> `
  --github --ledger <journal-path> --security-scan <scan-uuid> `
  --drill-thread refusal-4xx=<thread-ts> --drill-thread reserved-5xx=<thread-ts> `
  --drill-thread transport-failure=<thread-ts> `
  --drill-thread duplicate-input=<thread-ts> --drill-thread ambiguous-post=<thread-ts> `
  --approval-task DIR-LG-<task> `
  --trial <thread-ts> (ten consecutive owner posts) --rubric <structural-rubric.json> `
  --paid-thread <thread-ts> --paid-rubric <paid-rubric.json> `
  [--ci-replica-receipt <signed-receipt.json>]
```

`--live` requires `--deployment <compose project>`. There is no default stack:
without `--deployment` nothing is inspected and check 19 reads UNKNOWN. The
expected Slack channel comes only from `--channel`; no channel ID is committed
to the repository, and a deployment whose configured channel differs is not
read at all. Every other collector flag requires `--live`;
`--ci-replica-receipt` also requires `--github`, `--rubric` requires `--trial`
and `--paid-rubric` requires `--paid-thread`. A missing selection leaves its
checks UNKNOWN. The Python API accepts the same selections as keyword
arguments of `collect()`; its `topology=False` option still disables topology
collection.

There are no provider calls, Slack writes, retries, deployments or imported
credentials. Slack and Director reads are bounded GETs through the offline trial
collector's `Collector`, plus the thread-binding reads and one paginated
`conversations.history` read of `--channel` under the same deadline and byte
bound, using credentials read in memory from the selected Director container.

The candidate SHA and observed runtime image/commit are separate. A merged
candidate's CI cannot prove a different running image passed that CI. Runtime
facts are tied to the inspected image's immutable ID and commit label, within
the time bindings described under [Execution-binding limits](#execution-binding-limits).

### Deployment profile

`--deployment` names a Compose project such as `edge-engine-director`. The
Director is the one running container carrying the labels
`com.docker.compose.project=<project>` and
`com.docker.compose.service=orchestrator`; zero or several matches stay
UNKNOWN. The inspected container must be running, carry the
`org.director.candidate.commit` label and an immutable image ID, and publish
`8080/tcp` on exactly one `127.0.0.1` binding. Its host port (for example
18082) becomes the health and API base; `APP_ENV` (default `local`) is the
environment `/health` must report. n8n and its database are the project's
`n8n` and `n8n-db` services, found the same way, and every `docker exec` targets
a container ID, never a name.

### Per-check sources

- 01, 02: Docker running inventory for `--gateway`, the gateway's channel and
  relay, the published n8n intake version and the n8n upstream `/health`. 01 is
  UNKNOWN without `--channel`, FAIL when the gateway belongs to another Compose
  project, and UNKNOWN when the gateway has no Compose labels. 01 also requires,
  from `docker inspect` alone, that the gateway and n8n share a Docker network
  (same network ID) on which n8n's aliases or DNS names include the relay
  host (on every network the source is attached to, no other container may
  answer to that name; an `ExtraHosts` entry for the name on the source FAILs
  the hop, and host networking, custom `Dns`/`DnsSearch` or a mounted
  `/etc/hosts` or `/etc/resolv.conf` leave it unproven), that the active intake `Create Director Task` node
  forwards exactly as `n8n/workflows/director-intake.json` (the whole active
  workflow is compared with that file: every node's type, typeVersion,
  parameters, disabled flag and credential types, the node set and the
  connection graph; only node ids, positions, `webhookId` and credential ids
  may differ; the gateway must
  hold the Director's bot token, owner, channel, bearer and intake signing
  secret `SLACK_GATEWAY_SECRET`, compared in constant time, since n8n forwards
  its Authorization and signature headers unchanged; a test derives the
  gateway's secret reads from `slack_bot.py` so a new one cannot be missed;
  no node of the reference workflow may be disabled; Authorization and
  the three `X-Director-Intake-*` headers, the signed body, timeout, no
  redirects, full response, and the same connections), and that n8n's own `DB_*` settings name the queried
  `n8n-db` (PostgreSQL, database and user `n8n`, port 5432, a host only that
  container answers to, and the server's `POSTGRES_USER`, `POSTGRES_DB` and
  `POSTGRES_PASSWORD` equal to n8n's, the password compared in memory in
  constant time) before its workflows or executions are read; setting
  values are never reported, and likewise that n8n and the selected Director share a network on
  which the Director answers to `orchestrator` (the host n8n's health probe and
  intake request use). On that network no other container may answer to the
  same name. FAIL when a hop has no such network or the name has another
  owner, UNKNOWN when a container shows no network attachments. No probe runs
  inside the gateway.
- 03, 09: `--drill-thread` Slack threads, the bot's replies, the Director's
  `GET /v1/slack-thread-bindings` and the task readback (see below). A receipt
  or `BLOCKED` notice counts only as a reply's leading header (after the
  gateway's contribution BEGIN tag), at most one receipt per reply; the words
  quoted in a body count as neither, and the 4xx, 5xx and transport drills need
  exactly one `*DIRECTOR — BLOCKED*` reply. A drill
  whose thread holds a bot reply Slack reports as edited or with a subtype is
  UNKNOWN before anything in it is graded, and such a reply's text is never
  parsed: the bot never edits, so its receipts and `BLOCKED` text are no longer
  what was posted.
- 04: the newest scheduled `Director Status` run of any outcome is graded
  first (a failed one FAILs 04, an unfinished one leaves it UNKNOWN); then,
  only when, from `docker inspect`, the deployment's n8n reaches the
  selected Director (and no other container) as `orchestrator`, the newest
  saved successful scheduled `Director Status` execution of a workflow that is
  active now, whose active version is the `Read Health` GET of
  `http://orchestrator:8080/health` -> `Diff Readiness` -> one Slack
  `Post Status` route, the whole active version equal to
  `n8n/workflows/director-status.json` under the same rule (the Post Status
  channel is bound to `--channel`), whose `Diff Readiness` code is exactly the repository's
  and whose `Post Status` sends `$json.text` to the project channel through a
  Slack API credential, with no node of that workflow disabled, which ran its active version (the execution's recorded version
  where n8n stores one, otherwise a start after both that version's history
  entry and the workflow's last update; UNKNOWN when neither shows it), in the
  last 72 hours, read in a read-only n8n database transaction, correlated with
  the Slack readback of the message it posted to `--channel`. An execution that
  started before the running Director container was created is UNKNOWN, and so
  is a posted message Slack reports as edited or with a subtype. The status
  message must be posted by the authenticated Director bot user.
- 02: the Slack app ID each running container's app-level token names
  (`xapp-<n>-<APP_ID>-...`), so a rotated or second token of the same app is
  still a duplicate gateway; a token whose app cannot be read is unclassified
  and leaves 02 UNKNOWN.
- 05, 19: the Director's `/health`; 05 also requires `writer_lease_ready`, the
  database-derived live writer lease, not only the persisted owner row.
- 06, 08: the `--approval-task` readback, its deliveries and its Slack thread;
  08 also the task's `approval_resume` command receipts from `consistency`.
  08 needs a complete thread readback, each prompt's complete blocks (every
  field, accessory, element, confirm dialog, URL, value and style; only Slack's
  own `block_id` and plain-text `emoji` flag ignored) equal to the
  Director's own rendering (`primary_slack_delivery.render_notification`) of
  the persisted application record `consistency` returns (task record,
  execution mode, effect grant) for that approval's kind, request and
  projection digests (the complete proposed scope and button value), each `approval_resume` receipt joined to
  its durable command authorization (same command digest and writer fence,
  gateway proof version `v1`, the owner as actor), and each prompt's approve button must
  carry exactly this task, projection, request digest and kind while its
  visible text names that kind and decision scope (and the plan outcome when
  the task has a plan). An
  approval prompt Slack reports as edited or with a subtype is not the native
  prompt, so 08 cannot pass on it. Drill owner inputs follow the same rule.
- 07, 17, 15 (budget and meter rows): the in-image `consistency` command. Its
  verified generation commit and tree must equal the runtime image's commit
  and tree labels, and the tree must be the labelled commit's tree on GitHub
  (GET only); when any of these is unverified or differs, 07, 08, 11, 12, 15,
  17 and 20 are UNKNOWN, because the in-image evidence belongs to that
  generation, not to the labelled image. 17
  is UNKNOWN unless the adoption's repository is exactly this repository.
- 10: Owner ruling (2026-09-24), option chosen: "Offline = pipeline proof". Check 10 proves the pipeline works end to end offline (delivery, simulation labels, ten consecutive, committed structural rubric); answer correctness is check 20's job (the real paid run). Check 10 therefore does
  not verify answers: its PASS rests on the committed structural rubric and
  every binding, freshness, route and consecutiveness predicate, and its run
  records report `structural_rubric_passed`, never answer correctness. Check 20
  verifies answers.
- 10: ten `--trial` threads evaluated in-process with `evaluate_run_records(mode="offline")`,
  then one bounded `conversations.history` read of `--channel` from the first
  trial to the last. A trial whose terminal reply Slack reports as edited, or
  with any subtype, shows neither the simulation label nor the cost text, so it
  is neither offline nor rubric-correct.
- 11, 12: the in-image `activation-captures` command; 11 also reads the paid run's lanes.
  The reference workflows for 01 and 04 are read from the running candidate
  commit (`git show <runtime sha>:n8n/workflows/...`), never the working tree;
  unavailable is UNKNOWN. The in-process renderer 08 and 20 use must be the
  candidate's own files (`primary_slack_delivery.py`, `contribution_tags.py`,
  `models.py`, compared with that commit's blobs); otherwise 08 and 20 are
  UNKNOWN. n8n must use the default `public` schema (`DB_POSTGRESDB_SCHEMA`
  absent or `public`), and both n8n queries pin `search_path` to it.
  11 requires both the app and the runtime-adapter capture of each lane used
  (`provider_ready` needs both) to have verified, been observed and persisted
  before the paid attempts and stayed unexpired through them. 04 requires the
  posting execution to postdate the Post Status credential's latest update
  (only `credentials_entity.updatedAt` is read, never credential data). 10 and
  20 take a run's kind only when the persisted `execution_mode` agrees
  (`offline_simulation` or `paid_off` for offline, `live` for paid);
  inconsistent readback is UNKNOWN. 20 grades an outcome only when its
  `provider_result_digest` and assignment sequence join a SUCCEEDED assignment
  and its latest SUCCEEDED attempt with the same result digest. 18 requires the
  indexed `target_revision`, not only the diff head, to be the candidate.
  12 requires the captured lanes to equal the generation's required providers
  exactly. 03 and 09 treat an edited or subtyped owner post in a drill thread
  like an altered bot reply (UNKNOWN), and 04 requires the read-back status
  text to equal the text the execution's Slack response says it posted.
- 13, 14: the workload journal from `--ledger`.
- 15 (daily accounting): `/health` `usage_monitor.daily_accounting`, only when
  the monitor is enabled with its paid gate open (`gate_state` `open`) and no
  configured provider is unsupported, missing or stale. A provider with spend
  today (`reported_cost_usd` or `reserved_cost_usd` above zero; `request_count`
  counts the monitor's own usage-check probes) and neither a numeric
  `daily_budget_usd` (only gemini, grok and perplexity have one) nor an
  owner-signed enrolled budget for that provider leaves 15 UNKNOWN.
- 19 also requires `provider_activation.combined_live_ready`.
- 16: GitHub `ci.yml` runs and jobs at the exact head, or `--ci-replica-receipt`.
  Runs are read page by page (100 per page, at most 10 pages) until GitHub's
  `total_count` is reached, and each run's jobs must match its `total_count`;
  an incomplete listing is UNKNOWN, so the receipt is never admitted on a
  partial read.
- 18: the native Security index for `--security-scan`. The sealed scan's base
  must equal the candidate's merge base with `main` (GitHub compare, GET only);
  any other base, or an unreadable merge base, is UNKNOWN, because a convenient
  base can drop the candidate's own changes from the covered diff. A candidate
  whose merge base moves (for example after merging `main`) needs a new scan.
- 20: the `--paid-thread` readback, `--paid-rubric` and settled meter rows.

### Drill-thread kinds

Each `--drill-thread KIND=THREAD_TS` names one owner message the operator
posted to exercise one path. A drill that was not exercised stays UNKNOWN.

| Kind | PASS | FAIL | UNKNOWN |
|---|---|---|---|
| `refusal-4xx` | complete thread, one owner input, a `DIRECTOR — BLOCKED` reply, no receipt, binding 404 | a bound task (200) or ambiguous binding (409), or any receipt | otherwise |
| `reserved-5xx` | complete thread, one owner input, a `BLOCKED` reply, no receipt, binding 200 and the task's readback shows one task for the thread | binding 409 or more than one thread task | binding 404 (the reservation was not exercised), a receipt (the gateway answered 202, so the 5xx was not exercised), or otherwise |
| `transport-failure` | complete thread, one owner input, a `BLOCKED` reply, no receipt, and binding 404 or binding 200 with one thread task | binding 409 or more than one thread task | a receipt (the relay answered 202, so the failure was not exercised), or otherwise |
| `duplicate-input` | one owner input, two or more receipts (distinct replies) naming the bound task, the single terminal delivery `DELIVERED` with its Slack ts and matching the native final message, one thread task and one final reply | binding 409, receipts naming several tasks, several thread tasks or final replies | fewer than two receipts |
| `ambiguous-post` | a complete thread with one owner input, the bound task's single terminal delivery is `DELIVERED` with `acknowledged_at` null, `delivered_at` and `slack_ts` set, one thread task, one final reply matching the native message | the terminal delivery is `AMBIGUOUS` after two readbacks, several thread tasks, or several final replies | `acknowledged_at` is set (the ambiguity was not exercised) |

Check 03 combines `refusal-4xx`, `reserved-5xx` and `transport-failure`; check
09 combines `duplicate-input` and `ambiguous-post`. Any FAIL fails the check,
all PASS passes it, anything else (including a kind that was not supplied) is
UNKNOWN. A transport failure (the relay pointed at a closed port, step 7b of
[n8n operations](n8n-operations.md)) reaches the same n8n ambiguity branch as a
5xx but usually without a reservation, so it reads binding 404, which
`reserved-5xx` cannot score; it is therefore its own drill. Observable outcomes
overlap between kinds (a 4xx refusal and a transport failure both read 404 and
`BLOCKED`), so each kind names a distinct operator-labelled thread and the
scorecard scores the observable outcome. The scorecard cannot tell a transport
failure from a refusal or a reserved 5xx, so 03's PASS says the transport
failure itself is operator-attested, not observed. The drill procedures
themselves are operator actions and are not automated here.

### In-image commands

```text
python -m director_orchestrator.primary_generation consistency [--task DIR-LG-… (up to twelve)]
python -m director_orchestrator.primary_generation activation-captures
```

The scorecard runs both through `docker exec <Director container ID>` with a
45-second bound. Each first verifies the running generation exactly as
`verify` does (`require_generation`, never reserving) and prints one JSON
document. They never call `require_runtime_off`: they run beside an active
runtime and write nothing.

- `consistency` opens one `REPEATABLE READ`, `READ ONLY` transaction with
  `default_transaction_read_only` set and counts unresolved reconciliation
  claims, ambiguous assignments, tasks requiring reconciliation, terminal
  tasks with open assignments or effects, projection/batch plan-digest
  conflicts, projections ahead of the intake fence, `SUCCEEDED` projections
  with any assignment not `SUCCEEDED`, projections naming a plan digest with
  no batch row, batch rows with no projection, and in-flight work (open
  effect reservations, active assignments, live writer claims, batches pending
  projection binding, and `OWNER_DECISION_REQUIRED` holds with a pending
  approval, which are in flight even with no open assignment or lease). For each `--task` it adds the spend rows with their
  settled usage and request correlation, and the enrolled budget rows with the
  day's held amount, their provider and storage grant and whether the full
  budget document passes the owner-signed `ProviderBudget` contract (closed
  schema, HMAC owner signature re-verified with the entitlement owner-authority
  secret, and a grant digest equal to the grant it is stored under). Each spend
  row carries its grant and whether its usage receipt passes the
  `ProviderUsageReceipt` HMAC with the entitlement route-verifier secret and
  binds that attempt, grant, provider, cost and tokens; an unverified receipt
  is no actual cost. Without either secret nothing it signs verifies, and the
  report says which secrets were available, and its `approval_resume` command receipts (SHA-256 of
  the command ID, command and resulting projection digests, recorded time). In
  `owner-local-evidence-v1` mode it also returns the pinned adoption's
  repository, pull request, author, reviewers, reviewed source and adoption
  digest. A task is terminal here when its terminal classification is set,
  unless it is `OWNER_DECISION_REQUIRED` with a pending approval request (the
  predicate Slack delivery enqueues approvals by): that hold is resumable, so
  its open assignments, effects and unbound batch count as in flight. An
  `OWNER_DECISION_REQUIRED` hold with no pending approval cannot resume and is
  terminal. A terminal task whose batch never bound to its projection counts as
  `terminal_batches_unbound`. A `PENDING` assignment whose prerequisites
  include a `FAILED` or owner-held assignment, directly or through another such
  assignment, is the aggregator's `BLOCKED` projection and is counted neither
  as open nor as active. Check 07 fails on any unresolved, ambiguous,
  conflicting or terminal-residue count, is UNKNOWN unless the snapshot names
  the running generation pin and intake fence, and is UNKNOWN while work is in
  flight.
- `activation-captures` reads the activation store through a load-only view and
  the validation store through its read-only capture reader. For every lane it
  reports the binding digest and, per surface, the latest stage, evidence
  digest, observation time, expiry and whether the capture verifier accepts it,
  plus the product that produced the verified runtime-adapter capture and the
  LIVE_OK row's persisted `recorded_at`. Check 11 requires both that row's
  `observed_at` and its `recorded_at` to precede the paid attempts, because
  `observed_at` is supplied by whoever published the capture. The
  product comes from the capture's own dialect and model: an OpenAI Responses
  envelope needs an OpenAI model (`gpt-`, `o<digit>`, `codex`, `chatgpt-`), a
  Codex capture needs a native Codex JSONL turn (and check 11 maps each OpenAI
  attempt's transport by allowlist: `responses-api` to the Responses product,
  `native-cli` and `authenticated-windows-host-bridge` to Codex; any other
  transport is unattested), a Grok Responses envelope a
  `grok` model, a Grok CLI envelope `stopReason` with an integer `num_turns`
  and a Grok model, and a Claude result envelope a non-empty `modelUsage`
  whose keys are all `claude` models. An envelope that names no model has no
  product: the lane name alone attests nothing. Anything else has no product. These are not part of
  `/health`, which stays readiness booleans only.

Against an image built before these commands existed, `docker exec` exits
non-zero and the dependent checks read UNKNOWN.

### Paid answer rubric format

`--paid-rubric` is a JSON file of at most 2 MiB with exactly these keys:

```json
{"schema": "director-paid-answer-rubric/v1", "rubric_id": "lowercase-id",
 "declared_at": "2026-09-23T07:00:00+00:00", "expected_answer_substrings": ["one to five non-empty strings"]}
```

The owner's paid Slack input must carry `rubric-sha256:<sha256 of the file's
bytes>`, `declared_at` must not follow the input, the task must be `DONE` with
exactly one final reply, and every substring must appear in the persisted
provider results and in the reply as the Director's renderer produced it
(the reply must equal that rendering, truncation included, so text cut from
it is never graded), and none in the owner's question or in
the Director's fixed terminal wrapper (header, completion and cost lines,
contribution tags), which would otherwise satisfy it. A
paid thread with any bot reply Slack reports as edited, or with any subtype,
is UNKNOWN (the task receipt lookup refuses it), and such a final reply is
never graded correct: Slack keeps its original timestamp, which the wall time
uses, but returns the new text. Every Slack text read as evidence goes through
the collector's one `unedited` rule.

### Local-replica receipt format

When no required job executed at the exact head, `--ci-replica-receipt` may
supply an owner-signed receipt:

```text
{"document":{"schema":"director-ci-local-replica/v1","repository":"edwardjones3976/director-orchestrator",
  "candidate_sha":<40 hex>,"base_sha":<40 hex>,"produced_at":<timezone-aware ISO time, not in the future>,
  "lanes":[{"lane":"windows-postgres-lane"|"ubuntu-replica","head_tests_run":<int > 0>,"base_tests_run":<int > 0>,
    "head_failures":[<unique test ids, each at most 512 characters, at most 10000>],"base_failures":[...],
    "command_sha256":<64 hex>} x exactly these two lanes]},
 "signature":<128 hex Ed25519 signature over
   json.dumps(document, sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)>}
```

The verification key is the 64-hex raw public key at
`~/.director-scorecard/ci-replica-signer.ed25519.pub`, which must be a regular
file, not a link; there is no option to name another key. The signer must be
owner-held and never an author of the candidate's pull request. `base_sha`
must be a commit on `main` that the candidate descends from (GitHub compare:
`base...candidate` is `ahead` and `base...main` is `ahead` or `identical`), and
every lane's `head_tests_run` must be at least its `base_tests_run`; otherwise
the receipt is UNKNOWN. The receipt passes when neither lane's head failures
include a test absent from its base failures, fails on any such new failure,
and stays UNKNOWN when the key is missing or the receipt is malformed, unsigned
or signed by another key. It is bound to its own `candidate_sha`, so a receipt
for another candidate is UNKNOWN. A receipt is considered only when none of
the six required jobs executed; when some executed and others did not, check 16
fails. An executed job that did not succeed fails check 16 regardless of any
receipt. Hosted billing stubs (zero steps) never count; an executed job with a
required name counts the same on hosted and registered self-hosted runners, but
only from a `.github/workflows/ci.yml` run. That path is fixed in the
scorecard, not read from the candidate. A same-named job of any other workflow,
including one the candidate adds, can fail check 16 but never fills a required
job. The contents of `ci.yml` at the candidate are still the candidate's, so a
change to that file is left to review (17) and the Security scan (18).

### Status execution retention

`n8n/workflows/director-status.json` saves successful executions so check 04
can correlate the posted message (default D13 of the owner-approved
2026-09-23 completion plan: save successful `director-status` executions and
prune them after 72 hours). The workflow file alone changes nothing on a
running n8n: re-import it. Every five-minute run is saved, not only runs that
post. Each saved execution keeps the full `Read Health` response (the
Director's `/health` body, which carries readiness booleans and counts but no
secrets, plus its response headers), the diff node's output and, when it
posted, the Slack post response. It carries no credentials.

The 72-hour pruning is an operator action and is not in this repository's
`docker-compose.yml`: set `EXECUTIONS_DATA_PRUNE=true` and
`EXECUTIONS_DATA_MAX_AGE=72` on the n8n service. Those settings apply to every
workflow on that instance, the intake relay included. Without them n8n keeps
saved executions for its own default age. The scorecard reads only the last
72 hours either way.

### Execution-binding limits

The consistency snapshot also returns the manifest-verified
`DIRECTOR_RUNTIME_IMAGE_ID`, which must equal Docker's inspected image
(otherwise 07, 08, 11, 12, 15, 17 and 20 are UNKNOWN), and the selected gateway
must run the Director's image (01 FAILs; Slack-derived 03, 08, 09, 10 and 20
are UNKNOWN). The consistency and activation reads re-verify, inside their own
read-only transaction, the accepted control plane's storage identity and
database instance, and refuse any other database. Each observation is stamped
when it was actually made (after every read it rests on, never later than the
collection's end), so a PASS read before slower sources is not carried forward
to the end; a time-limited PASS (12's capture expiries) is revalidated against
its stamp, and a fixed-clock collection stamps at its frozen end. Upper bounds
applied while grading use the time of that grading read. Just before the end the Director, gateway
and n8n containers are re-inspected (ID, image, creation, start time and
running state); if any differs from the deployment probe, or cannot be read,
every runtime-derived PASS or FAIL becomes UNKNOWN. That reinspection covers
every container whose state backs evidence: the Director, its
`langgraph-primary-db`, n8n, `n8n-db` and the named gateway. Check 04 bounds a
saved status run by the later creation of n8n and `n8n-db`, so a run saved
before the execution store was recreated grades nothing. The primary database
is not a freshness bound: its evidence is bound per read to the accepted
database instance. `score()` scores runtime checks for exactly one runtime
binding: the supplied one, or else the single binding all runtime observations
carry (reported at the top level); observations from mixed deployments or
images leave every runtime check UNKNOWN. Credential comparisons never match
two empty values, and the gateway's credentials count only when each is set,
the intake signing secret is at least 32 characters (slack_bot's startup rule)
and differs from the bearer. Check 08 also requires the owner's authorization
to be claimed no earlier than the prompt's Slack timestamp. Offline doubles
persist no result packet, so check 10 binds each offline outcome to its
assignment's durable ledger row: run_id `offline-double:<reservation digest>`,
the fixed simulation label as its result and the row's result digest; any
other outcome FAILs and a row without its reservation digest is UNKNOWN.
Every observation is stamped when its source phase (deployment, GitHub, ledger,
Security) began reading, so a composite built from several reads (15 joins
`/health` with the consistency snapshot) is never stamped after its earliest
dependency read. Each assignment's `reserved_at` and `completed_at` are
settlement evidence: both must be present, in order and inside the freshness
window. The ambiguous-post drill (09) needs a DELIVERED terminal row that was
never acknowledged and was settled by one or two readback attempts, as
`PrimarySlackDelivery.readback` does. Check 07 counts every nonterminal
projection as in flight (`nonterminal_projections`), even with no subordinate
row. Check 04's newest run needs both its start and stop time, in order and
inside the window. Settlement (06, 10, 20) needs every attempt's events and every
effect's lifecycle (reserved, started, bound, terminal) in chronological order.
Every budget, cost, limit and run measurement read must be a finite, non-negative
number. Every task-backed drill verdict (reserved-5xx, task-backed
transport-failure, duplicate-input, ambiguous-post) needs the same correlation:
`task_record_bound` and `task_api_gateway_attested`. One ordering helper serves
every lifecycle (Slack delivery, effect, attempt, assignment, n8n execution): a
DELIVERED row counts only as `PrimarySlackDelivery` produces it, created, claimed,
acknowledged when Slack answered (null on the ambiguous recovery path) and then
delivered by one or two readback attempts, in that order. One read-time rule
serves every snapshot: the consistency snapshot, the captures report and each
qualification readback carry their own read time, which must lie inside this
collection. Check 10 also requires each offline final reply's text and blocks to
be the candidate renderer's canonical rendering, and check 15 requires
`daily_accounting` to cover exactly the monitor's configured `providers` (unique,
valid names). A DELIVERED row's Slack timestamp lies inside its lifecycle
(claimed, then posted, then acknowledged or read back); every attempt event lies
between its assignment's reservation and completion (the product's write order);
and check 07 counts a SUCCEEDED projection without a bound batch or a SUCCEEDED
assignment as a conflict (`succeeded_projections_without_plan`).

Documented limit (check 04): the check binds the status post to the
authenticated Director bot identity (the posted message's user is the bot
`auth.test` names), to a run after the Slack credential's last update in n8n,
and to the currently active workflow version. It does not authenticate the
token n8n holds: that would need n8n's encryption key to decrypt its credential
store and a Slack call with that token, which is new credential access reserved
for the owner. A token revoked outside n8n, with its credential row unchanged,
is therefore not detected until a later run fails. A drill's (09) or the paid
run's (20) correlated final reply counts only when its fallback text and its
complete normalized blocks (compared as check 08 compares prompts) are the
Director's canonical terminal rendering; 20 grades the answer on the blocks'
content too. An approval prompt (08) must match the canonical rendering in its
fallback text (newlines possibly flattened, as the Director's own readback
allows) as well as its blocks. An owner input binds to its task only through
the gateway's own `source_event_id` (`<channel>:<ts>`, computed by the one
`models.canonical_slack_source_event_id` both use), and every Director terminal
classification counts as a final reply. The n8n status query casts its
timestamps to `timestamptz` under a UTC session, so they always carry an offset. Check 20 also requires every settled assignment's persisted
provider result packet (read by the in-image report and revalidated against
its stored digest) to be the one its result digest names, with outcome text
equal to the graded outcome's result: a mismatch FAILs, an unread or missing
artifact is UNKNOWN.
Every upper bound and expiry reference uses the collection's end: check 12's
capture observation and `FRESH_FOR` validity, and the CI replica, workload
journal and Security seal times. Check 04 dates the newest scheduled run inside
the current n8n's life and the collection before using its status. One
settlement join serves checks 10 and 20 (every readback assignment SUCCEEDED
with exactly one outcome of its sequence and the same result digest; for paid
runs the latest attempt too), and one settlement rule (every latest attempt and
started effect terminal) serves checks 06 and 20. Activation insertion times
are read for the current binding's latest row (every binding field), never by
digest alone.
A collection runs from its start to its end, which is known only once the live
reads finish. Evidence is bounded by the end; each source's own read time (the
consistency snapshot's `captured_at`, the captures report's `observed_at`)
must lie between the start and the end; and every observation is stamped when
it was made, after its reads, so nothing read during the collection is backdated. Drill verdicts
bound every post they read (root, owner inputs, receipts, `BLOCKED` notices,
final replies), and run records bound each external effect's reservation,
start, binding and terminal times. Check 16 credits a job only if at least one
of its steps completed without being skipped, and counts only those steps.
One freshness rule (`_is_current`) runs before any grading in every runtime
collector (03, 04, 06, 08, 09, 10, 11, 20): evidence whose moments (a Slack
post, a task's creation or update, an execution's start or stop) are not all
between the latest creation of the containers that handled it and the
collection time grades nothing, so its outcome is UNKNOWN, never PASS and
never FAIL. The same upper bound applies to the consistency snapshot's
`captured_at` (07), each capture's `observed_at` (12), the paid attempts (11)
and the approval decision (08), whose command authorization must also have
been claimed before its receipt and inside its signed-proof window. Both
workflow contracts also compare the workflow-level `settings` with the file
and require no pinned node data; tags and static data change nothing at run
time and are not compared. For 04 that is the Director and n8n; for Slack
intake evidence the Director, the `--gateway` and n8n, where the gateway must
also hold the Director's Slack bot token, owner, channel and bearer secret
(compared in constant time, never reported).

No producer records which image or process executed a task. Checks 10, 11 and
20 bind a run to the running image only when the container was created before
the task was. Checks 03 and 09 do the same for each drill (the drill post and,
when a task is bound, its creation), and check 08 for the approval task. Check
06 requires the current process start to fall between the task's creation and
its terminal delivery, a durable processing step (an approval prompt
enqueued or a provider attempt recorded) before it, and a durable graph step
after it (a provider attempt recorded or the task projection updated), since a
terminal notification enqueued after a restart is not the graph resuming; a task that was only
created or queued before the restart is UNKNOWN. A later container recreation, such as an activation that
force-recreates the Director, therefore makes those checks UNKNOWN until the
runs and drills are repeated. The writer generation does not prove a restart,
because the same owner token renews a live lease. This is an ordering check,
not an execution record: a task created after the container but handled by a
process the scorecard cannot see is still credited to the running image.
03, 08, 09, 10 and 20 are graded only while the current intake route is
verified with check 01's predicates (the gateway's relay URL and credentials,
both hops, n8n's store and the active intake workflow equal to the candidate's
file); otherwise they are UNKNOWN. Every Slack and readback moment a verdict
uses (task creation and update, each delivery's times, provider attempts, the
owner input and the terminal replies) must lie inside the freshness bound. A
paid run's ambiguous-effect count is zero only once every attempt is terminal;
an unsettled or unknown effect state leaves it unknown. Task receipts count
only as a reply's leading header, in the collector as in the drill parser.
Slack intake passes through the gateway and n8n, so 03, 08, 09, 10 and 20 also
bind to the `--gateway` container and the deployment's single n8n: their
freshness bound is the latest creation of the Director, the gateway and n8n,
and without a running `--gateway` and one running n8n of the deployment they
are UNKNOWN. `--channel` accepts `C` and `G` Slack IDs.
Check 10 reads only what Slack still shows: a deleted post leaves a tombstone,
which 10 treats as a break, but a deleted post with no replies leaves nothing.

## Evidence and limits

Secrets and digests (the app-token fingerprint, generation and binding pins,
command-ID digests, seal and artifact hashes, rubric digests) are compared in
constant time with `hmac.compare_digest`.

Only observations produced by the module's fixed read-only collectors in the
current process can earn points. JSON containing `verified: true`, a copied
scorecard, model prose, a declared provider name or an unverified signature
never becomes authoritative. Files in the explicitly selected evidence folder
are linked with SHA256 as **unverified references**. The scan is non-recursive,
limited to 100 entries and 2 MiB per JSON/Markdown file, and skips symlinks and
hidden files. A hash establishes byte identity, not truth or author identity.

Evidence includes source, observed time, expiry, digest and applicable immutable
bindings. Observations expire after five minutes. Missing, stale, future-dated,
wrong-head and wrong-image evidence yields UNKNOWN, never PASS, as far as the
bindings above can see it. The diagnostic
trusts the named local host and service; its in-process marker is not a security
sandbox against arbitrary Python code, and it does not attest an uncompromised
host or validate image-label supply-chain provenance.

Health counters alone can only fail 07 and 12: a reported historical hold, hold
reason or ambiguous effect fails 07 even when other health fields are missing,
and a false `combined_live_ready` fails 12. Zero counters and a true readiness
flag prove nothing; 07 and 12 pass only from the in-image commands. Worker
readiness is separate in 19. CI reuses `state_witness.gh_checks_green`,
including its actual-step and required-job checks. Its temporary ledger is
diagnostic and does not imply that production merge/approval composition has
injected the Witness Ledger.

Check 01 observes localhost port bindings, the gateway's channel and
n8n relay configuration, and reads the actual published intake version through
the native workflow-history table in a read-only database transaction. It requires
the supported webhook-to-request connection and the exact static Director intake
URL before performing a health request to that URL's upstream from the
deployment's n8n. A healthy generic alias cannot pass for a workflow pointing
elsewhere. Missing version history or an unsupported/dynamic workflow cannot
pass. This is configuration/routing evidence; behavioral intake and final
delivery remain separate checks. Collection reads environments in memory but
emits only selected booleans/counts and the public upstream environment name,
never tokens, channel IDs or the full environment.

Check 02 inventories all running Docker containers, not only Compose labels,
and compares app-token fingerprints in memory. It distinguishes the gateway
launch command from the known API launch command, which also inherits the token
without starting Socket Mode. An unclassified same-token container yields
UNKNOWN. A bounded Windows process inventory examines known Python/Node/shell
gateway launch signatures without exporting command lines. Unreadable process
details or a host gateway requiring app-identity reconciliation yield UNKNOWN,
with the verified Docker count retained. Multiple matching Docker gateways
yield FAIL. This health inventory does not attest maliciously disguised processes,
other machines or remote socket sessions. Container inventory is capped at 128;
a changing or oversized inventory stays UNKNOWN rather than claiming completeness.

Check 18 resolves only a UUID through the native workbench database at
`~/.codex/state/plugins/codex-security/workbench.sqlite3`. A custom native state
location is not supported. There is no CLI option to designate a copied JSON file
or an arbitrary index as trusted. The collector verifies the index's stored
manifest digest, original canonical artifact and preserved-source hashes,
completed scan/head/base binding, seal time and every changed Git path's coverage.
It requires complete coverage, no exclusions/deferred surfaces and no canonical
findings. Native operational limitations remain in the source manifest and the
report's evidence facts. It trusts the local native index, not a cryptographic
attestation of an uncompromised workstation. It reads SQLite with `mode=ro`,
closes the handle and never calls native scan finalization.
Canonical artifact hashes cover raw bytes. Preserved checkpoint hashes follow
the native producer's sorted compact JSON digest; formatting changes alone do
not alter that semantic digest.

Check 13 recomputes the current journal through `EngineeringWorkloadLedger.status`.
It needs twenty actual admissions after the prospective cutover and reads the
ledger's own authoritative tri-state `reported_resource_bounds_ok`: PASS only
when every admission's reported usage is fully known and within its declared
bounds; FAIL as soon as any admission has a known reported bound violation,
even while other admissions' usage is still unknown; and UNKNOWN whenever
usage is unknown, incomplete, or the value is malformed — unknown usage is
never treated as compliant and never yields PASS. The owner retired the
rolling-twenty allocation hard ceiling on 2026-09-21; normal allocation bands
remain an advisory routing preference and this check does not gate on either.
Imported historical attestations do not count as authenticated proof.
Before twenty prospective admissions exist, UNKNOWN honestly describes the
incomplete window. This observation does not authorize the journal's policy,
authenticate declared provider identities or confer dispatch/production credit.
Check 14 uses the same complete window: it passes when every admission
declares positive timeout, call, turn and output bounds and an operator-attested
hard USD cap with an evidence reference and SHA-256, and fails when any
admission lacks a USD limit or that attestation. Owner ruling (2026-09-25),
verbatim: option chosen: "Attestation, labelled". Check 14 keeps grading the
operator's per-admission hard-cap attestation from the engineering workload
ledger and says so plainly: its description, PASS explanation and facts
(`cap_evidence: "operator_attestation"`, plus the ledger's own
`operator_attestations_are_not_cryptographic_proof` flag) call the caps
operator-attested, not cryptographically verified. The product has no
authoritative source for these caps: the ledger stores the operator's boolean
and evidence reference without checking them
(`engineering_workload_ledger.py` `_bounds`), and only Director-runtime paid
tasks carry owner-signed `ProviderBudget` caps (`antigravity_team.py`,
enforced on every paid attempt by `PrimaryProviderBudgetAuthority.reserve`),
which check 15 already grades. Team-journal admissions have no signed budget
row and no join key to one.

Checks 11 and 20 read the paid run's own evidence. 11 takes the lanes from the
paid task's collected attempts and fails when any lane is not one of the real
products above or its verified capture names another product; a verified,
bound capture with no product counts as a substitution. On the OpenAI lane the product must also match the
attempts' transport: `responses-api` needs `openai-responses` and any other
transport `openai-codex`. 11 is UNKNOWN when the running container was created
after the paid task, the captures are not bound to the running generation pin
and provider bindings, or a lane's runtime-adapter capture does not verify now,
was observed after its first paid attempt or expired before its last one. 20 builds its run record from task-level evidence:
the mode is paid only when the readback records at least one provider attempt,
never from the runtime's paid switch, and actual cost is the sum of settled
usage rows whose metering evidence carries request and response IDs, correlated
by attempt digest to every attempt the task made. Check 15 fails when an
enrolled signed daily budget's held amount exceeds its limit or a usage-monitor
provider exceeds its daily budget, and passes only when the paid task's budget
rows, the usage-monitor accounting and a settled cost are all present. A
budget row that fails that signed contract, or a charged attempt whose task,
project, budget day, provider and grant do not all match one signed budget, leaves 15 UNKNOWN, and an
unverified usage receipt leaves 15 and 20 without actual cost (UNKNOWN). It is
UNKNOWN when a budgeted usage-monitor provider has attempts today (a
`request_count` that is not exactly zero), incomplete cost visibility and no
reserved cost, because `reported_cost_usd` sums known costs only. Unbudgeted
providers (claude, openai) are capped by their signed enrolled budgets; their
`request_count` counts usage-check probes, not paid attempts.
Usage-monitor accounting covers only its configured providers; the signed
budget rows carry the enforced cap. The reusable run-fact validator enforces
count, unique IDs, actual owner origin, runtime/image binding, correct mode,
wall time, owner-pause arithmetic, final-message count, ambiguity, zero offline
calls and the basis of actual cost. The CLI does not award points for a
user-authored run JSON file.

An estimate or reservation is not actual cost. Unknown cost is not zero.
Machine time excluding an owner pause is shown separately and cannot substitute
for the required under-60-second wall time. Actual Grok Bot is distinct from
direct xAI Grok and Cursor Grok, regardless of shared branding or quota labels.

## Offline trial records

[Offline trial tooling](collect-offline-slack-trials.md) checks a rubric declared
in the original source message. Its JSONL recorder uses exactly
`OFFLINE_RUN_RECORD_FIELDS` (`RUN_RECORD_FIELDS` plus `simulation_label`) and
shares `evaluate_run_record`. The ten-run evaluator keeps its count, distinct-ID,
wall-time, origin, image, cost, ambiguity and zero-call requirements.

A zero-call-verified offline run maps to `actual_cost_usd: 0` with
`cost_basis: simulation_zero_calls`; incomplete operational-attempt authority
stays null. The recorder retains a non-qualifying row and stops further appends.
Saved receipts do not become scored observations. Check 10 loads the same
collector in-process for the ten `--trial` threads and scores them with
`evaluate_run_records(mode="offline")`; offline means the task's own evidence
(offline-double outcomes, zero exposed and recorded provider attempts and a
simulation-labelled final reply), not the runtime's current mode switch. The
ten must also be consecutive: one bounded `conversations.history` read of
`--channel` from the first trial to the last must show no other top-level owner
post the gateway could forward (only bot posts and `bot_message`,
`message_changed` and `message_deleted` subtypes are ignored) and no deleted
(`tombstone`) or hidden message. Otherwise 10 is UNKNOWN. Replies inside other threads are not read. Check 10 is FAIL, not
UNKNOWN, when a selected run fails the standard.

## Qualification verdicts with explicit UNKNOWN cost

`evaluate_qualification_record` classifies one canary's **asserted** lineage
row (source event digest, task, assignment/attempt/effect identities, provider
route, exact runtime SHA, image and configuration digest, effect state,
delivery/readback, status-receipt counts, duplicate-effect and policy-violation
counters) into `QUALIFIED_COST_MEASURED`, `QUALIFIED_COST_UNKNOWN`,
`NOT_QUALIFIED` or `UNKNOWN`. The row is untrusted caller input: the function
checks that the asserted links are complete, well-formed, unique and bound to
the exact revision; it cannot verify that any link is real, and its reasons
say "asserted links only". Authenticated collector correlation remains a
separate requirement that no verdict here claims. It is deliberately separate
from the check 10/20 criterion: `evaluate_run_record` still returns `None` for
an unknown cost and never accepts a `QUALIFIED_COST_UNKNOWN` row as a
measured-cost PASS.

Strict inputs: identities must match the compact identifier form and be unique
within each lineage list; `provider_route` must be one of the canonical
`allocation_profiles.LANES` (aliases such as `external_openai` are not
accepted); counters must be exact non-negative integers (booleans and floats
are rejected); a cost must be a finite non-negative number (`inf`/`nan` are
not measured costs). A `ZERO_PROVIDER_EFFECT` simulation is `NOT_QUALIFIED`
for a live provider round trip, as are ambiguity, duplicates, violations,
unverified readback and foreign revision or configuration bindings. Missing or
malformed links and absent status-receipt counts (the Issue #230 dependency)
stay `UNKNOWN`. No verdict from this function contributes points.

## Validation

```powershell
$env:PYTHONPATH = 'src'
python -m unittest tests.test_completion_scorecard tests.test_primary_scorecard_evidence.ScorecardEvidenceUnitTests -v
.\scripts\windows-postgres-test-lane.ps1 -Action Test -TestNames tests.test_primary_scorecard_evidence.ScorecardEvidencePostgresTests
```

Every collector has a positive case, a negative control and an UNKNOWN case for
a missing or unavailable source, and one test supplies every selection while
every source fails to show that collection never raises and never emits a
credential, channel or owner identity. Tests also cover stale/future evidence,
forged self-claims, candidate/image mismatch, missing fields, executed steps
versus billing stubs, UNKNOWN cost, incorrect cost basis, simulations presented
as paid runs, duplicate results, measured wall time, unlabelled duplicate
gateways, wrong project routes, unavailable host visibility, altered native
seals and incomplete changed-path coverage. The read-only consistency counts
and settlement rows run against the disposable PostgreSQL lane
(`ScorecardEvidencePostgresTests`); no live provider, Slack or deployment is
touched.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 23:07 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 02:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:05 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:00 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]] -->
<!-- [[Codex Director integration | GPT-6 Astra | 2026-09-22 | 01:54 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 07:12 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:29 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 20:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 22:57 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:02 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:39 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 23:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:17 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:32 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 00:50 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 06:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 07:40 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:03 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:22 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:39 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:58 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:18 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:37 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 17:53 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:46 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:09 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 19:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:17 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 20:53 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:10 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:32 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 21:53 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:16 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:34 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 01:36 UTC | END]] -->
