<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:52 UTC | BEGIN]] -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 16:58 UTC | BEGIN]] -->
<!-- [[Cursor Agent | Composer 2.5 | 2026-09-22 | 08:15 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:42 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:40 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:11 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 01:12 UTC | BEGIN]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 07:55 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 17:28 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:31 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:06 UTC | BEGIN]] -->
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:55 UTC | BEGIN]] -->
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | BEGIN]] -->
# LangGraph-primary operations authority

**Status:** merged source architecture; deployment and paid activation require fresh,
release-bound evidence. This page is not a live health report.
**Owner:** Aesop / Ed
**Orchestration authority:** deterministic policy and code (`policy.py`), never model output
**Repository:** `edwardjones3976/director-orchestrator`

Source reconciliation baseline: `2f21b793cc7b10586948e1b3f3c2c6fc0eab16ec`.
The canonical operations procedures below apply to a selected release. Record that
release's exact source, image and generation in its acceptance package; the
source baseline above is not Long Table canary authority or a deployment receipt.

Archived pre-primary sources (historical, non-authoritative):

- [langgraph-transition-plan-pre-primary.md](archive/langgraph-transition-plan-pre-primary.md)
- [langgraph-shadow-operations-pre-primary.md](archive/langgraph-shadow-operations-pre-primary.md)

## Current truth

Read current deployment ownership from authenticated health and its authoritative
writer ledger at the selected generation. Neither a merged PR nor a configured
switch establishes ownership, provider qualification or paid execution. Offline
LangGraph intake ownership can coexist with paid dispatch disabled; distinguish
those observations explicitly in the acceptance package.

Exactly one engine owns each task's mutations and external effects. Ownership
transfer requires the cutover gates below; rollback must preserve the ledger and
reconcile external effects rather than blindly replay them. Gate 0 accepted a
prerequisite, not a deployment. Packet schemas and the
[interoperability contract](model-interoperability-contract.md) remain binding.

## Target topology

LangGraph becomes the primary coordinator for the full task lifecycle:

1. **Intake and deduplication** — authenticated Slack/API events with stable
   idempotency keys.
2. **Authority binding** — project/profile, allowlisted repository, immutable
   starting commit and authority-snapshot digest.
3. **Plan and decomposition** — configured runtime Director planning; validated assignment DAG.
4. **Deterministic gates** — policy preflight, workload limits, budget reservation,
   hop/revision/timeout counters; model output may add gates, never remove them.
5. **Authenticated interrupts** — owner approval and provider callbacks bound to
   task, assignment, generation, scope, candidate digest/SHA, actor, and expiry.
6. **Dependency scheduling** — only dependency-ready assignments enter dispatch.
7. **Provider subgraphs** — v1 packet boundary adapters; provider-native payloads
   stay inside envelopes.
8. **Evidence aggregation** — normalized manifests, usage, interaction evidence.
9. **Bounded debate** — Gemini/Antigravity simulation and behavioral validation
   lanes where policy permits; not free-form routing.
10. **Synthesis and acceptance** — configured runtime Director integrates peer
    evidence; cannot accept its own exact candidate.
11. **Revisions** — bounded revision loop with deterministic escalation.
12. **Exact-candidate review** — independent exact-head acceptance under the
    applicable review contract before merge authority.
13. **CI, security, and completion gates** — trusted CI, security review, and
    allowlisted completion workers. Exact-head evidence alone grants no merge or
    deployment authority; a deterministic effect executor may perform an explicitly
    owner-authorized scoped merge or deployment only after all current gates,
    ledger, idempotency, and writer-fence checks pass.
14. **Deterministic effects** — GitHub/Slack writes through policy effect keys.
15. **Reconciliation** — ambiguous provider or side-effect outcomes enter explicit
    reconciliation; never blind retry.
16. **Terminal reporting** — durable task record and owner-visible summary.

Failure routes are explicit: retry (when idempotent), quarantine, owner decision,
cancellation, or terminal failure. No model chat controls routing.

## Deterministic policy remains authority

`src/director_orchestrator/policy.py` and deterministic code own approval,
repository, branch, cost, timeout, hop, revision, exact-SHA, CI, security, budget,
safety, and owner gates. Model output is untrusted data.

- Models cannot widen permissions, bypass exact-SHA review, skip CI or security
  gates, reset budgets after restart, revive cancelled work, or approve their own
  implementation.
- Checkpoint rollback restores workflow state; it does not undo commits, provider
  runs, Slack messages, or GitHub writes.
- Provider identity never grants authority; all providers consume the same
  validated work contract.

## Persistence: Postgres, thread IDs, namespaces

**Production** requires a dedicated PostgreSQL LangGraph checkpointer, separate from
legacy task tables and effect ledgers.

| Requirement | Rule |
| --- | --- |
| Checkpointer | Dedicated Postgres store; not shared with unrelated services |
| Thread ID | Stable, task-derived; survives restarts and redeployments |
| Namespace | Versioned deployment/graph namespace; distinct from shadow and legacy |
| Schema migrations | Planned, tested, and reversible; recorded in activation evidence |
| Backup/restore | Mandatory before cutover; restore drill included in rollback evidence |
| Secret exclusion | No secrets, tokens, or raw credentials in checkpoints or traces |

