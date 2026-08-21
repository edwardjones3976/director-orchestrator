# Project state report

Use for a new bootstrap, milestone, reshaped plan, pause, or completion. Delete
sections that do not carry decision-relevant information. Do not place secrets,
credentials, tokens, or raw private source in this report. Workflow skills are
procedure overlays, not permission grants.

Event markers follow
[`project-bootstrap-protocol.md`](../../project-bootstrap-protocol.md).

## <BOOTSTRAP | PLAN | EVIDENCE | RESHAPED | PAUSED | COMPLETE | OWNER_DECISION_REQUIRED>

**<Project name>** is now <current state>. Canonical record:
[<artifact, profile, pull request, or decision record>](<URL>).

### Current shape

- **Purpose/outcome:** <current proposition or delivered result>
- **Selected modules:** <active modules>
- **Team routing:** <providers, roles, and discovered workflow skills>
- **Coordination:** <slack channel ID or configured placeholder and thread>
- **Execution surface:** <verified repository/ref/worktree or analysis-only>
- **Stopping point:** <current stopping condition>

### What changed

- <new profile, module, implementation, evidence, decision, or handoff>
- <what was reshaped, removed, or deliberately kept provisional>

### Boundaries

- <what was not inherited from the source project>
- <data, repository, budget, credential, and external-effect limits>
- <owner, review, security, CI, merge, or deployment gates>

### Evidence

- **Validated:** <checks actually run and exact results>
- **Provider evidence:** <authenticated run IDs or clearly labelled provider report>
- **Independent review:** <provider, exact SHA/digest, verdict, or pending>
- **Known gaps:** <unavailable providers, skipped checks, uncertainty, or none>

### Current handoff

- **State:** <review ready / owner decision required / accepted / merged / deployed / paused / complete>
- **Exact candidate:** <SHA/digest or not applicable>
- **Next responsible party:** <provider, Director, deterministic gate, or owner>
- **Next action:** <one concrete action>

Nothing outside the stated project scope was <written, pushed, merged, deployed,
published, enabled, or otherwise changed>.
