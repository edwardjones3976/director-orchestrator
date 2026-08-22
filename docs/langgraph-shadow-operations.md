**[[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | BEGIN]]**

# LangGraph shadow and approval-resume operations

## Authority boundary

The LangGraph additions are default-off proof lanes. The legacy Director engine remains the sole workflow authority, provider dispatcher, and GitHub/Slack writer. Neither feature flag activates a graph from the API process, and LangGraph cannot approve, resume, dispatch, merge, deploy, or write externally.

The proof has two bounded purposes:

- `LANGGRAPH_SHADOW_ENABLED` permits isolated shadow-state experiments that compare graph transitions with legacy decisions.
- `LANGGRAPH_APPROVAL_RESUME_ENABLED` permits isolated approval pause/resume experiments while existing deterministic policy continues to decide whether an approval is authentic, current, assignment-scoped, and sufficient.

Both flags default to `false`. There is no provider dispatch integration, state migration, production cutover, or provider subgraph in this phase.

## Required isolation

An enabled proof lane requires both:

- `LANGGRAPH_CHECKPOINT_PATH`: a SQLite file different from `SQLITE_PATH`;
- `LANGGRAPH_CHECKPOINT_NAMESPACE`: a non-empty, profile-specific namespace.

Startup fails closed when an enabled lane has a blank namespace, a blank checkpoint path, or a checkpoint path that resolves to the legacy task database. The committed example profiles use distinct values:

| Profile | Legacy task database | LangGraph checkpoint database | Namespace |
| --- | --- | --- | --- |
| Local | `/data/director.sqlite3` | `/data/langgraph-shadow.sqlite3` | `director-local-shadow-v1` |
| Project Uppercut | `/data/project-uppercut.sqlite3` | `/data/project-uppercut-langgraph-shadow.sqlite3` | `project-uppercut-shadow-v1` |
| Edge Oracle | `/data/edge-oracle.sqlite3` | `/data/edge-oracle-langgraph-shadow.sqlite3` | `edge-oracle-shadow-v1` |

Do not reuse a checkpoint path or namespace across deployment profiles. Checkpoints must not contain secrets, raw provider credentials, or unrestricted transcripts.

## Health interpretation

`GET /health` exposes only booleans under `langgraph`:

- configured feature flags;
- whether the checkpoint path is present and isolated;
- whether the namespace is present;
- whether the enabled configuration is valid;
- `runtime_active=false`;
- `legacy_sole_writer=true`.

An enabled/configured flag is not evidence that LangGraph owns execution. In this phase the API validates configuration but never imports or invokes the graph.

## Safe proof procedure

1. Keep paid execution, Slack intake, and external writes closed for the proof.
2. Copy the appropriate example profile to its ignored local environment file.
3. Confirm the profile's LangGraph checkpoint path and namespace are unique.
4. Enable only the proof lane named by the accepted work order.
5. Restart the isolated API and confirm the `langgraph` health booleans while `runtime_active` remains false and `legacy_sole_writer` remains true.
6. Run offline fixtures or the separately approved proof tooling. Do not route live intake or provider callbacks into the graph.
7. Preserve comparison and recovery evidence outside canonical task state.

## Rollback

1. Set both LangGraph feature flags to `false`.
2. Restart the isolated Director profile.
3. Confirm `/health` reports both flags false, `runtime_active=false`, and `legacy_sole_writer=true`.
4. Retain the graph database as evidence, or remove only that isolated graph database after its exact path has been verified. Never delete or replace `SQLITE_PATH`.

Disabling the flags requires no task-state conversion because there is no migration from the legacy store. Removing the isolated graph database cannot roll back an external effect and must never be represented as doing so.

## Explicitly deferred

- importing or invoking a graph from `main.py`;
- provider dispatch or callback ownership;
- GitHub, Slack, merge, deployment, or other side effects;
- migration of existing task records or graph checkpoints;
- provider-specific subgraphs;
- any dual-writer or writer-cutover phase.

Each deferred capability requires separate acceptance evidence and authority.

**[[Codex | GPT-5.6 | 2026-08-22 16:05 UTC | END]]**