**Local test only:** SQLite file checkpoints and in-memory checkpointers. They do
not satisfy production activation gates and must not be cited as cutover evidence.

Pre-primary shadow and packet-runtime SQLite paths remain historical local-dev
artifacts until Postgres cutover evidence replaces them.

Graph checkpoint stores and effect ledgers are **separate durable stores**. Do not
claim atomic cross-store commits unless a later implementation proves one shared
Postgres transaction covering both.

## External effects: intent ledger, idempotency, reconciliation

Every external effect (provider dispatch, Slack write, GitHub mutation, callback
mutation, merge/deployment action) requires:

1. **Durable intent** — commit durable effect intent to the ledger **before** the
   external call.
2. **Budget reservation** — dispatch and review cost reserved before paid work.
3. **Stable idempotency key** — same key replays durable outcome or stops.
4. **Ledger consult on graph replay** — when a node reruns after interrupt or
   restart, consult the effect ledger by the stable idempotency key; do not blindly
   re-execute if intent or terminal evidence already exists.
5. **Fail-closed on drift** — any crash window, checkpoint-ledger drift, or
   disagreement between graph checkpoint state and ledger rows enters fail-closed
   reconciliation and remote readback; never blind retry.
6. **Terminal evidence** — authenticated callback or remote readback bound to the
   original intent bindings.
7. **Reconciliation for ambiguity** — timeout, process loss, or conflicting callback
   moves to `RECONCILIATION_REQUIRED`; no blind resend.

The primary effect executor adds an append-only execution marker between intent
reservation and the mutating call. Exactly one current writer can create that
marker. A restart that sees the marker but no remote binding records ambiguity
and never calls the mutation again; a restart with a binding performs bounded
readback only. The mutating and readback transports are explicit injected
dependencies with fixed timeouts. Live startup fails closed when either is
missing, and `/health.langgraph_primary.effect_executor_ready` must be true for
`primary_runtime_active` to become true.

Ambiguous effects never receive a second unreserved attempt. Owner or authenticated
readback must settle reconciliation before progression.

## Single-writer lease and fencing

Exactly one engine may own provider dispatch, Slack/GitHub writes, callback
mutation, merge/deployment actions, and task-state transitions for a task.

- A **durable single-writer lease** with **fencing generation** protects every
  writer edge. A stale generation fails closed.
- **Dual writer ownership is a no-go.** Legacy intake must be disabled before the
  graph writer lease is acquired for new work.
- Never run legacy and graph dispatchers against the same active task as a
  "comparison."
- `/health` must report checkpoint, writer, and reconciliation readiness before
  cutover; health never exposes secrets.

## Interrupts, approvals, and idempotent side effects

LangGraph may resume an interrupt by rerunning a node. Therefore every
pre-interrupt side effect must be idempotent under replay.

Approval and callback resumes bind:

- task ID and assignment ID;
- writer generation and scope;
- candidate digest/SHA;
- actor identity and expiry.

Stale approval, rebinding, or scope mismatch fails closed. Interrupt resume does
not transfer writer ownership to model output.

## Provider roles

| Provider | Role |
| --- | --- |
| **OpenAI / Codex** | Default runtime Director; bounded engineering and eligible independent review |
| **Claude** | Engineering/review peer; alternate runtime Director only when explicitly activated |
| **Cursor** | Full-spectrum implementation and review peer |
| **Grok** | Independent xAI lane; adversarial and security value; Grok Bot and Cursor-hosted models are separate routes |
| **Gemini / Antigravity** | Isolated implementation, simulation, behavioral validation, bounded debate |
| **Perplexity** | Public sourced evidence only; no private data expansion |
| **Firecrawl** | Public repository-research acquisition where the selected profile permits it |
| **GitHub Copilot** | Bounded hygiene where the selected profile permits it; distinct from retired Microsoft 365 Copilot |

**Acceptance rule:** a producer cannot independently accept its own exact candidate.
Exact-head acceptance requires a different provider than the implementation producer.

## Native Long Table canary evidence

The Native Long Table canary proves the full all-provider graph on an isolated lane.
It is evidence for orchestration graduation only. **Physical Quest validation** is a
separate gate and is not satisfied by this canary. Passing the canary does not
claim production readiness, deployment permission, or permission to merge product
`main`.

**Accepted Native Long Table source baseline (canary authority):**

| Field | Value |
| --- | --- |
| Commit | `4d26680fb99abacb20ecb18807b7c8833646cfa3` |
| Tree | `1e0db56f1e85d66c93ab64bc3dca8c237947c210` |
| Source worktree | `C:\Users\edwar\.codex\worktrees\long-table-claude-provenance-004` |

**Canary target:**

| Field | Value |
| --- | --- |
| Worktree | `C:\Users\edwar\.codex\worktrees\long-table-native-langgraph-canary` |
| Branch | `codex/native-langgraph-canary` |

**Prohibited canary authority:** do not use stale native-first-deal commit
`fb729579` or its branch as canary authority. Those identifiers are historical only.

Procedure:

1. Bind the accepted source baseline commit and tree above as the immutable authority
   for Native Long Table canary intake.
