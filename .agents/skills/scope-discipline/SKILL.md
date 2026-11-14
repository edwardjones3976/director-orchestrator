---
name: scope-discipline
description: "Keep an engineering assignment in scope and cheap in tokens using the repository's OpenSpec change folder, Beads task memory and the Ponytail ladder."
when_to_use: "At the start of every Director-Orchestrator engineering session, before editing code, when handing work to another provider, and when a task looks larger than the order."
---

# Scope discipline

The rules live in `docs/scope-and-context-discipline.html`. This skill is the session routine only.

1. Read the assignment's change folder in `openspec/changes/`. It is the whole brief.
2. Run `bd prime`, then `bd ready`. Take exactly one task.
3. Walk the Ponytail ladder; stop at the first rung that solves the task.
4. Run the done command named in `tasks.md`.
5. Tick the task, close it in Beads, `bd remember` what the next session needs.
6. End the session. Hand off the change-folder path, the task id and the head SHA.

Work that is not on the task list is out of scope: stop and ask the issuer. If a tool is missing, run `python scripts/check_scope_tools.py` and install what it lists on this host.
