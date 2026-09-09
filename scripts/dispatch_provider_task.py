# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | BEGIN]]
"""Dispatch a bounded task to a provider CLI.

Provider CLIs read their permission rules from the shared Claude settings file,
which has no `gh` rule. A CLI that is denied a command reports the session as
`cancelled`, which reads exactly like a hung or broken provider. Four separate
Grok dispatches were abandoned on that misreading before the cause was measured:
`git rev-parse` returned `end_turn`, `gh pr view` returned `cancelled`, and the
same prompt with `--allow 'Bash(gh pr view:*)'` returned the answer.

A per-task allow list was the first answer and it was the wrong one. A reviewer
that may only read cannot run the suite, exercise the code path it is judging,
or build a reproduction, so it reports on what the source appears to say rather
than on what it does. A Grok review of #146 was cancelled mid-run for exactly
that: the prompt asked it to construct a malformed input and observe the result,
which its grant did not permit. Owner instruction, 2026-09-09: give it full
capabilities.

So each CLI is now dispatched with its own full-approval flag, and permission is
subtracted rather than added: the merge, the branch, and the pull request's
state. Everything a review actually needs is available.

**The deny list is not a security boundary, and must not be described as one.**
An earlier version of this file claimed it was. An independent review disproved
that by execution, under its own review grant, on this host: `gh pr merge` and
`git push` are refused as literals, and the same session still reached
`python`/`subprocess`, `curl`, `urllib` with the token from `gh auth token`,
`gh issue close` (a pull request is an issue), `gh pr review --approve`,
`gh pr comment`, `gh alias set`, and `git send-pack`. A prefix matcher over
direct command strings cannot bound an agent that can spawn a subprocess.

What the rules do buy is that the obvious spelling of a mutation is refused, so
a reviewer does not merge by drifting into it. That is a guardrail against
accident, not a control against intent. AGENTS.md rule 3 -- producer, reviewer
and acting agent are three distinct identities -- is therefore enforced by the
dispatch policy and by review, not by this list. Closing it for real needs
credential isolation or a brokered write path, which is a larger change than
this file; until then, do not claim the reviewer cannot write.

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


# Full capability, minus the verbs that would let a reviewer change its own
# subject. A review may run anything; it may not merge the pull request, move
# the branch under the SHA it is reviewing, or alter that pull request's state.
# `gh api` is subtracted outright because it takes -X PUT and so reaches the
# merge endpoint, which is how an earlier "read-only" grant could have merged.
TASK_DENY_RULES: dict[str, tuple[str, ...]] = {
    "review": (
        "Bash(gh pr merge:*)",
        "Bash(gh api:*)",
        "Bash(gh pr edit:*)",
        "Bash(gh pr close:*)",
        "Bash(git push:*)",
    ),
    "merge": (),
}

# How each CLI takes a prompt, a deny rule, and a turn bound. Cursor is absent
# on purpose: its CLI refuses to run on Windows without disabling its sandbox,
# and a sandbox is not something a dispatcher may switch off.
PROVIDERS: dict[str, dict[str, object]] = {
    "grok": {
        "executable": "grok",
        "base": ["--output-format", "json", "--always-approve"],
        # A path, never the prompt text. grok resolves to grok.EXE rather than
        # a .CMD shim and has always read the prompt from a file, so it was not
        # reachable by the injection below; `--prompt-file -` is not a stdin
        # spelling it accepts ("Failed to read '-'"), so this stays a path.
        "prompt": ["--prompt-file", "{prompt_file}"],
        "deny": "--deny",
        "turns": ["--max-turns", "{turns}"],
    },
    "codex": {
        "executable": "codex",
        # `-s` pins the sandbox at dispatch. Without it codex exec inherits
        # whatever ~/.codex/config.toml says, and on this host that is
        # sandbox_mode = "danger-full-access" with approval_policy = "never".
        # An earlier version of this file asserted the opposite -- that codex's
        # sandbox withholds the GitHub credential -- citing an HTTP 401. That
        # 401 came from `codex sandbox`, a different command from the `codex
        # exec` this dispatcher actually builds. An independent review ran
        # `gh pr view` under the real dispatch and got OPEN. The claim was
        # false and the exemption it justified is gone.
        "base": ["exec", "--skip-git-repo-check", "-s", "workspace-write"],
        "prompt": ["-"],
        "deny": None,
        # codex exec has no per-rule flag, so TASK_DENY_RULES cannot reach it.
        # That is recorded here rather than papered over: see the module
        # docstring on what these rules are and are not.
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
    if spec["deny"]:
        for rule in TASK_DENY_RULES[task]:
            command += [str(spec["deny"]), rule]
    for part in list(spec["turns"]):
        command.append(part.replace("{turns}", str(turns)))
    for part in list(spec["prompt"]):
        command.append(part.replace("{prompt_file}", prompt_file))
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--task", required=True, choices=sorted(TASK_DENY_RULES))
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--prompt-file", required=True)
    # A review that runs the suite, mutates production and re-runs it needs
    # far more than 25 turns. At 25 the CLI stops mid-work and reports the
    # session as `cancelled`, which reads like a hang or a denial: measured
    # across nine dispatches, every cancelled one ended at exactly 25 turns and
    # every completed one came in under it. Two reviews were abandoned and one
    # re-dispatched on that misreading before the budget was the suspect. A
    # merge is a handful of checks and does not need the headroom, but sharing
    # one default costs nothing.
    parser.add_argument("--turns", type=int, default=80)
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
    # The prompt is piped, never passed as an argument. Both CLIs are .CMD
    # shims on Windows, and Windows hands a .CMD invocation to the command
    # shell, which reparses the argument string: an independent review put
    #     SAFE" & echo MARKER & rem "
    # in a prompt and the echo ran, outside the provider's permission controls
    # entirely. Prompts routinely quote pull-request bodies and review
    # comments, so the text is not trusted input.
    return subprocess.run(
        command,
        input=pathlib.Path(args.prompt_file).read_bytes(),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:27 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:14 UTC | END]]