2. Execute in the isolated canary worktree and branch listed above.
3. Run the scoped graph with applicable qualified providers, deterministic IDs
   and Postgres checkpointing in the selected isolated canary namespace. A zero
   allocation target does not permit filler assignments to manufacture coverage.
4. **Freeze exact head** — record commit SHA and tree digest at canary completion.
5. Require **targeted and full tests**, Unreal build/automation evidence where
   applicable, trusted CI, security review, **contract-eligible independent exact-head
   acceptance**, restart/Docker/network recovery drills, **zero duplicate**
   provider/Slack/GitHub effects, and rollback evidence with preserved ledgers.
6. **Merge scope:** the canary may merge only an **isolated test-only candidate**
   when separately owner-authorized. It may **never** merge product `main` or
   deploy merely because the canary passes.

## Activation

LangGraph becomes primary only after **all** gates pass and owner accepts evidence:

1. Independent review and trusted CI/security on the exact candidate SHA.
2. Postgres checkpointer live with backup/restore drill recorded.
3. Distinct namespaces for deployment, graph, checkpoint, and effect ledger stores.
4. **Legacy intake disabled** before acquiring the graph writer lease for new tasks.
5. Pre-cutover tasks **bound or explicitly migrated**; no orphaned dual ownership.
6. `/health` shows checkpoint, writer, and reconciliation readiness.
7. Kill switch blocks new graph tasks and reconciles in-flight effects.
8. Long Table canary evidence bundle complete (see above).
9. Owner/operator acceptance recorded with exact commits, profile IDs, store digests,
   provider run IDs, costs, and rollback drill results.

Merged code or documentation alone satisfies none of these rows.

## Rollback

Rollback preserves evidence and never deletes ledgers to enable retry.

1. Stop new graph intake; engage parallel kill switch if active.
2. Allow in-flight effects to complete or move through authenticated reconciliation.
3. Do not hand unresolved packet assignments to the legacy engine.
4. Disable graph writer and live dispatch; confirm legacy sole-writer for new tasks.
5. Export checkpoint, task, and effect state; switch deployment flag to last accepted
   engine; restart isolated profile.
6. Reconcile provider runs and Slack/GitHub writes by idempotency key.
7. Resume only after owner review; preserve checkpoint and ledger databases as evidence.

Destructive rollback (ledger deletion, checkpoint wipe, secret-bearing export) is
prohibited.

## Explicit no-go conditions

Do not activate or widen the canary when any condition holds:

| No-go | Reason |
| --- | --- |
| Dual writers | Race on dispatch, Slack, GitHub, or task state |
| Missing durable Postgres checkpointing | Cannot survive restart or prove recovery |
| Unreserved external effects | Duplicate charge or write risk |
| Ambiguous paid retries without readback | Blind resend violates reconciliation policy |
| Stale evidence | Acceptance bound to wrong SHA or digest |
| Approval rebinding | Stale or widened approval scope |
| Secret-bearing checkpoints | Credential leakage and rollback contamination |
| Budget reset after restart | Cost escape |
| Revived cancelled work | Policy violation |
| Destructive rollback | Evidence loss and unsafe retry |
| Stale native-first-deal authority (`fb729579`) | Wrong Long Table baseline |

## Relationship to runtime switches

Pre-primary documents described SQLite-backed packet runtime switches
(`LANGGRAPH_RUNTIME_*`, shadow flags). Those paths are **historical local-dev
evidence**. Primary activation targets Postgres checkpointing, graph-coordinated
lifecycle, and the writer lease described here.

Until activation evidence passes, treat any `LANGGRAPH_*` switch in a deployment as
operator-local unless health and ledger inspection prove otherwise. Do not present
contract-only or default-off features as active production behavior.

## Source architecture and review boundaries

The accepted release must expose distinct request and result boundaries around
its internal checkpoint state, preserve compatible persisted keys and keep
sequential parent-node fields under LastValue semantics. Parallel assignment
coordination must not introduce concurrent writes or additive reducers for those
fields without a separately reviewed contract. Bind proof of these requirements
to the exact accepted release and its schema/regression tests; an implementation
assignment or this document alone does not prove that the schemas have landed.
Typed ingress and provider packet contracts remain binding independently.

Ordinary repository PRs follow the current [agent rules](../AGENTS.md): one material
non-author reviewer records exact-head ACCEPT and performs the authorized merge.
That does not remove provider identity separation required by runtime acceptance
issuers or completion receipts. Runtime provider evidence must satisfy its actual
binding contract; a PR comment is not a provider activation receipt.

Workload targets come from `allocation_profiles.py`, selected per intake. The
engineering profile assigns OpenAI/Claude/Cursor/Grok/Antigravity 40/25/15/10/10;
its other lanes have zero targets. Other profiles enable research or hygiene as
needed. Preserve the twenty-real-assignment denominator, soft tolerance and hard
limits; use neither fake assignments nor automatic redistribution.

## Active documentation guard

`python scripts/check_active_documentation.py` checks active Markdown and YAML under `docs/`
and `prompts/`, plus root README/agent instructions. It excludes `docs/archive/**`.
Its semantic rules target obsolete fixed allocation, unconditional legacy-writer
claims and retired blanket provider restrictions; this is a focused regression
check, not a proof of every statement or current deployment state.

