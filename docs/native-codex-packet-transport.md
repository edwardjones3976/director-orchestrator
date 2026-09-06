<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | BEGIN]] -->
# Native Codex packet transport

The default remains `LANGGRAPH_PRIMARY_OPENAI_TRANSPORT=responses-api`, using
existing paid-run and budget gates. Set `native-codex-host` only when an approved
Windows host bridge is staged. This selects native Codex exclusively: missing
bridge evidence, missing account auth, and ambiguous execution do not cause an
API fallback. Switching back to Responses is an explicit operator decision.

On the host, add `"openai":["C:\\absolute\\path\\codex.exe","gpt-5.6"]` to
`DIRECTOR_HOST_PROVIDER_COMMANDS_JSON`. The entry is exactly a native executable
and an explicit model; shell wrappers and caller-supplied flags are rejected.
The existing bridge endpoint, TLS, distinct HMAC secret, Postgres writer fence,
and durable claim store remain required. Linux does not execute the Windows
binary. The signed host challenge includes OpenAI only after the native command's
read-only `login status` confirms ChatGPT authentication. Configuration is not a
live provider canary or a quota entitlement proof.

The host performs a single `codex exec --json` run in a fresh temporary directory.
It passes only the validated packet, uses read-only sandboxing, ignores user
configuration, disables shell, web, apps, plugins, hooks, image and agent tools,
and forces ChatGPT authentication with the OpenAI provider. Native child
environment contains operating-system essentials only; API and Director secrets
are excluded. The account's existing protected native auth store is consumed by
Codex; this transport never reads or copies its credentials.

This is **packet-only analysis**, equivalent in scope to the previous tool-free
Responses transport. It does not inspect or modify the packet's repository.
Acceptance checks default to NOT_RUN; repository or execution evidence requires
an appropriate independently authorized lane. The parser requires a native
thread ID and a completed turn, rejects tool/error/incomplete events, binds
results back to the original packet, and reports actual token counts when given.
The requested model is recorded as a request in the limitations; actual model
and dollar cost remain null because the JSONL stream does not attest them.
ChatGPT login does not prove unlimited usage, available quota, or zero cost.

Input and output are capped at 1 MiB; runtime is capped at the packet deadline,
host deadline, and 900 seconds. A timeout, nonzero exit, oversized output, or
invalid terminal stream is ambiguous and never automatically redispatched.
The existing host durable claim blocks replay even after a host restart.
Writer authority is checked immediately before dispatch. As with the current
bridge contract, there is no asynchronous cancellation callback during a running
provider request; the bounded deadline remains the in-flight stop mechanism.

The JSONL fixture follows the official [non-interactive mode documentation](https://developers.openai.com/codex/noninteractive).
CLI options and feature names were also verified through installed `codex exec
--help`, `codex features list`, and `codex login status`. Tests exercise fixture
normalization, native auth selection, credential filtering, fail-closed routing,
host configuration, and real local child timeout/output bounds without sending
a provider request. A separately authorized live canary remains required.
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 02:20 UTC | END]] -->
