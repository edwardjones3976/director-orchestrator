<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 23:35 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:31 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]] -->
# Native acceptance issuers

Status: dormant implementation requiring independent adoption and owner installation.
It does not activate LangGraph or approve its own release. Existing v1 remains
unchanged. CI issuance, registry enrollment and atomic activation challenge
consumption are separate interfaces owned by the v2 verifier/runtime.

## Four identities, two native logins

Use a dedicated **broker SID and native worker SID per role**, four non-admin
Windows accounts total. Claude owns the security role; Cursor owns independent
review. Only the two workers need native provider logins. A model subprocess under
a signer's SID could read that SID's key even if its environment omitted the key;
therefore the workers never load signing material. The coordinator must not share
any of these SIDs. Malicious host administrators are outside this boundary.

The administrator-owned installation contains these protected directories:

| Directory | Allowed non-administrator access |
|---|---|
| code, issuer.json, launcher | Broker/worker read and execute |
| authorizations | Broker read; owner administrator writes |
| broker | Broker modify: key and durable claims |
| requests | Broker modify; worker read |
| worker | Worker modify: native temporary workspace and claims |
| responses | Worker modify; broker read |
| public | Broker modify; coordinator read |
| cursor-config | Broker/worker read; owner administrator writes |
| mirror | Broker/worker read; owner administrator updates |

The request and response files are an ACL-protected local handoff, not an HTTP
endpoint or an imported-session API. The coordinator cannot author a trusted
response. There is no `sign JSON` operation. No always-on privileged service is
installed: the initial supported mode is an owner-started fixed broker/worker
invocation in each dedicated login session. A scheduled service wrapper needs a
separate protected task/service ACL and profile-loading acceptance check.

## Owner installation

1. Independently review/security-check/test and accept the exact issuer release.
   Do not use these issuers to approve their own initial adoption.
2. Create four new non-admin local accounts in an owner-controlled elevated
   session. Do not reuse the coordinator or existing agent sandbox accounts.
3. Provision administrator-owned Python with the accepted project's dependencies,
   Git, Claude and Cursor runtimes at machine-accessible read/execute locations.
   Do not copy saved provider credentials. Pin the complete native distribution,
   not only its launcher; all imported code and DLL locations must be protected.
   Owner-profile Python/CLI installations usually fail this requirement. The
   installer deliberately rejects unprotected executable/entrypoint ancestors.
4. Run `scripts/windows-install-native-issuer.ps1` once for each role, specifying
   `Role`, `BrokerAccount`, `WorkerAccount`, `CoordinatorSid`, `AcceptedSource`,
   `AcceptedCommit`, `RepositoryMirror`, `Repository`, `PythonExecutable`,
   `GitExecutable`, `NativeExecutable`, `ProtectedRuntimeRoots`, `Model`, and `Deployment`. Cursor also
   requires the pinned `NativeEntrypoint` (its index.js); NativeExecutable is its
   node.exe. Claude's native executable is claude.exe. No environment secret input
   or password is accepted by this script. It extracts only regular tracked package blobs from the exact accepted commit
   (ignored bytecode and dirty working files are excluded) and creates an
   independent local bare mirror; it does not fetch remote data, create accounts,
   generate a private key, sign evidence, log in, or dispatch a model.
5. The installer prints a configuration digest and writes an immutable launcher
   beneath `%ProgramData%\DirectorAcceptance\<role>`. Matching re-installation
   checks installed digests and returns without changing state. Partial or
   different installations require owner review; it never deletes keys or retries
   a partial installation automatically.
6. Under each worker identity, complete its native interactive login. This can
   happen asynchronously while verifier work continues. Claude must report native
   `claude.ai` authentication, not an API key. Cursor uses the native
   `cursor_login` ACP method, which consumes existing login credentials. A missing
   login fails; the worker never runs an interactive login flow. On the pinned
   Windows build, login uses the worker APPDATA/Cursor/auth.json independently
   of CURSOR_CONFIG_DIR (see the verified storage detail below). Keep permission
   configuration administrator-owned and saved authentication worker-only.
7. Under each broker identity, run the installed `invoke.ps1 -Action initialize`.
   It verifies the actual Windows token SID and internally generates a private
   Ed25519 key if absent. Only the public IssuerAuthority document is printed.
   The owner enrolls these public keys into the separately protected v2 registry.

Before enrollment, owner QA must test access with the **coordinator's actual
non-elevated token**: private key/state reads, configuration/program writes,
parent-directory replacement, owner/WRITE_DAC, task/service reconfiguration and
cross-role access must fail. Reject reparse points throughout installed code,
runtime and state ancestry. Test worker reads of the broker key fail. Being a
different process or removing an Allow ACE while retaining coordinator ownership
is insufficient. Validate the interpreter's complete `sys.path`, native runtime
dependencies and effective token/group memberships; path pinning alone is not
whole-runtime isolation. No such installation or access test has run during code
development.