A necessary historical quotation outside the archive can use an explicit HTML
comment pair `director-doc-history BEGIN: <reason>` / `director-doc-history END`
around only the quotation. The reason is mandatory; nested, unmatched or empty
blocks fail validation. Reviewers must reject a history exemption used to hide
current instructions. The checker does not exempt whole dated files automatically.

## Work ownership

- **Owner / Director:** authority, exceptions, cutover, rollback, final acceptance.
- **Control-plane implementation:** graph, Postgres persistence, state mapping,
  compatibility, recovery.
- **Provider adapters:** translate neutral packets; do not own workflow state.
- **Independent review:** behavior, security, evidence, provider parity.
- **Project consumers:** validate orchestration cannot alter product canon or
  evidence meaning.

No model approves its own migration PR or accepts its own exact candidate.
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:50 UTC | END]] -->
<!-- [[Cursor Agent | Composer | 2026-08-31 | 09:55 UTC | END]] -->

## Windows Docker activation tooling

See [primary staging and generation controls](primary-staging-controls.md) for
explicit image preparation, identity-only bootstrap, immutable generation
selection, and the evidence-only service boundary.

The following tools are operational mechanisms, not activation evidence by
their mere presence:

- `scripts/windows-primary-snapshot.ps1` creates a new snapshot only when its
  target does not exist, or verifies an existing clean snapshot against an exact
  repository, starting ref, commit, and tree. It never resets or replaces an
  existing checkout. When a source checkout has multiple remotes,
  `-SourceRemoteName` selects one explicitly; creation requires that remote to
  be an exact GitHub URL for the allowlisted repository and requires its live
  starting-ref head to equal the pinned commit before creating the target.
  Snapshot verification uses `LANGGRAPH_PRIMARY_PROJECT_EXECUTION_PINNED_*`
  when both are set; otherwise it reuses `LANGGRAPH_PRIMARY_PINNED_*` for the
  allowlisted repository worktree while service acceptance keeps the Director
  candidate pins.
- `scripts/windows-host-provider-bridge.ps1 -Action Preflight` verifies the
  allowlisted Windows command map without invoking a provider. `-Action Run`
  starts the authenticated host bridge in the foreground for supervision.
  `-Action CheckEnvironment` prints the per-lane production configuration
  digests and makes no provider call. It opens no validation route, so it
  omits `antigravity`, which the bridge validates only through the team route,
  and omits `openai` when `DIRECTOR_PROVIDER_VALIDATION_OPENAI_RESPONSES_CONFIG`
  is set; for those lanes the bridge binds the team fingerprint or the
  Responses route digest, which only the authenticated
  `GET /v1/provider/configuration-digest` endpoint reports. Host-command
  provider children and those digests read only ten operating-system names
  (`SYSTEMROOT`, `WINDIR`, `PATH`, `PATHEXT`,
  `TEMP`, `TMP`, `COMSPEC`, `LOCALAPPDATA`, `APPDATA`, `USERPROFILE`) and the
  lane credentials (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
  `XAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GH_TOKEN`,
  `GITHUB_TOKEN`). When the profile sets
  `DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE`, the bridge, before any
  digest or provider run, replaces the ten names with the pinned file's values
  and drops every inherited lane credential. A lane credential then reaches a
  provider only when the bridge profile sets it and names it in
  `DIRECTOR_HOST_PROVIDER_PROFILE_AUTH_NAMES` (comma-separated, no spaces); the
  launcher copies that value from the profile and refuses an unknown or unset
  name. With a pin, the digests depend on the pinned file and the profile, not
  on the launching shell. Without a pin, the launching shell's values are used.
  The `openai` lane's native Codex child is not a host command: besides the ten
  names it inherits `HOME`, `PROGRAMDATA`, `PROGRAMFILES`, `PROGRAMFILES(X86)`,
  `LANG` and `LC_ALL` from the bridge process. Those six are neither pinned nor
  hashed, so that child still follows the launching shell or Scheduled Task.
  The pinned file is a UTF-8 JSON object of at most 64 KiB at an absolute path
  with exactly two keys: `version`, equal to
  `director-native-process-environment/v1`, and `environment`, an object with
  exactly the ten names above. Every value is a non-empty string without CR, LF
  or NUL, and every `PATH` entry and the `TEMP`, `TMP`, `SYSTEMROOT` and
  `USERPROFILE` directories must exist. Any other file refuses startup without
  echoing its content.
  `-Action Install -InstallRoot <dir>` requires the pinned file and a clean
  checkout: the runtime paths it copies (`src/director_orchestrator`,
  `docs/schemas`, `docs/provider-profiles`, `prompts` and the launcher) may hold
  no modified, untracked or ignored file other than bytecode caches. It copies
  the bridge runtime into a content-addressed directory, records the source
  commit and tree beside it in `<digest>.source.json`, runs `CheckEnvironment`
  from the copy and registers a restart-on-failure Scheduled Task without
  starting it. Install is persistent host configuration and needs owner
  approval. Generations must pin the digests printed by the installed copy,
  and for the routed `openai` and `antigravity` lanes the digests the running
  bridge's authenticated endpoint reports.
