<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:36 UTC | BEGIN]] -->
# VS Code contact methods and Director integration

VS Code is an editor and agent host, not a provider with one universal API key.
Register the actual provider, product, authentication mode, and execution route
separately. This inventory documents supported connection classes; it does not
enroll a route, establish remaining capacity, or grant production authority.

| Connection | Authentication and purpose | Director process |
| --- | --- | --- |
| Built-in Copilot chat / Copilot extension | GitHub sign-in and eligible Copilot entitlement | Interactive route until a supported adapter proves task submission, completion, and cancellation. Opening chat is not dispatch evidence. |
| GitHub Copilot CLI | GitHub OAuth; supported token in `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`; stored credentials or GitHub CLI fallback | Existing `github-copilot` command/packet route. Resolve the native executable, verify entitlement and capacity, then apply packet and resource controls. |
| VS Code Language Model extension API | Extension requests user consent and an available model | Runs inside an extension host. It is not a public HTTP endpoint or a portable Copilot API key. Requires a separately reviewed extension adapter. |
| Custom model / BYOK integration | Provider-specific credentials or a local model endpoint | Preserve actual provider identity and billing product. API access cannot silently substitute for a subscription coding route. |
| Agent extensions / agent harnesses | Product-specific sign-in, subscription, or API credential | Codex, Claude, and other agents keep their actual provider identity even when launched from VS Code. Confirm each installed adapter rather than inferring support from an extension name. |
| MCP servers | Local stdio or remote HTTP; server-specific OAuth or protected credential inputs | Tool/data connection, not proof of an autonomous provider. Validate server trust, allowed tools, authorization, timeout, and results. |
| VS Code CLI / URI handlers | Local user session and installed editor | Workspace-opening and UI routing only. A successful launch does not establish task acceptance or output validity. |
| Remote SSH / tunnels / containers | SSH credentials, tunnel sign-in, or container access | Execution-environment transport. Enroll the remote worker separately and retain repository, capability, and credential boundaries. |
| GitHub cloud coding agent | GitHub identity, entitlement, repository access, and policy | Remote job route with job/PR identifiers and asynchronous completion evidence. Distinct from a local editor session. |

## Authentication controls

Copilot CLI supports OAuth tokens, fine-grained personal access tokens with
Copilot Requests permission, and GitHub App user-to-server tokens. Classic PATs
are not supported. A token's presence is not authentication, entitlement, or
remaining-capacity evidence. Preserve existing secret-store bindings; do not
copy values into packets, repository documentation, logs, or editor settings.

Check BYOK environment overrides before dispatch: they may change the selected
model and billing route. Never fall back to separately billed access implicitly.
Microsoft 365 Copilot remains retired from this project; VS Code and GitHub
Copilot setup does not reintroduce it. Shared Microsoft/GitHub provenance must
remain visible when determining reviewer independence.

## Common LangGraph / n8n / Director contract

1. Record the assignment ID, actual provider/product, exact starting revision,
   allowed files/tools, objective, acceptance checks, and resource limits.
   Reconcile the real rolling assignment history before admitting production
   work; this inventory earns no production allocation credit.
2. Resolve a supported executable, extension adapter, or remote job transport.
   Verify authentication, entitlement, capability, and capacity independently.
   Route unavailable or unknown evidence through the existing fail-closed gates.
3. Submit through the existing Director packet boundary. Preserve LangGraph's
   sole-writer ownership and n8n intake behavior. Record execution ID, deadline,
   cancellation handling, and any ambiguous effects before considering recovery.
4. Validate the result and attributable artifacts. Preserve failed and malformed
   responses. Work acceptance, Slack posting confirmation, and asynchronous
   readback are separate outcomes; notification recovery must not duplicate work.
5. Record tests, elapsed time, available usage evidence, and independent review.
   Neither editor access nor a provider reply authorizes merge or activation.

## Host observations and bounded follow-up

Observed on 2026-09-13 UTC: VS Code 1.135.0 includes built-in `copilot` and
`github-authentication` components. Copilot CLI 1.0.83 is installed via npm.
Its PATH entry is a PowerShell launcher; its native executable is nested under
the package's platform dependency. The current Director discovery requires a
native executable and can therefore miss this installation. An explicit native
executable binding needs a bounded transport-author fix and independent review;
this document does not change discovery or claim the route ready.

The project has a Copilot credential binding. A separate live attempt reached
the spending limit, so this inventory does not repeat a paid call. Local tests
must be reported separately from authenticated live execution. Do not infer
Grok Bot's capacity from Copilot or Cursor model limits.

## Official references

- [Copilot CLI authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
- [VS Code Copilot setup](https://code.visualstudio.com/docs/setup/copilot)
- [VS Code language models](https://code.visualstudio.com/docs/agent-customization/language-models)
- [Language Model extension API](https://code.visualstudio.com/api/extension-guides/ai/language-model)
- [MCP servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [VS Code command line](https://code.visualstudio.com/docs/configure/command-line)
- [GitHub coding agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)

<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:36 UTC | END]] -->
