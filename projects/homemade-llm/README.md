<!-- [[Cursor | Auto | 2026-09-29 | 05:32 UTC | BEGIN]] -->
<!-- [[Cursor | Auto | 2026-09-29 | 04:22 UTC | BEGIN]] -->
<!-- [[Cursor | Auto | 2026-09-29 | 03:30 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 07:45 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 06:20 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 00:15 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 03:10 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 13:29 UTC | BEGIN]] -->
<!-- MX-080 -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 04:14 UTC | BEGIN]] -->
<!-- MX-049 -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 02:13 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:35 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:25 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:44 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:35 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:29 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:19 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:34 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:27 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:47 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:56 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | BEGIN]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | BEGIN]] -->
# Homemade LLM — project subsection

**Project ID:** `homemade-llm` · **Owner:** Ed · **Project Director:** Claude (Claude Fable 5.1), appointed by the owner on 2026-09-24 for this project only · **State:** M0 closed under O-031; M1 closed under O-052 (winner Qwen3-14B); M2 past its 2026-10-29 target with no admitted training rows and no adapter; paused by the owner's stop and handed to Claude on 2026-10-31; direction returned to the Claude project Director (decision-log.html D-M2-65 to D-M2-76), see the [resumption record](director-resumption-20261031.html) and the [Director orders](director-orders-20261031.html)

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
   cd <repo-root>
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
   Review must accept the current head and base, and all required CI
   checks must pass before the merge. For PR #293, O-040 assigns Cursor its own
   thorough review and the guarded merge after all gates; Claude and Codex
   contributed to that PR and cannot review or merge it. Cursor is the single
   required reviewer for #293, subject to B2 and the PRESTAGE_REQUIRED steps
   in [`project-state-report.md`](project-state-report.md).

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
   Under the later owner ruling O-043, Codex's owner-directed maintenance on
   PR #293 is assistance and Claude is its sole author. O-041 remains the
   historical exception before that ruling. AGENTS.md at PR #293's base
   `4ece6d1` and in the current tree makes the repository-wide 52% sole-author
   rule effective for maintenance from 2026-09-26, including work in flight.
   Under the owner ruling on #299, maintenance commits and owner-directed
   corrections are assistance excluded from authorship shares. Earlier
   statements that the rule awaited #284 are pre-rule history only; O-041's
   exception and arithmetic remain recorded, while O-043 governs #293's
   current classification. Cursor remains its independent reviewer and
   guarded merger; neither Claude nor Codex may review or merge #293.

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
| [`rollout-packet.md`](rollout-packet.md) | Mandatory rollout packet; `analysis_only` deployment intent, `explicitly_scoped` external effects, all Slack identities pending |
| [`workstream-plan.md`](workstream-plan.md) | M0 phase plan (closed, O-031) |
| [`workstream-plan-m1.md`](workstream-plan-m1.md) | M1 phase plan (approved O-038/O-039) with the M1 gate record (closed 2026-09-28, D-M1-G; winner Qwen3-14B) |
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 06:20 UTC | BEGIN]] -->
| [`workstream-plan-m2.md`](workstream-plan-m2.md) | M2 plan, owner-approved 2026-09-28 (O-M2-A): DIRECTOR data v1 packets, adapter v0.1 to v0.3, gates and stops; packets dispatch only under their own gates, caps and the D-M2-8 meter rule; row contract in `schemas/director-data-v1-row.schema.json` |
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 06:20 UTC | END]] -->
| [`train/prompt_template.py`](train/prompt_template.py) | The one DIRECTOR prompt rendering shared by evaluation (`eval/generate_candidates.py`) and training (M2-T1): the D-M1-1 input (objective, repository, title) and the D-M2-10 input that adds cited `context` items; `eval/build_inputs.py --context` builds the latter and refuses context that cites or leaks a held-out item (packet M2-C1) |
| [`eval/validate_rows.py`](eval/validate_rows.py) | M2-S1 row validator: schema, canonical `row_id`, provider `terms_verified_at`, manifest binding to both held-out sets, the three D-M2-3 leak-wall checks (lineage, title and objective, shingle near-duplicate with a quarantine band) and the group-wise train/dev split; writes `accepted.jsonl`, `quarantine.jsonl` and `report.json` |
| [`train/train_adapter.py`](train/train_adapter.py) | M2-T1 QLoRA trainer. Response-only loss on the validator's train rows, with the attention mask kept on the batch. Smoke-base 20-step dry run and the pinned Qwen3-14B probe. `scripts/train-adapter.ps1` holds the GPU lock and the storage guards |
| [`eval/generate_rows.py`](eval/generate_rows.py) | M2-G1 exemplar harness. One bounded competing-only dispatch per scenario through `dispatch.py`. A dry run writes zero rows and zero receipts. A live run writes schema-valid rows only after the leak wall and the dataset scan |
| [`decision-log.html`](decision-log.html) | One line per owner or design decision |
| [`director-resumption-20261031.html`](director-resumption-20261031.html) | Director resumption and state checkpoint, 2026-10-31: owner direction, where Claude left off, the 2026-10-07 to 2026-10-31 record gap including the owner's stop and handover, outcome numbers, rulings, and the direction proposal awaiting the owner |
| [`director-orders-20261031.html`](director-orders-20261031.html) | Director decisions under the owner's rolling authorization of 2026-10-31 (D-M2-69) and the first three bounded work orders: host seat intake and packet readout, grading of the twelve staged candidates, and a conditional A10 teacher top-up |
| [`run-sheet-adapter-v01-20261031.html`](run-sheet-adapter-v01-20261031.html) | One-page run sheet for the first adapter v0.1 session on a rented GPU: pins, pre-GPU checks, the session sequence, post-GPU scoring, stops |
| [`cursor-host-packet-20261031.html`](cursor-host-packet-20261031.html) | The exact host workload for the Cursor BlackBox worker under the memory-safe tandem of 2026-10-31: steps H0 to H4, memory rules, gates, report format and caps (D-M2-70) |
| [`project-state-report.md`](project-state-report.md) | Evidence-first milestone report (M0 closed under O-031; record reconciliation pending until PR #293 gates pass) |

## Milestones

| # | Milestone | Exit gate | Stop |
|---|---|---|---|
| PR-0 | This subsection merged | `main` contains `projects/homemade-llm/` | exact-SHA ACCEPT and green checks; merge by a non-contributing reviewer that has thoroughly reviewed the work |
| M0 | Foundation: `C:\llm` layout, Unsloth smoke fine-tune on the GPU, LM Studio serving a merged GGUF, Open WebUI reaching it, Ollama retired, mirror job | state report with every number verified | closed under O-031; record reconciliation pending until PR #293 gates pass ([`project-state-report.md`](project-state-report.md)); initial report filed 2026-09-25 (history) |
| M1 | Base-model shootout | winner per family with license check; owner sign-off | gate |
| M2 | DIRECTOR data v1 + adapter v0.1 → v0.3 | > 60 % field-level acceptance on both held-out sets; no > 3-point general regression; week-4 early warning at < 45 % | gate |
| M3 | DIRECTOR v1.0 as a local provider lane (owner decision required; after PRs #263/#264) | **L1 DIRECTOR launch checklist** (`PLAN.md` §2.1), target 2026-11-12 | gate |
| M4 | COMPANION Persona Bible + Stage A library | Bible and source list approved; Stage A perplexity gate | gate |
| M5a/b | COMPANION Stage B / Stage C | **L2 COMPANION launch checklist** (`PLAN.md` §2.2), target 2026-12-17 | gate |
| M6 | Hardening, runbooks, restore drill | fresh-machine restore < 1 day | gate |
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-24 | 07:55 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 08:16 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-25 | 23:56 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:47 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:35 UTC | END]] -->
<!-- [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:18 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:27 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 23:08 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:34 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:56 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:19 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 09:29 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:35 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:44 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:25 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:35 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 02:13 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 04:14 UTC | END]] -->
<!-- [[OpenAI Codex | GPT-6 | 2026-09-27 | 13:29 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 03:10 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 00:15 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 06:20 UTC | END]] -->
<!-- [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 07:45 UTC | END]] -->
<!-- [[Cursor | Auto | 2026-09-29 | 03:30 UTC | END]] -->
<!-- [[Cursor | Auto | 2026-09-29 | 04:22 UTC | END]] -->
<!-- [[Cursor | Auto | 2026-09-29 | 05:32 UTC | END]] -->