- `scripts/windows-langgraph-primary-control.ps1` provides separate `Preflight`,
  `Accept`, `Migrate`, `Backup`, `RestoreDrill`, `Health`, `Activate`,
  `ActivateOffline`, and `Rollback` operations. Every invocation requires the exact environment-file path,
  Compose project, and host port; profile identity is never inferred.
- `scripts/windows-postgres-test-lane.ps1` starts, inspects, exports, tests
  against, and removes one disposable `postgres:17-alpine` container pinned to
  the same digest as Compose and CI, bound to `127.0.0.1:55433` with trust auth
  for the throwaway `director_ci` role, mirroring the CI Linux lane. It exists
  only to satisfy `LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN` and
  `LANGGRAPH_RUNTIME_POSTGRES_TEST_CONTAINER` for the local unit-test run. It is
  not the primary database, records no activation evidence, never reads an
  environment file, never starts Compose services, and refuses to touch any
  container it did not label.

The Windows host bridge is an optional narrow transport for supported native
CLIs. It uses a secret distinct from API, Slack, callback, reconciliation, and
writer secrets. Requests bind provider, task, assignment, canonical packet
digest, hashed idempotency key, and current writer generation. The bridge
commits a durable claim before one `shell=False` subprocess attempt. Successful
outcomes replay from the durable result; timeouts, process loss, nonzero exits,
or malformed output become ambiguous and are never resent.

The container client accepts only an exact `http` URL at
`/v1/provider/dispatch`, with an explicit port and the host
`host.docker.internal` or an exact loopback address. User information, query
strings, fragments, alternate paths, remote hosts, TLS-looking aliases, and
redirects are rejected. Immediately before any child-process I/O, the Windows
bridge reads the authoritative Postgres task-writer row and requires the
LangGraph owner, signed owner-token digest, exact fencing generation, and a live
lease. A missing, stale, inaccessible, or mismatched row fails closed. The child
receives only a minimal Windows process environment plus the selected lane's
explicit authentication variables; Director API, bridge, callback,
reconciliation, and shared secrets are never inherited. Each profile must set
`DIRECTOR_HOST_PROVIDER_POSTGRES_DSN` to its own reachable primary database;
that host-side DSN is a secret-store value and must never be copied into logs or
checked-in profile files.

The bridge currently permits only explicitly configured Claude, Grok,
Antigravity, and GitHub Copilot packet-wrapper commands. Cursor remains direct and is never aliased through the Windows bridge.

Endpoint and secret configuration alone never makes a host-bridge lane callable.
Before composing adapters, the container sends a fresh HMAC-authenticated
challenge and verifies the signed, digest-bound configured-provider command map.
Missing, stale, redirected, malformed, unsigned, or mismatched challenge evidence
leaves every affected bridge lane unavailable. This liveness challenge performs
no provider subprocess or paid action.

Independent exact-head review, trusted CI, and security results are stored as one
immutable completion-evidence bundle in the primary Postgres control plane. The
bundle is accepted only under the current unexpired LangGraph writer fence and is
bound to the task, candidate SHA, provider-result digest, gate-ledger digest, and
individual result digests. Independent review, CI, and security each use a
different secret, issuer, and authenticated principal; one aggregate signer is
not an authority. Restart readback revalidates the canonical content and all
three distinct authority proofs;
missing or conflicting evidence remains `OWNER_DECISION_REQUIRED` and can never
be replaced with a synthetic pass.

For every profile, execute the control actions in this order while Slack intake
is closed: `Preflight`, `Accept`, `Migrate`, `Backup`, `RestoreDrill`, provider
activation evidence, then `Activate`. Under the default `github-v1` acceptance mode, `Accept` reads the canonical GitHub pull
request, requires its head to equal the frozen candidate, requires every named
CI check to be completed successfully with no skipped/pending/failed check, and
requires distinct non-author security and independent-review ACCEPT artifacts
that both name the exact head. Each must be an `APPROVED` review from the
corresponding configured, disjoint GitHub actor allowlist with an acceptable
GitHub author association, and its body must exactly equal the canonical
`director-activation-review/v1` JSON artifact. Comments, quoted markers, and
free-form prose never qualify. For alternative modes, see the separately signed
[provider-acceptance-v2](provider-acceptance-v2.md) and
[two-party-activation-v3](two-party-activation-v3.md) contracts. Under `github-v1`, the CI, security, and independent-review issuers
must supply a closed canonical artifact with a canonical facts digest and their
separate domain-separated HMAC attestations.

The `github-v1` control process does not mint those attestations: it requires
`-AcceptanceArtifactPath`, independently reads
GitHub, and fails unless the supplied artifact exactly matches the observed
repository, pull request, head, checks, and reviews. `ACCEPTED` accepts only that
exact artifact path;
raw `--facts-json` and caller-selected `--evidence-digest` values are rejected.

With explicit owner authorization, `owner-local-evidence-v1` instead adopts an
exact release's retained local QA, independent review and security evidence. It
keeps hosted checks recorded as `HOSTED_NOT_EXECUTED`; actual failed run records
remain in the pinned evidence bundle. This is owner adoption, not a GitHub or
provider signature. Default `github-v1` and both signed provider modes are unchanged.

