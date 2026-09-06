<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | BEGIN]] -->
# Antigravity team engineering boundary

**Draft implementation checkpoint; not a foundation-adoption candidate.**

This candidate adds `antigravity-team-engineering/v1` beside the existing closed
subordinate and debate contracts. The primary-specific
`LANGGRAPH_PRIMARY_ANTIGRAVITY_TEAM_ENABLED` setting defaults to false. Existing
Gemini settings and review flags cannot select this contract.

A typed owner-signed `TeamGrant` binds one v1 work packet, project/repository,
commit/tree and review base, full readable manifest with blob hashes, explicit
writable and prohibited paths, local command vectors, isolated workspace,
native account and Gemini model, complete installation/binding digests,
material-author family, expiry, generation and resource limits. Its signature
does not replace the live entitlement query. Gemini and Antigravity are one
material-author family. The team entitlement template does not compose shared
Slack, Notion, n8n or other integration effects.

`TeamAuthority` reads signed persisted grants and authorizes every manifest path
in the existing entitlement transaction. It checks live revocation, notice,
scope, installed build and generation at disclosure, dispatch, immediate native
execution, each local command and result admission. Review target identity is
separate from the installed reviewer build. The generic work-packet schema is
unchanged; no extension bag carries authority.

`PrimaryProviderBudgetAuthority` stores owner-signed monetary budgets for all
provider lanes. Project/day and task ceilings must agree across registrations
and cannot exceed signed project limits. `ProviderBudgetAdmission` integrates
this authority into the existing primary attempt transaction for other lanes;
`TeamAttemptAdmission` includes it with the team entitlement and nonce claim.
Both use the existing workload ledger and attempt insertion. A caller's budget
digest or amount is not monetary authority. Legacy stage-unit accounting remains
unchanged and cannot authorize team spending. An earlier primary attempt without
monetary accounting blocks new funded attempts until separately reconciled.

Reservations remain fully charged when usage is unknown or less than reserved.
Only a separately authenticated, complete meter receipt records observed cost
and tokens. Higher observed cost increases subsequent budget consumption;
observed token-limit violations hold further project admission. The meter uses
the separately trusted route-verifier secret and a distinct receipt schema.
Provider prose and optional telemetry do not create a meter receipt.

The native adapter requires a protected worker with `prepare` and `exchange`
operations. Before any packet/source export, the worker must produce fresh,
challenge-bound signed containment evidence for unassigned source, Git history,
coordinator credentials, broker keys, launcher mutation and production-network
denial. It must enforce the signed runtime, native account, workspace, resource
limits and local command restrictions. `exchange` calls the execution gate
immediately before invoking AGY and the command gate immediately before each
local command. The durable execution claim is single-use across processes and
nonces. There is no direct local CLI, API-key, custom endpoint or unrestricted
worker fallback in this adapter.

Captured native streams must have exactly one initialization and terminal
completion, one native session, the bound requested model and successful exit.
Review streams reject tool activity; nested provider dispatch is rejected in
both modes. Signed worker changes must be within writable paths, command logs
must match admitted commands, and test evidence must agree with the independent
worker capture. Requested model configuration is not an observed backend-model
claim. Source export reads only manifest files, checks every blob, and rejects
link aliases or extra Git history.

This candidate does **not** supply or provision the protected engineering worker
host. The `prepare`/`exchange` host integration and actual-token containment tests
are required before using the route. Issuer installation, acceptance signatures,
roster promotion, provider qualification, activation generations and live cutover
remain separate packages. Schema/fixture tests do not establish native AGY
execution, engineering qualification, review qualification or primary ownership.

Use `team_packet_adapter(settings, ...)` only for an explicitly selected team
assignment with its grant and authoritative attempt. The existing subordinate
adapter remains the default. Register the team attempt using format identity
`antigravity-team-engineering-v1` (the parallel ledger's identifier form) and
`team_admission=TeamAttemptAdmission(...)`; the signed envelope retains the
versioned contract name with `/v1`.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:19 UTC | END]] -->
