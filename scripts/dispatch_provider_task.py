# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | BEGIN]]
"""Dispatch a bounded task to a provider CLI with least-privilege permissions.

Provider CLIs read their permission rules from the shared Claude settings file,
which has no `gh` rule. A CLI that is denied a command reports the session as
`cancelled`, which reads exactly like a hung or broken provider. Four separate
Grok dispatches were abandoned on that misreading before the cause was measured:
`git rev-parse` returned `end_turn`, `gh pr view` returned `cancelled`, and the
same prompt with `--allow 'Bash(gh pr view:*)'` returned the answer.

So permissions are granted per invocation, from the smallest set the task needs,
and never by widening the shared settings file. A review may read; only a merge
may merge.

Eligibility is computed, not asserted. A provider named in the contribution tags
of the pull request's own commits materially authored it and cannot merge it, so
this refuses to build that command at all.

    python scripts/dispatch_provider_task.py --pr 77 --task review --provider grok
    python scripts/dispatch_provider_task.py --pr 77 --task merge --provider grok
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys


# Least privilege per task. A review never receives a rule that can write.
TASK_ALLOW_RULES: dict[str, tuple[str, ...]] = {
    "review": (
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git show:*)",
        "Bash(git rev-parse:*)",
        "Bash(git merge-base:*)",
        "Bash(gh pr view:*)",
        "Bash(gh pr checks:*)",
        "Bash(gh api:*)",
    ),
    "merge": (
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git rev-parse:*)",
        "Bash(gh pr view:*)",
        "Bash(gh pr checks:*)",
        "Bash(gh pr merge:*)",
    ),
}

# How each CLI takes a prompt, an allow rule, and a turn bound. Cursor is absent
# on purpose: its CLI refuses to run on Windows without disabling its sandbox,
# and a sandbox is not something a dispatcher may switch off.
PROVIDERS: dict[str, dict[str, object]] = {
    "grok": {
        "executable": "grok",
        "base": ["--output-format", "json", "--permission-mode", "dontAsk"],
        "prompt": ["--prompt-file", "{prompt_file}"],
        "allow": "--allow",
        "turns": ["--max-turns", "{turns}"],
    },
    "codex": {
        "executable": "codex",
        "base": ["exec", "--skip-git-repo-check"],
        "prompt": ["{prompt_file_stdin}"],
        "allow": None,  # codex exec is already bounded by its own sandbox
        "turns": [],
    },
}

# The identity each provider signs contribution tags with.
TAG_IDENTITIES: dict[str, tuple[str, ...]] = {
    "grok": ("Grok", "Cursor Grok"),
    "codex": ("OpenAI Codex", "Codex"),
    "claude": ("Claude Code", "Claude"),
    "cursor": ("Cursor",),
}

_TAG = re.compile(r"\[\[\s*([^|\]]+?)\s*\|")


def material_authors(base: str, head: str) -> set[str]:
    """Return the contributor names recorded in the commits under review."""

    out = subprocess.run(
        ["git", "log", "--format=%B", f"{base}..{head}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {name.strip() for name in _TAG.findall(out)}


def is_eligible(provider: str, authors: set[str]) -> tuple[bool, str]:
    """A material author may never merge its own work, delegated or not."""

    identities = TAG_IDENTITIES.get(provider, (provider,))
    clash = sorted(a for a in authors if any(a == i for i in identities))
    if clash:
        return False, f"{provider} materially authored this change as {', '.join(clash)}"
    return True, ""


def build_command(provider: str, task: str, prompt_file: str, turns: int) -> list[str]:
    spec = PROVIDERS[provider]
    executable = shutil.which(str(spec["executable"]))
    if executable is None:
        raise SystemExit(f"{spec['executable']} is not on PATH")
    command = [executable, *list(spec["base"])]
    for rule in TASK_ALLOW_RULES[task]:
        if spec["allow"]:
            command += [str(spec["allow"]), rule]
    for part in list(spec["turns"]):
        command.append(part.replace("{turns}", str(turns)))
    for part in list(spec["prompt"]):
        if part == "{prompt_file_stdin}":
            continue
        command.append(part.replace("{prompt_file}", prompt_file))
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--task", required=True, choices=sorted(TASK_ALLOW_RULES))
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--base", default="github/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--turns", type=int, default=25)
    parser.add_argument("--print-command", action="store_true")
    args = parser.parse_args(argv)

    if args.task == "merge":
        eligible, reason = is_eligible(args.provider, material_authors(args.base, args.head))
        if not eligible:
            print(f"REFUSED: {reason}", file=sys.stderr)
            return 2

    command = build_command(args.provider, args.task, args.prompt_file, args.turns)
    if args.print_command:
        print(" ".join(command))
        return 0
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | END]]
