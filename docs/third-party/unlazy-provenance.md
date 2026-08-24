<!-- [[Codex | GPT-5.6 | 2026-08-24 | 05:14 UTC | BEGIN]] -->
# Unlazy provenance for Director Completion Gates

## Reviewed upstream

- Repository: <https://github.com/Leonxlnx/unlazy>
- Exact reviewed commit:
  <https://github.com/Leonxlnx/unlazy/commit/754d9a68109e39b836cc72a39fb9a823f9d6b613>
- Commit: `754d9a68109e39b836cc72a39fb9a823f9d6b613`
- Commit message: `fix: keep hook lock waits within host timeout`
- Commit timestamp: `2026-08-23T04:51:19Z`
- Package metadata at the pinned commit targets `2.1.0`; it is not a tagged
  release.
- Exact README:
  <https://github.com/Leonxlnx/unlazy/blob/754d9a68109e39b836cc72a39fb9a823f9d6b613/README.md>
- Exact package metadata:
  <https://github.com/Leonxlnx/unlazy/blob/754d9a68109e39b836cc72a39fb9a823f9d6b613/package.json>
- Exact changelog:
  <https://github.com/Leonxlnx/unlazy/blob/754d9a68109e39b836cc72a39fb9a823f9d6b613/CHANGELOG.md>
- License: MIT, Copyright (c) 2026 Leonxlnx
- Exact license:
  <https://github.com/Leonxlnx/unlazy/blob/754d9a68109e39b836cc72a39fb9a823f9d6b613/LICENSE>
- Exact security model:
  <https://github.com/Leonxlnx/unlazy/blob/754d9a68109e39b836cc72a39fb9a823f9d6b613/SECURITY.md>

## Relationship

Director Completion Gates are independently designed and implemented. They are
inspired by Unlazy's concepts of recording acceptance expectations before work,
evidence-backed completion checks, independent re-verification, layered
integration checks, and remeasurement instead of trusting stale claims.

No upstream source code, template, hook, command syntax, or `Depth Tree`
implementation is included in this increment. Because this is concept-only
inspiration, the repository does not vendor an upstream license file. If later
work copies or adapts upstream code, templates, or substantial prose, that change
must retain the complete applicable MIT copyright and permission notice and
receive a separate provenance and security review.

## Deliberate security differences

The reviewed upstream security document says its checks execute shell commands
with the launching user's ambient filesystem, environment, credentials, network,
and tool access. Approval is consent, not sandboxing or semantic proof; referenced
scripts are not snapshotted; coordination scopes and leases do not create OS
isolation; and evidence can expose sensitive output.

Director Completion Gates therefore:

- define no raw shell-command contract;
- globally install no upstream package, skill, hook, or automatic update;
- enable no Claude Stop hook;
- bind runnable gates to a registered runner definition and structured arguments;
- require `shell=False`, sanitized environments, bounded resources, exact
  repository/SHA checks, and output redaction in a later runner increment;
- bind repository-owned scripts to exact Git blob SHAs;
- separate manual and external-CI evidence from executable runners;
- keep evidence outside canonical task and LangGraph checkpoint storage;
- never treat gate evidence as review, merge, deployment, or external-write
  authority.

## Update policy

The reviewed commit is the sole provenance reference for this design. Floating
`main`, untagged version text, automatic updates, and production installation
are prohibited. Any future upstream comparison must pin a new exact commit,
review its license and security model again, identify copied or adapted material,
and pass the normal exact-SHA independent review and security gates.
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 05:14 UTC | END]] -->
