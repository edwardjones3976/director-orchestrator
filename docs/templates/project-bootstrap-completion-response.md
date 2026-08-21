# Project bootstrap completion-response template

Use this template after creating or materially reshaping a project profile,
protocol, or project-specific operating arrangement. Keep only sections that
carry useful information. Replace every angle-bracket placeholder; never leave
a placeholder that could be mistaken for verified evidence.

```markdown
Created the project-specific bootstrap for **<project name>** in
[<artifact, pull request, or decision record>](<canonical URL>).

It turns **<source thread or pattern>** into a project-local operating frame:

- **Purpose:** <what the project is trying to accomplish, learn, or decide>
- **Current state:** <exploring, framing, building, validating, operating,
  paused, or closed>
- **Selected modules:** <only the active bootstrap modules>
- **Team routing:** <provider-to-workstream mapping and selected workflow skills>
- **Coordination:** <project Slack channel and thread>
- **Execution:** <verified repository, worktree, branch/ref, or analysis-only>
- **Canonical evidence:** <GitHub PR/SHA, research brief, artifact digest, or
  other authoritative record>
- **Stopping point:** <the decision, evidence threshold, budget, deadline, or
  safety condition that ends this phase>

Important boundaries:

- <what was deliberately not inherited from the source project>
- <repository, credential, private-data, budget, or external-action limits>
- <independent-review, owner-decision, deployment, or merge gates>

Validation:

- <exact checks run and their results>
- <evidence that the intended files, artifacts, sources, or project surfaces
  were the only ones changed>
- <known limitations, uncertainty, skipped checks, or unavailable providers>

Current handoff:

- **State:** <draft, review ready, changes required, owner decision required,
  accepted, merged, deployed, paused, or complete>
- **Producer:** <provider and material authors>
- **Reviewer:** <eligible different provider, or not yet assigned>
- **Exact candidate:** <head SHA or artifact digest, when applicable>
- **Next action:** <one concrete responsible party and action>

Nothing was <pushed, merged, deployed, published, enabled, or otherwise changed>
outside the scope stated above.
```

## Short form

Use this when the project is simple and the longer format would add no value:

```markdown
Created **<project bootstrap or change>** in
[<canonical artifact>](<URL>).

- Shape: <purpose, selected modules, and stopping point>
- Routing: <providers and any shared workflow skills>
- Boundaries: <what remains unavailable or owner-gated>
- Validation: <exact evidence>
- State: <current state>; next is <responsible party and action>

No unrelated scope or external authority changed.
```

## Rules for using the template

- Lead with the result, not the work diary.
- Report only checks that actually ran; distinguish structural validation,
  provider-supplied claims, CI, and independent review.
- Use exact branch names, commit SHAs, artifact digests, task IDs, or run IDs
  when they are material.
- State skipped validation and unresolved uncertainty plainly.
- A selected skill describes a workflow; it does not prove that the workflow
  ran or grant its provider additional authority.
- For a reviewable artifact, include the different-provider `REVIEW_READY`
  handoff. A producer's full advisory vote never substitutes for that review.
- If three safe contact attempts leave a provider unavailable, state how the
  plan changed and what coverage was lost.
- End with the current gate and next responsible party. Do not imply that a
  draft was merged, that accepted work was deployed, or that an installed
  skill was discovered before the catalog was refreshed.