## Packet and challenge preparation

`prepare` reads immutable Git objects from the configured local mirror, never
checks out or executes candidate code. Its shared packet is canonical JSON using
`provider_acceptance_v2.canonical`:

```
{version: "director-native-review-packet/v1", repository, base, head, tree,
 diff, files: [{path, base_blob, head_blob, base_text, head_text}, ...]}
```

Files are sorted by UTF-8 repository path. The diff uses `--binary --full-index
--no-renames --no-ext-diff --no-textconv`. Both full base/head file contents are
included; absent files have null blob/text values. Nonregular Git modes, gitlinks,
binary/non-UTF8 files, empty deltas and packets over 2 MiB fail closed. Nothing is
truncated. ReviewContext.packet_digest is SHA-256 of the complete canonical bytes.

The installed launcher accepts `-Action prepare -Base <SHA> -Head <SHA>` and prints
only packet digest, byte count and tree. The owner uses that digest in the v2
registry; the v2 runtime creates the common ReviewContext challenge. For this
initial owner-started flow, the owner writes the following protected authorization
to `authorizations/<challenge>.json` in **both** installations:

```
{"base":"<exact baseline SHA>","context":<ReviewContext.document()>}
```

The context is closed and includes full ActivationIdentity, repository, PR,
packet digest, challenge, lifetime and material authorship. Neither coordinator
request parameters nor provider prose supply trusted authorship or signer keys.
Changing the base changes the packet digest. Source updates in the protected
mirror require separate owner action; no automatic fetch occurs.

## One bounded review and collection

With separately authorized provider use, invoke these steps under the named SIDs:

1. Broker: `invoke.ps1 -Action issue -Job <challenge>` reconstructs and verifies the
   packet, records a SQLite FULL-synchronous unique claim, then creates one fixed
   worker request with a fresh run nonce.
2. Worker: `invoke.ps1 -Action work -Job <challenge>` verifies its actual SID,
   request binding, expiry and pinned CLI; records its own claim before native
   authentication or provider I/O; captures the native session directly.
3. Broker: `invoke.ps1 -Action collect -Job <challenge>` validates the protected
   worker capture and exact ACCEPT/challenge/packet/run-nonce result, then signs
   one fixed-role v2 receipt. The public file is create-once. Re-read that existing
   public file for delivery retries; do not rerun a paid review.

Any timeout, failed authentication, partial stream, invalid output, exception or
missing response leaves the claim in place. There is no automatic second attempt
and no claim reset operation. A new administrative challenge must not be used to
repeat ambiguous paid work without independent reconciliation.

Claude uses print/stream JSON, native account authentication, safe and restricted
modes, no built-in tools, strict empty MCP configuration and no permission prompts.
Cursor uses ACP NDJSON stdin: initialize, existing native authenticate, session/new,
explicit session/set_mode to ask, explicit configured session/set_model, then the
full text packet in session/prompt. ACP's initial mode was observed as agent even
when a launch flag requested ask; the explicit mode acknowledgment is mandatory.
The protected Cursor config denies shell/read/write/web/MCP tools; the ACP client
advertises no filesystem or terminal capability, rejects all permission requests,
and rejects tool notifications. Tool-event rejection is detection, not rollback;
preventive native permission enforcement must be validated for the pinned release.

Installed Cursor initialize/authenticate/new/set_mode/set_model were checked with
no session/prompt and no paid review. Actual isolated worker login, preventive
permission enforcement and complete native terminal captures remain deployment
acceptance checks. Claude's supported local help was inspected, not a paid run.

Each attempt has at most 600 seconds and 1 MiB captured evidence. Request delivery
is also bounded; stderr is discarded. Native children receive only the worker's
OS/profile environment, not API, coordinator or signing credentials. Native model
text cannot assert identity: the fixed worker captures protocol session metadata
and the broker binds the raw transport digest. The resulting artifact contains
`version`, `context`, and one `receipt`, to be assembled with the other native role
and independent CI receipt by the existing v2 verifier/runtime.

