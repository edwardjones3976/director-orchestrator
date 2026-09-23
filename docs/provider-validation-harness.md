<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:52 UTC | BEGIN]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | BEGIN]] -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:12 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]] -->
# Provider validation harness checkpoint

This package is a validation foundation, disabled by default. Software wiring is implemented; host qualification and real authenticated runs remain required. It makes owner-signed LIVE_OK status alone insufficient. No live provider was called and no production database or host configuration was changed during implementation.

The bridge exposes `/v1/provider/validate` only when explicitly configured. Requests select only a registered generation and provider; commands, accounts, packet contents, costs and namespaces come from the immutable owner-provisioned generation and existing bridge configuration. Postgres admission serializes budgets and allows only one attempt per generation/provider. Ambiguous attempts retain their reservation and cannot be replayed. The dedicated schema is `director_provider_validation_v1`; checkpoint namespace is `director-provider-validation/v1`. Constructor and evidence reads never bootstrap tables. Before admission the bridge compares each lane's current configuration pin with the generation's registered lane digest through a read-only lookup: the native production pin (executable, account, workspace and adapter), the OpenAI Responses route digest (bounded by that lane's shared deadline), and the Antigravity team fingerprint. Drift on any lane therefore creates no attempt row and consumes neither budget nor the lane's single attempt. The post-admission and post-execution checks remain.

`GET /v1/provider/configuration-digest` returns the per-lane configuration digests the bridge would bind, plus whether a pinned process environment is active: the native production digest for host-command lanes, the Responses route digest for `openai` when that route is configured, and the team fingerprint for `antigravity` when the team route is configured. `antigravity` never reports a native digest, because the bridge validates it only through the team route. It requires the validation owner token as a bearer credential (compared in constant time) and returns digests only: no paths, tokens or values.

The explicit operator CLI is `python -m director_orchestrator.provider_validation_cli bootstrap` or `validate`. Bootstrap requires DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN, DIRECTOR_PROVIDER_VALIDATION_MANIFEST and DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN. The manifest has exactly generation, candidate, tree, expires_at (Unix seconds, within 24 hours), budget_units and lanes. Generation is the immutable primary generation manifest SHA256; candidate/tree must match its ActivationIdentity. Each lane contains exactly binding (ProviderActivationBinding fields), configuration_digest, cost_units and work_packet. Canary work packets use the exact candidate, VALIDATION identifiers, no tools/effects, max_hops=1 and max_revisions=0.

Validation additionally requires DIRECTOR_PROVIDER_VALIDATION_BRIDGE_URL with HTTPS and the exact validation path. The bridge requires DIRECTOR_PROVIDER_VALIDATION_ENABLED=true, dedicated capture key and owner token (at least 32 characters, distinct from each other and production authentication), and DIRECTOR_PROVIDER_VALIDATION_ACCOUNT_FILES_JSON mapping provider names to pinned local account/runtime files. Runtime capture verification requires DIRECTOR_PROVIDER_VALIDATION_CANDIDATE and TREE plus the dedicated database and capture key. When DIRECTOR_PROVIDER_VALIDATION_GENERATION is absent, consumers derive it from LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256; an explicit conflicting value is rejected. Keep all values in the existing protected secret/configuration mechanism. Never place credentials in this document or Git.

A capture authenticates raw native output, normalized result, native session, packet, complete activation binding, candidate/tree, generation, reservation, nonce, validation fence and expiry. The bridge checks its configured executable/account-file hashes and workspace/argv/environment identity before and after execution. The cloned executor checks the dedicated durable validation fence immediately before subprocess dispatch and never consumes production writer leases. The same authenticated capture can support both activation surfaces; recording the normal ordered activation stages remains a separate owner-authorized operation.

## Explicit remaining work before activation

* Antigravity full-team validation uses the same pinned SSH worker and shared authenticated readiness/capture checks as production, with a separate validation budget/fence and a signed immutable review canary. Only the fixed synthetic `validation-canary.txt` bytes embedded in the module may be disclosed; commands and writes are forbidden. No production task/attempt/integration table is used. Configure DIRECTOR_PROVIDER_VALIDATION_TEAM_CONFIG with the exact closed fields grant_file, owner_key_file, worker_key_file, ssh, ssh_sha256, host, user, identity_file, known_hosts and worker_command. The registered packet must match the signed grant without rewriting its identifiers. The production installation/account/workspace binding must match the grant and activation binding; this does not enroll a host. The worker independently requires a fresh task-scoped issuer qualification and protected native account/project readback. Generic legacy JSON still cannot qualify this route.
* OpenAI JSONL and configured native JSON terminal routes carry raw bytes through the real host executor. No actual authenticated provider/account run was performed. Provider-specific installed CLI envelopes and native read-only restrictions still require qualification; metadata saying no tools is not an operating-system containment proof.
* Account-file hashes bind credential material but do not independently prove subscription/account identity. The runtime pin currently covers the executable, configured argv files, adapter source and explicitly listed account/runtime files. Operators must supply the full installed runtime closure; configured runtime directories are recursively inventoried with file/byte bounds and aliases rejected. Operators must configure the full installed runtime roots, and native account identity readback remains a host qualification prerequisite. Workspace content immutability is not established by its path.
* ProviderAdapterContext now carries a trusted optional execution observer. Native command, native Codex and team transports expose captured bytes through it. The dedicated validation route consumes its own observer directly, and the team route authenticates the signed worker capture. No provider-controlled work packet may set this callback.
* No signed acceptance package, deployment, provider LIVE_OK record or LangGraph ownership switch is produced here. Staging ValidateProviders may call this CLI only after its own generation/image/restore gates, and must remain disabled until the missing qualification paths are completed and independently reviewed.

Focused tests use a disposable sanctioned Postgres lane and a local synthetic subprocess. These demonstrate database fencing, admission races, budget consumption, replay prevention, raw-output authentication and the production-executor reuse path. They are not native paid-provider evidence.

## Read-only all-lane preflight

`python -m director_orchestrator.provider_validation_cli preflight` (or
`read_only_lane_preflight()` in `provider_validation.py`) projects the manifest
lanes through the static inventory at base `bea596d8` and one synthetic
fixture (`preflight_fixture()`: no tools, no external effects, zero budget
units). It needs only DIRECTOR_PROVIDER_VALIDATION_MANIFEST, never constructs
the store, opens the bridge or contacts a provider, and reports
`provider_calls`, `work_reservations` and `spend_reservations` as `0`. The
inventory lists eight canonical lanes (openai, claude, cursor, grok,
antigravity, perplexity, firecrawl, github-copilot) plus three separate
contacts (Grok Bot, Z.ai, VS Code Coding Bot) whose enrollment stays `UNKNOWN`;
direct Grok evidence never qualifies the Grok Bot contact, Z.ai keeps its
tools-off trial contract, and VS Code must preserve its underlying provider
identity. Perplexity and Firecrawl are research-only with an engineering
target of zero; every allocation weight is read from `allocation_profiles.py`.
Every lane's verdict and `enrollment_and_live_eligibility` stay `UNKNOWN`: a
caller-supplied row is recorded verbatim as untrusted `asserted_evidence`
(reason `asserted_evidence_recorded_not_verified`) and never changes
enrollment, eligibility or the verdict, because no authenticated collector
exists yet; a live canary needs separate owner authorization and is not
launched here.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5 | 2026-09-21 | 21:45 UTC | END]] -->
<!-- [[Auto (Cursor Agent) | Composer 2.5 | 2026-09-22 | 18:12 UTC | END]] -->
<!-- [[Codex Director | GPT-6 Astra | 2026-09-22 | 18:31 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:52 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:20 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 17:51 UTC | END]] -->
