<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | BEGIN]] -->
# Project rollout packet

Create this packet whenever the owner presents a project to the Director. It
records the project-specific deployment boundary even when the project remains
analysis-only. Unknown or unavailable live identities stay `pending`; they are
never borrowed from another deployment.

Do not place secret values, credentials, tokens, private keys, or raw private
source in this packet. Record only approved secret references and readiness
status. The project profile, owner-authored work order, runtime allowlists, and
deterministic gates remain authoritative.

## Project and deployment identity

- **Project ID/name:** <stable ID and name>
- **Repository/ref/worktree:** <exact values or analysis-only>
- **Deployment intent:** <analysis_only | planned | live>
- **Concurrent with other active Director deployments:** <true | false | unknown>
- **Deployment profile:** <ignored environment-file name or not_created>
- **Compose namespace:** <project-specific namespace or not_created>
- **State namespace:** <project-specific database/volume/checkpoint namespace or not_created>
- **Ports and volumes:** <project-specific values or not_created>

## Allowlists and execution boundaries

- **Project allowlist:** <exact project ID or pending>
- **Repository allowlist:** <exact owner/name or pending>
- **Starting-ref allowlist:** <durable ref or pending>
- **Data classification:** <public | private | sensitive | mixed | none | unknown>
- **External effects:** <analysis_only or exact owner-authorized effects>
- **Paid execution:** <disabled | owner-authorized and gated>
- **Automerge/deployment/publication:** <disabled or exact gated authority>

## Slack isolation

- **Workspace ID:** <exact ID or pending>
- **Slack owner-user allowlist reference:** <ignored-profile or secret-store
  reference, or a non-reversible verification digest; exact IDs stay in the
  ignored deployment profile>
- **Runtime channel allowlist:** <exact project channel ID(s) or pending>
- **Dedicated Slack app display name:** <project-specific name or pending>
- **Slack app ID:** <exact ID or pending>
- **Slack bot user ID:** <exact ID or pending>
- **Slack app manifest SHA-256:** <64-hex digest or pending>
- **Installation status:** <not_created | created | installed | verified>
- **Socket Mode status:** <disabled | configured | verified>
- **Dedicated project channel ID:** <exact ID or pending>
- **Bot membership:** <not_invited | invited | verified>
- **App-token secret reference:** `SLACK_APP_TOKEN` in the ignored deployment profile
- **Bot-token secret reference:** `SLACK_BOT_TOKEN` in the ignored deployment profile
- **Concurrent-deployment collision check:** <pending | passed | not_applicable>

Every concurrently active deployment requires a distinct Slack app, a distinct
project channel, and a distinct deployment profile/state namespace. A missing,
reused, or unverified identity permits analysis-only shaping but blocks Slack
gateway activation. Never copy token values into this packet or reuse another
deployment's Slack app, channel, credentials, profile, database, volume, or
checkpoint namespace.

## Provider and workflow readiness

- **Providers enabled:** <provider names and readiness only>
- **Budget and timeout:** <bounded values>
- **n8n/workflow identity:** <project-specific workflow ID or not_applicable>
- **Callback and internal-secret status:** <configured | pending; references only>
- **Usage-monitor status:** <disabled | configured | verified>

## Activation evidence

- **Profile collision check:** <evidence or pending>
- **Slack app/channel verification:** <evidence or pending>
- **Off-channel event rejection:** <event ID and no-consumption evidence or pending>
- **Non-owner event rejection:** <event ID and no-consumption evidence or pending>
- **Replayed event handling:** <event ID and idempotent/no-duplicate evidence or pending>
- **Cross-deployment isolation:** <proof no other active deployment consumed or persisted the event>
- **Paid-disabled health check:** <endpoint/run and result or pending>
- **Slack intake dry run:** <correlation ID and result or pending>
- **n8n route check:** <workflow ID/run and result or not_applicable>
- **Provider checks:** <provider/run IDs and terminal results or pending>
- **Restart/recovery check:** <result or pending>
- **Exact configuration identity:** <commit/digest or pending>

Activation may proceed only through the owner-authorized gates recorded in the
project profile. Evidence is recorded without secret values.

## Communication fallback

Each Slack delivery obeys its immutable assignment-specific `retry_limit` of
exactly 3. Stop on success. Recovery requires temporary failure; a third attempt requires materially new information or substantially different transport, with remaining resource budget. Never retry an ambiguous Slack side
effect; reconcile it first. When the bounded route is exhausted, use
the same project's prior durable communication path—SQLite/API task state and
provider-native run/session evidence. Do not borrow another deployment's Slack
app, channel, thread, credentials, profile, or state namespace as a fallback.

## Rollback

- **Stop command or action:** <bounded project-specific action>
- **Configuration rollback:** <prior profile/config identity>
- **State restore:** <project-specific database/volume/checkpoint procedure>
- **Slack rollback:** <disable only this deployment's gateway/app connection>
- **Evidence retained:** <task IDs, run IDs, logs, backups, and exact identities>

## Disposition

- **Current state:** <analysis_only | blocked | ready for dry run | active | paused>
- **Blocking fields:** <pending identities/evidence or none>
- **Next responsible party:** <Director | provider | owner | deterministic gate>
- **Next bounded action:** <one action>
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-29 | 08:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-08-30 | 02:22 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:04 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 22:51 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:22 UTC | END]] -->