Reference behavior: [Cursor ACP](https://cursor.com/docs/cli/acp),
[Cursor permissions](https://prod.cursor.com/docs/cli/reference/permissions), and
[Cursor configuration](https://prod.cursor.com/docs/cli/reference/configuration).
<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]] -->

The installer now requires `-ProtectedRuntimeRoots` covering the complete Python
base installation (including a virtual environment's separate base), Git runtime,
and native CLI distribution. It recursively verifies all files and directories,
then checks the isolated Python interpreter's base, prefix and import paths fall
inside those roots. A per-action runtime audit checks the installed code and
runtime trees once; consumed authorization/request/response files receive separate
owner, ACL and reparse checks. Ancestor DELETE permission is also rejected.

The generated launcher clears all inherited `PYTHON*` settings and uses `-P -s`
with only its fixed installed code directory as `PYTHONPATH`. A real subprocess
regression places a hostile same-named package in the caller's folder and poisons
PYTHONHOME/PYTHONPATH; the installed template loads only the intended package.

For Cursor 2026.09.02-c22c1a3 on Windows, installed code computes credentials from
APPDATA, the title-cased credential domain, and `auth.json`; the CLI supplies domain
`cursor`. Thus its native login resides at `%APPDATA%/Cursor/auth.json` under the
worker identity. `CURSOR_CONFIG_DIR` instead controls `cli-config.json` independently.
Enrollment uses the pinned CLI's `login` command under the worker identity with
its normal APPDATA. No credential is moved or read by the broker. At review time
HOME and USERPROFILE point to the empty administrator-owned cursor-config directory,
while APPDATA remains the worker's actual profile location. Cursor's home-based
rules/skills loaders therefore see the protected empty home; the workspace is a
fresh empty temporary directory. Claude uses its documented `--safe-mode` (disables
CLAUDE.md, skills, plugins, hooks, MCP and custom instructions) together with
`--restricted`, empty tools and strict empty MCP configuration; `--bare` is avoided
because it disables the native OAuth credential mechanism.

Validation here exercises the actual generated launcher and rejects a real
current-user-owned temporary installation through Windows Get-Acl. It does not
claim an elevated installation or successful four-SID effective-access test:
those and the two native logins remain owner provisioning gates.

<!-- [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:31 UTC | END]] -->

<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | BEGIN]] -->
## Grok security lane (implemented, fail-closed)

`-Provider grok` is eligible for the **security** role only, installs into its own
`DirectorAcceptance\security-grok` directory with its own broker/worker SIDs, key
and claims, and changes nothing about the Claude, Cursor, Codex or AGY lanes. The
transport is the locally authenticated **Grok Build CLI**, matching the exact-SHA
review responsibility already assigned in `docs/provider-setup.md`. The
analysis-only xAI Responses API is never a review transport here: the worker
builds a Build CLI argv, and no API key, base URL or coordinator credential is
placed in the child environment. Because the CLI is a single pinned executable,
the installer and `Config.load` both reject a separate `NativeEntrypoint`.

The lane **cannot issue a receipt today and fails closed before any paid
attempt** because protected native-account readback and effective tool/effect
containment have not been implemented and qualified. The worker refuses before
its create-once claim. No ambiguous paid work or challenge consumption occurs.

The structural parser now follows the installed xAI manual
`user-guide/14-headless-mode.md`, **streaming-json** section: `text.data` chunks,
optional thought/plan/usage events, and a final `end` carrying `stopReason`,
`sessionId`, `requestId`, `num_turns`, and `modelUsage`. Only a successful
`end_turn`, exact nonce-derived session, one reported turn/model call and exact
ACCEPT/challenge/packet/nonce binding pass structural validation. Unknown,
error, tool, incomplete, foreign, duplicate-terminal and trailing events fail
closed. CLI-reported model usage is metadata, not a signed backend identity or
account observation. Missing metadata cannot be filled from requested labels.

`--model` is documented and included as a request, not treated as proof of the
served model. The invocation uses the ACP `streaming-json` format exclusively.
The documented Messages format (`result.subtype=success`, `is_error=false`)
is deliberately rejected on this route rather than mistaken for an ACP end;
its init model and authentication-source metadata also have fidelity caveats.

The argv requests one bounded turn, no subagents, no web search, and explicit
canonical deny tool IDs (`run_terminal_cmd`, `search_replace`, web tools, Agent)
plus Bash/Edit/Write/MCP permission denials. It does not assume an empty
`--tools` disables tools or that a wildcard tool ID matches every tool. The
vendor manual says MCP meta-tools remain available unless denied. Requested
permission restrictions and rejecting tool events are **not proof of effective
prevention**. The protected account and containment gate therefore remains
closed independently of successful structural parsing. No API fallback or
credential changes are introduced.

Remaining blockers before this lane can be enrolled: an actual successful native
Build CLI run producing a terminal/session/model observation; the pinned
executable and dependency hashes; distinct non-admin Grok broker/worker accounts
with an independently protected broker key; native login completed under the
worker identity without copying coordinator credentials; proof that the worker
and coordinator cannot read signing material or protected requests; and
independent nonauthor review, required CI and Codex Security of this source. No
native Grok run, host qualification, login, installation or enrollment was
performed for this implementation, and the fixtures in
`tests/test_native_acceptance_grok.py` are explicitly synthetic.
<!-- [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-07 | 23:35 UTC | END]] -->
