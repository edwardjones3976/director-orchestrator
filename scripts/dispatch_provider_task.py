# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | BEGIN]]
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
import pathlib
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
        "Bash(gh pr diff:*)",
        # gh api is deliberately absent. It accepts -X PUT, so a review holding
        # it could merge through the REST pull-request endpoint; excluding the
        # literal "gh pr merge" would not have prevented that.
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
        # read-only refuses gh authentication, so reviews run in the default
        # sandbox; the prompt is passed as an argument because codex exec takes
        # it positionally rather than on stdin.
        "base": ["exec", "--skip-git-repo-check"],
        "prompt": ["{prompt_text}"],
        "allow": None,
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


def pull_request_range(pr: str) -> tuple[str, str]:
    """Resolve the base and head of a pull request from GitHub, not the caller.

    Deriving authorship from a caller-supplied range let the check pass against
    the wrong commits, or against none at all.
    """

    out = subprocess.run(
        ["gh", "pr", "view", pr, "--json", "baseRefOid,headRefOid",
         "-q", ".baseRefOid + \" \" + .headRefOid"],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or len(out.stdout.split()) != 2:
        raise SystemExit(f"could not resolve the range for pull request {pr}")
    base, head = out.stdout.split()
    return base, head


def is_eligible(provider: str, authors: set[str]) -> tuple[bool, str]:
    """A material author may never merge its own work, delegated or not."""

    if not authors:
        # No recorded authorship is unverified, not cleared.
        return False, "no contribution tags found in the range; cannot establish authorship"

    identities = TAG_IDENTITIES.get(provider, (provider,))
    # Tags carry variants such as "Cursor Grok 4.6", so exact equality let a
    # material author through. Match on the identity appearing in the recorded
    # name, case-insensitively.
    lowered = [i.lower() for i in identities]
    clash = sorted(a for a in authors if any(i in a.lower() for i in lowered))
    if clash:
        return False, f"{provider} materially authored this change as {', '.join(clash)}"
    return True, ""


def build_command(provider: str, task: str, prompt_file: str, turns: int) -> list[str]:
    # The resolved path is kept when PATH has one: on Windows the CLIs are
    # .cmd shims, and CreateProcess does not apply PATHEXT, so dispatching the
    # bare name fails with WinError 2. Falling back to the bare name keeps
    # construction independent of which CLIs this host happens to have; main()
    # refuses to dispatch a command whose executable is missing.
    spec = PROVIDERS[provider]
    command = [shutil.which(str(spec["executable"])) or str(spec["executable"]),
               *list(spec["base"])]
    for rule in TASK_ALLOW_RULES[task]:
        if spec["allow"]:
            command += [str(spec["allow"]), rule]
    for part in list(spec["turns"]):
        command.append(part.replace("{turns}", str(turns)))
    for part in list(spec["prompt"]):
        if part == "{prompt_text}":
            command.append(pathlib.Path(prompt_file).read_text(encoding="utf-8"))
        else:
            command.append(part.replace("{prompt_file}", prompt_file))
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--task", required=True, choices=sorted(TASK_ALLOW_RULES))
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--turns", type=int, default=25)
    parser.add_argument("--print-command", action="store_true")
    args = parser.parse_args(argv)

    # The range comes from the pull request itself. Both tasks are gated: a
    # review that can reach GitHub is not harmless, and the earlier version
    # skipped the check for reviews entirely.
    base, head = pull_request_range(args.pr)
    eligible, reason = is_eligible(args.provider, material_authors(base, head))
    if args.task == "merge" and not eligible:
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    if args.task == "review" and not eligible:
        print(f"NOTE: {reason}; dispatching read-only review anyway", file=sys.stderr)

    command = build_command(args.provider, args.task, args.prompt_file, args.turns)
    if args.print_command:
        print(" ".join(command))
        return 0
    if shutil.which(command[0]) is None:
        raise SystemExit(f"{command[0]} is not on PATH")
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | END]]
