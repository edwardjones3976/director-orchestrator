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
   `GitExecutable`, `NativeExecutable`, `Model`, and `Deployment`. Cursor also
   requires the pinned `NativeEntrypoint` (its index.js); NativeExecutable is its
   node.exe. Claude's native executable is claude.exe. No environment secret input
   or password is accepted by this script. It copies accepted code and an
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
   login fails; the worker never runs an interactive login flow. Verify how the
   installed Cursor version resolves authentication with `CURSOR_CONFIG_DIR`
   before enrollment; keep permission configuration administrator-owned, and
   keep native saved authentication exclusively under the worker identity.
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