Mount the owner-protected evidence directory read-only in **every** Compose
invocation and the persistent service. Set `LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_PATH`
to its container-side `adoption.json` and pin its bytes with
`LANGGRAPH_PRIMARY_LOCAL_EVIDENCE_ADOPTION_SHA256`. The closed adoption document
uses `director-primary-local-evidence-adoption/v1`, decision
`ADOPT_SCOPED_LOCAL_EVIDENCE`, and the existing owner token's domain-separated
signature. Its QA, independent review and security digests must identify actual
files in `pinned_evidence`; each path is a simple filename beside the adoption
document. Loading enforces at most 32 unique pins, a 64 MiB aggregate bundle
limit, and an 8 MiB per-file cap before hashing. Loading and replay verify those file bytes. Reviewed source must match
the candidate tree. An earlier full-suite receipt remains earlier evidence;
changed code needs fresh focused QA and current-head independent/security review.

The acceptance artifact uses version
`director-primary-acceptance-artifact/owner-local-evidence-v1` and a single
`owner_adoption` attestation from `owner-local-evidence-adoption`, signed with the
existing owner token over its canonical unsigned artifact using the
`director-primary-acceptance/owner_adoption/owner-local-evidence-adoption/v1` domain.
The control command checks the explicitly supplied repository, PR and reviewer
allowlists. It never manufactures an independent reviewer or security signature.

Select a fresh owner-pinned generation with version
`director-primary-generation/owner-local-evidence-v1`; it retains the existing
generation fields, substituting `owner_adoption_path`/`owner_adoption_sha256` for
the provider registry pair. All image, resolved Compose, configuration, provider
binding, candidate and database checks still run. Its SQLite generation marker
has a companion `.evidence.json` file for the ordered activation history, so older
generations are preserved. The existing `SelectGeneration`, `Preflight`, `Accept`,
`Migrate`, `Backup`, `RestoreDrill`, staging and activation sequence applies.
Startup verifies readiness and generation before opening the primary database.
This path changes no enrollment, budget, provider qualification or Slack delivery
requirement.
The durable record retains the source artifact and its digest for deterministic
offline replay. Every reader needs the selected mode's verifier secrets:
`github-v1` requires three distinct authorities, while `owner-local-evidence-v1`
requires the owner adoption authority. Replay revalidates the full closed artifact
and its attestations before readiness can pass. Acceptance signatures detect
artifact substitution. Later stage digests detect inconsistent or corrupted
facts; their public hashes do not authenticate someone who can rewrite both
facts and hashes. Protect the durable evidence volume and generation pins.
Operator-set booleans are not acceptance evidence.

