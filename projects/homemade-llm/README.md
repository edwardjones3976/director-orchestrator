<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:34 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:27 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | BEGIN]] -->
# Homemade LLM — project subsection

**Project ID:** `homemade-llm` · **Owner:** Ed · **Project Director:** Claude (Claude Fable 5.1), appointed by the owner on 2026-09-24 for this project only · **State:** building (M0 gate report filed 2026-09-25)

This folder is the project-local record for building two purpose-trained local
models on the owner's PC "blackbox" (RTX 5070 12 GB): **DIRECTOR**, an
orchestration and brief-writing model on an instruct base, and **COMPANION**
(placeholder name), a persona model built on a pretrained-only base. Training is
QLoRA with Unsloth in Docker; serving is LM Studio; the chat surface is Open
WebUI with SearXNG. The full plan is [`PLAN.md`](PLAN.md).

The project is *hosted in* director-orchestrator under the repository's
[modular project bootstrap protocol](../../docs/project-bootstrap-protocol.md).
It is not a provider lane of the runtime, it changes nothing under `src/`,
`tests/`, `docs/`, or the live stack, and it inherits no authority from any
other project. All model artifacts, datasets, and secrets live on the host under
`C:\llm\` and are never committed.

## Chain of command for this project

`docs/project-leadership.md` and `CLAUDE.md` state that the ChatGPT/Codex lane
is the Director of director-orchestrator and that the Claude lane has no
standing director role. Both remain true for the repository. The bootstrap
protocol's "Project profile" section gives each *project* its own
`team.director` binding, and this project's
[`project-profile.yaml`](project-profile.yaml) records the owner's appointment:

> "You will lead this specific project; that doesn't mean you'll lead any other
> project outside it. As the acting Director of this project, you will direct
> other commercial AI platforms to help build the following project."
> — owner, 2026-09-24, in the Claude Code session (relayed by Claude)

So for `homemade-llm`: **Ed → Claude (project Director) → providers by
capability.** The deviation from the template default is recorded in
[`provider-roles-contract.yaml`](provider-roles-contract.yaml) under
`project_local_bindings.deviations`. Every repository rule still binds: one
author (at most one sole author, at least 52 % of the PR; see the
[sole author rule](../../docs/contribution-attribution-policy.md#sole-author-rule)) and one non-author reviewer per PR, no contributor (including maintenance-only contributors) merges;
the non-contributing reviewer that has thoroughly reviewed the work performs the
merge (owner merge law, 2026-09-26), contribution tags on every submission, and
`OWNER_DECISION_REQUIRED` for anything in AGENTS.md rule 10.

## How to run the project from here

1. **Work in a fresh worktree from `github/main`.** The main checkout is on an
   old branch with untracked work; never switch it.

   ```powershell
   cd C:\Users\edwar\director-orchestrator
   git fetch github main
   git worktree add .worktrees\homemade-llm-<slug> -b <provider>/homemade-llm-<slug>-YYYYMMDD github/main
   git -C .worktrees\homemade-llm-<slug> rev-parse --show-toplevel   # must print the worktree path
   ```

2. **Change only `projects/homemade-llm/`** in this project's PRs until the M3
   owner decision opens a runtime lane. Never add a subdirectory named `data`
   (globally gitignored) and never commit anything from `C:\llm\`, the old
   `xo-fleet` Open WebUI data directory, or `Life_Coach.Modelfile` (personal data).

3. **Tag everything** per
   [`docs/contribution-attribution-policy.md`](../../docs/contribution-attribution-policy.md):
   a fresh outer pair in every changed commentable file, a bold pair as the
   first and last visible lines of the PR body, and the pair as the HEAD commit
   message envelope. Time comes from `date -u` when the text is written.
   Validate before pushing:

   ```powershell
   Push-Location .worktrees\homemade-llm-<slug>   # the interpreter path below is relative to the worktree
   $env:PYTHONPATH = 'src'
   ..\..\.venv-win\Scripts\python.exe scripts\check_contribution_tags.py --base <main-sha> --head HEAD
   ```

4. **Dispatch providers** only through the owner's provider-dispatch skill
   (outside the repository): `python ~/.claude/skills/provider-dispatch/dispatch.py status`
   then `grok | codex | agy | cursor | copilot | perplexity | firecrawl` with a
   work packet shaped by
   [`docs/schemas/model-work-packet-v1.schema.json`](../../docs/schemas/model-work-packet-v1.schema.json).
   Perplexity receives public research questions only. Antigravity receives no
   model-authored prose and never touches Slack. OpenAI, ChatGPT, and Codex are
   one identity; the xAI API, Grok CLI, and Grok Bot are one family.

5. **Review and merge.** The Director assigns a non-author reviewer of record
   (Grok CLI first, Codex fallback). Only the sole author and its provider family
   are excluded from reviewing on authorship grounds; an assisting contributor
   may review under that policy, subject to the
   [runtime limits](../../docs/contribution-attribution-policy.md#sole-author-rule).
   For any review, acceptance or merge passing through the material-author
   gates listed there, contributor review eligibility is prospective until a
   separately reviewed runtime change aligns them, including Director-routed
   and manual reviews whose verdicts pass through those gates. The reviewer's exact-SHA
   `ACCEPT` is relayed to the PR. The accepting reviewer performs the merge,
   except that any reviewer with counted lines, whether an assisting contributor
   or the exactly-48 % co-author, hands the merge to a non-contributing reviewer
   that has thoroughly reviewed the work.
   Any contributor (including maintenance-only contributors) may never
   merge that PR, and this bar never lifts. Every candidate change invalidates
   prior review evidence. This is the owner merge law as policy.
   Until the separately authorized framework alignment lands, protected-branch merges (including `main`) remain tier 0 under `docs/human-out-of-the-loop-operation.md`, so an accepting reviewer records the `ACCEPT` and the framework blocker instead of merging; non-protected merges still require a live grant.

6. **Hard stops.** The Director halts and reports at every milestone gate,
   before any provider call that would exceed the milestone's USD 25 envelope,
   and before any fan-out of more than three concurrent agents or dispatches.
   Downloads, configuration changes, deletions, and messages to other Directors
   wait for the owner's explicit "Go".
   Maintenance (review-finding fixes, record and document inconsistencies, CI
   fixes, provider plumbing) goes to Codex under the charter's
   [Director maintenance routing](../../docs/team-operating-charter.md#director-maintenance-routing);
   `dispatch.py codex` is the usual route from this project.
   Codex's maintenance commits are assistance and do not count toward authorship shares (owner ruling 2026-09-27); the sole author is computed from authored commits in `merge_base_sha..head` (the sole-author rule, PR #284).

7. **Evidence.** Numbers come from artifacts this project produced and re-read.
   Claims carry a grade: `[verified]` (read or measured), `[relayed]` (from a
   document or a person), `[stale]` (true at a stated time). Small evidence
   files go under `evidence/`; session handoffs under `handoffs/`.

## Files

| File | Role |
|---|---|
| [`PLAN.md`](PLAN.md) | Plan v1.3: launch definition (L1/L2 checklists, calendar), research findings, and the v1.2 corrections to the owner's v1.1.0 |
| [`project-profile.yaml`](project-profile.yaml) | Living project profile (`director-project-profile/v1`) |
| [`provider-roles-contract.yaml`](provider-roles-contract.yaml) | Project-local provider boundaries (`director-provider-roles/v1`), with the Director deviation recorded |
| [`rollout-packet.md`](rollout-packet.md) | Mandatory rollout packet; analysis-only, all Slack identities pending |
| [`workstream-plan.md`](workstream-plan.md) | Current phase (M0) assignments, providers, acceptance, stops |
| [`decision-log.md`](decision-log.md) | One line per owner or design decision |
| [`project-state-report.md`](project-state-report.md) | Evidence-first milestone report (latest: M0 gate, 2026-09-25) |

## Milestones

| # | Milestone | Exit gate | Stop |
|---|---|---|---|
| PR-0 | This subsection merged | `main` contains `projects/homemade-llm/` | exact-SHA ACCEPT and green checks; merge by a non-contributing reviewer that has thoroughly reviewed the work |
| M0 | Foundation: `C:\llm` layout, Unsloth smoke fine-tune on the GPU, LM Studio serving a merged GGUF, Open WebUI reaching it, Ollama retired, mirror job | state report with every number verified | gate; report filed 2026-09-25 ([`project-state-report.md`](project-state-report.md)) |
| M1 | Base-model shootout | winner per family with license check; owner sign-off | gate |
| M2 | DIRECTOR data v1 + adapter v0.1 → v0.3 | > 60 % field-level acceptance on both held-out sets; no > 3-point general regression; week-4 early warning at < 45 % | gate |
| M3 | DIRECTOR v1.0 as a local provider lane (owner decision required; after PRs #263/#264) | **L1 DIRECTOR launch checklist** (`PLAN.md` §2.1), target 2026-11-12 | gate |
| M4 | COMPANION Persona Bible + Stage A library | Bible and source list approved; Stage A perplexity gate | gate |
| M5a/b | COMPANION Stage B / Stage C | **L2 COMPANION launch checklist** (`PLAN.md` §2.2), target 2026-12-17 | gate |
| M6 | Hardening, runbooks, restore drill | fresh-machine restore < 1 day | gate |
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:27 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:34 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:29 UTC | END]] -->