For `github-v1`, before issuer signing, `windows-langgraph-primary-control.ps1 -Action
PrepareAcceptance -GitHubRepository owner/repository -PullRequestNumber 98
-CandidateCommit <exact-40-character-PR-head>
-SecurityReviewerAllowlistJson '["security-login"]'
-IndependentReviewerAllowlistJson '["independent-login"]' emits an unsigned JSON
request to stdout. Supply operator-authorized nonsecret reviewer allowlists;
these inputs do not grant GitHub membership or create a review. Preparation
fetches the candidate tree from GitHub and reuses `Accept`'s exact check/review
validation and serialization. Missing, failed, stale, unaffiliated, prose-only,
or author-self-acceptance evidence emits no request. Preparation accepts no
runtime environment file or prebuilt facts, reads no deployment secrets, starts
no Docker services, writes no files, and does not record an activation stage.
The unsigned five-field request lacks `attestations`, so the existing verifier
rejects it as an acceptance artifact. This is preparation tooling, not a
production signer or proof of acceptance.

Production issuer provisioning remains required. Two independently controlled,
non-author GitHub identities must provide the exact canonical APPROVED review
bodies above and have an eligible repository association. A shared owner token,
provider prose, CI check name, or environment flag cannot supply those identities.
Each issuer must independently refresh and validate the request's GitHub facts,
and authenticate its own principal in its own execution context. The CI issuer
needs a deployment that binds authenticated GitHub Actions provenance to the
exact successful run/candidate; no such identity integration is implemented here.
Only after provisioning should each issuer sign its own role using its separately
held role secret. The coordinator must never generate all three signatures.
The three distinct verifier secrets remain provisioned through the existing
secret store/environment mechanism; preparation neither accesses nor creates them.
Assemble only attestations over the identical canonical unsigned request, with
issuer names matching the facts and domain-separated signatures specified by
`primary_activation_evidence.py`. Submit the completed package through unchanged
`Accept`, which rereads GitHub and verifies all signatures. A facts change requires
fresh issuer validation and signatures. This tooling does not supply reviewer
accounts, signing credentials, deployment authority, or a fallback acceptance mode.

Activation reopens Slack only after the observed health
projection proves Postgres, writer fencing, reconciliation, current provider
activation, graph ownership, and zero ambiguous effects. It creates the Slack
gateway stopped from the verified candidate image, reads back the created
gateway's image ID, records `ACTIVATED`, and only then starts n8n and the
gateway, so a failed readback or evidence write unwinds with Slack and n8n
intake still closed. `ActivateOffline` needs the same
acceptance, migration and restore evidence but not provider readiness: it turns
offline simulation on and paid runs and live dispatch off, requires the paid-off
intake projection with the live runtime inactive, and records no `ACTIVATED`
stage. A later paid `Activate` can reuse that evidence chain only while the
legacy SQLite task ID/status inventory is unchanged: it binds the inventory
again and refuses any difference as immutable. Legacy tasks stay live under
primary (startup and periodic recovery of queued, planning, running and
reviewing legacy tasks, legacy approvals and callbacks), so a legacy status
change during offline operation means paid activation needs a new generation.
Rollback stops Slack and the orchestrator, waits for the writer lease to expire,
refuses when ambiguous effects exist, performs the fenced generation change back
to legacy, and preserves the primary database. It then recreates the
orchestrator on the image recorded before the first candidate recreate (by
`StageRuntime`, or by activation when no staging ran) in
`<profile>.prior-images.<generation>.<image>.json`, reads its image ID back and
waits for health, creates the gateway stopped on the recorded gateway image and
reads it back, and only then starts n8n and the gateway. If a readback or health
check fails after the compare-and-swap, it stops and proves Slack, n8n and API
intake closed again before it reports the failure. An invalid record refuses
rollback before anything stops. With no record, the fenced return to legacy
still runs on the candidate image with paid runs and primary off, and the tool
reports that no image was restored.

The control tool persists `/data/langgraph-primary-activation-evidence.json` as
an atomic versioned state machine. Its identity binds the exact Director
candidate commit/tree, profile, Compose project, opaque Postgres storage
identity, schema, and checkpoint namespace. The only accepted sequence is
`PREFLIGHT -> ACCEPTED -> MIGRATED -> BACKED_UP -> RESTORE_PROVED -> ACTIVATED`; missing,
duplicate, out-of-order, corrupt, cross-candidate, cross-profile, or cross-store
evidence fails closed. Restore proof requires all named primary control-plane
tables, all four LangGraph checkpoint tables, and a nonzero control-plane schema
row from the restored disposable database. A successful command exit or a zero
table count is not restore evidence.

`RestoreDrill` copies the selected dump to its random container staging path,
hashes those exact staged bytes, and compares the lowercase `sha256:` digest to
the prior durable `BACKED_UP` record under the same activation identity before
database creation or `pg_restore`. Missing files, mutated dumps, and substituted
but otherwise valid dumps fail closed. `RESTORE_PROVED` records the same bound
backup digest, so a later activation cannot splice restore evidence from another
artifact.

Before rollback changes the intake generation, the Postgres quiescence query
must prove zero unresolved effect/provider reservations, zero pending/active/
ambiguous parallel assignments, zero live task-writer leases, zero unresolved
reconciliation claims, zero unsettled claims, and zero ambiguous effects. The
legacy engine never receives tasks merely because the intake lease expired.

Preflight first bootstraps only the profile's PostgreSQL service and persists a
database-instance UUID inside the control-plane singleton. Activation evidence
binds both the canonical connection target and that instance UUID, so replacing
a volume or server behind the same DSN invalidates the earlier evidence chain.

Immediately before cutover, the control tool stops and verifies all authenticated
intake surfaces: Slack, n8n, and the API orchestrator. It then hashes the complete
legacy SQLite task ID/status inventory and durably binds that immutable snapshot
to legacy ownership before `require-ready` may pass. Pre-cutover tasks remain
legacy-owned; only the fenced new-intake generation transfers to LangGraph.

If any post-start activation check fails, including the gateway image readback,
the tool stops and proves Slack, n8n, and API intake closed, and restores the
paid, live, offline-simulation and new-intake switches to their closed values. Rollback uses unconditional runtime exceptions for
ambiguous-effect and quiescence failures; Python optimization cannot remove the
safety checks.

The checked-in local Docker Compose topology uses `sslmode=disable` only for the
exact service hostname `langgraph-primary-db` and only under the explicit
`LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=true` exception injected by
Compose. Loopback remains allowed for local tests. Lookalike names, arbitrary
container names, private-network IPs, mixed remote `hostaddr` values, and every
other non-loopback target fail closed. Managed or remote production PostgreSQL
must use `verify-ca` or `verify-full` with an explicit CA root; the local Compose
exception is not production TLS evidence.

## Default parallel assignment lifecycle

For live Default intake, the configured provider list is an available pool, not a
requirement to dispatch every provider. Current authenticated app and adapter
readiness must cover OpenAI orchestration and at least two other primary providers
capable of implementation and independent review. OpenAI production is explicitly assigned under the frozen allocation profile;
control-plane work creates no production credit and material authors cannot self-review. The planner freezes one implementation provider and a
different reviewer before the existing exact-plan execution approval. Eligible
providers exclude only zero-target lanes; the retired rolling-twenty hard cap no
longer blocks admission, and the normal band guides preference, then
operator-configured comparable cost ranks break ties. Pair selection projects
research (when requested), production, then review in dependency order so rollover
can unlock an independent reviewer; these projections never create ledger rows. Missing cost authority
holds selection. Dispatch rechecks admission atomically; it never substitutes a
new provider after approval. Concurrency remains the configured initial bound,
independent of the inventory size.

Structured `primary_required_capabilities` on authenticated intake can request
`public_research`, `enterprise_context`, `bounded_scaffolding`, or
`repository_assistance`; private task prose never grants these scopes.
Public research requires `primary_public_research_query`, selects an eligible
Perplexity or Firecrawl contact under the frozen profile,
and precedes implementation. Its outbound task contains only that explicit public
query. Enterprise context stops when its scoped adapter is unavailable:
allocation targets do not supply missing enterprise adapter capability or source
authorization. Enterprise lanes are never aliased into another provider's allocation. Missing requested scope/readiness similarly holds.

Startup uses the owner-approved full twenty-slot denominator with only real
assignments. Primary and legacy admission retain real reserved assignments;
accepted productive output remains separately recorded. Neither ledger fabricates
rows. Recognized genuine v2 primary constraints may be upgraded to the new profile
policy while preserving existing rows. Unknown or incompatible schema/history
fails closed; the v1-only table still requires explicit remediation before any
provider attempt. Legacy reservations without frozen profile-admission provenance
require explicit reconciliation before new admissions, preserving their history.
Mature windows still assess last nineteen plus the prospective assignment
against the normal ±3-point band as an advisory preference; the owner retired
the target+5 rolling-twenty hard ceiling on 2026-09-21.
The approved Engineering profile replaces the former unsustainable core-only
allocation: OpenAI35/Claude30/Cursor15/Grok10/Antigravity10. The full matrix,
role boundaries, zero-target rule and preserved-history profile changes are in
[Provider allocation profiles](provider-allocation-profiles.md). Real authenticated
adapter capability and independent review remain prerequisites; percentages alone
do not prove operational throughput or authorize deployment.

The Default primary application registers this complete assignment DAG in the
Postgres parallel ledger before an execution approval can cross the provider
boundary. After approval, the historical single-assignment graph edge is an
interrupt only: it never owns provider transport. The parallel ledger is the sole
dispatch owner and commits each assignment reservation before invoking its exact
packet adapter.

Each wave concurrently consumes every dependency-ready assignment allowed by
the configured task, provider, deployment, and write-surface caps. A dependent
packet is built only after all named upstream result packets are durably accepted.
Unknown provider exceptions are ambiguous and are never resent. Only the trusted
transport's typed pre-dispatch temporary failure permits recovery. Each assignment
has at most three attempts and stops immediately on authenticated success. Attempt
two may reuse a transport after that temporary failure. Attempt three requires a
different underlying transport category; format changes and HTTP bridge wrappers
around the same native command do not qualify. The live path has no trusted new
information input and therefore cannot use that alternative as retry authority.
The writer-fenced Postgres reservation rechecks the stored prior outcome and
transport before inserting the next STARTED event, before provider I/O. A prior
success, active or ambiguous attempt cannot be redispatched after restart. Three
recorded failures exhaust the assignment route; the assignment fails closed for
configured fallback or owner routing rather than silently selecting another paid
provider. An ambiguous paid dispatch stops immediately for reconciliation.
The graph task is successful only when the deterministic aggregate proves every
assignment succeeded; a pending, active, ambiguous, failed, or dependency-blocked
member cannot be reported as successful completion.

## Authenticated recovery and completion ingress

The periodic recovery worker heartbeats only task-writer leases touched by its
current process. A live database-clock lease proves normal `DISPATCHING` or
`EXECUTING` work is still owned and is never recovered by the sweep. Only a
nonterminal task with an absent or expired lease may be taken over through the
next fencing generation and reconciled; terminal tasks are ignored.

Cancel and recover commands require a fresh Slack-channel HMAC proof bound to
the exact route, task, command body, projection digest, writer generation,
actor, timestamp, and nonce. The nonce and command identity are claimed in an
append-only Postgres row under that live writer fence before state changes. An
exact redelivery is idempotent; nonce or command rebinding is rejected.

Completion evidence enters through the signed adapter-callback route only when
the transport actor equals the evidence actor. The route binds the exact task,
candidate SHA, provider-result digest, projection, and writer generation, then
uses the immutable writer-fenced completion store. Evidence without a valid
signature, exact gate/result digests, current writer lease, or exact replay
identity cannot authorize successful completion.
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:06 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:42 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 11:32 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:11 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 18:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:12 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:31 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:40 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:46 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 00:03 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-02 | 08:20 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-03 | 10:07 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-04 | 11:14 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-04 | 17:28 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]] -->
<!-- [[Codex | GPT-6 | 2026-09-05 | 23:45 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 07:55 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:50 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | END]] -->
<!-- [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:15 UTC | END]] -->
<!-- [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 01:12 UTC | END]] -->
<!-- [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:35 UTC | BEGIN]] -->
<!-- [[Cursor Agent | Composer 2.5 | 2026-09-22 | 05:35 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:11 UTC | END]] -->

<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:40 UTC | END]] -->

<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 06:42 UTC | END]] -->
<!-- [[Cursor Agent | Composer 2.5 | 2026-09-22 | 08:15 UTC | END]] -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 16:58 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:52 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | END]] -->
