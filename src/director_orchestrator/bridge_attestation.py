# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | BEGIN]]
"""Read-only CLI restriction sets for host-bridge lanes in the small class.

Owner ruling, 2026-09-24: "Bridge with attested sandbox". A host-bridge lane
joins the small read-only class only when the bridge's authenticated
challenge reports the exact argv it runs for that lane and that argv carries
every restriction below. This attests the configured command line, not an
operating-system sandbox: the CLI still runs as a host process, and the
restrictions are the CLI's own flags.

Every flag here is one this repository already passes to that CLI. A
restriction the repository has never expressed for a CLI is not invented, so
a lane whose required set cannot be written as bridge argv is never attested:
Claude's empty ``--tools ""`` value cannot be configured on the bridge, whose
executor refuses empty argv items, so the Claude bridge lane is never attested.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import PurePath

from .native_acceptance_streams import GROK_DENY_TOOLS
from .packet_contracts import canonical_json_bytes


# Per lane: flags that must carry exactly this value wherever they appear
# (at least once), switches that must be present, comma-list flags that must
# name every listed item, and repeatable flags that must name every value.
READ_ONLY_REQUIREMENTS: dict[str, dict[str, object]] = {
    # native_acceptance_capture.py and zai_trial.py (tool and MCP removal) and
    # primary_transports.py (plan permission mode).
    "claude": {
        "values": {"--permission-mode": "plan", "--tools": "",
                   "--mcp-config": '{"mcpServers":{}}'},
        "switches": ("--strict-mcp-config", "--safe-mode"),
        "lists": {},
        "repeated": {},
    },
    # providers.py (plan mode, no web search, no subagents, bounded turns) and
    # native_acceptance_streams.py (canonical deny list and permission denials).
    "grok": {
        "values": {"--permission-mode": "plan", "--max-turns": "1"},
        "switches": ("--disable-web-search", "--no-subagents"),
        "lists": {"--disallowed-tools": tuple(GROK_DENY_TOOLS.split(","))},
        "repeated": {"--deny": ("Bash", "Edit", "Write", "MCPTool")},
    },
}
# Arguments that widen what the CLI may do, refused for every lane wherever
# they appear, alone or as ``--flag=value``, in any letter case. The Grok
# review argv test (test_native_acceptance_grok.py) asserts this same set is
# absent: auto-approval (``--yolo``), permission skips, tool allow-lists, extra
# directories, web search, resumed or imported sessions, redirected APIs,
# schema overrides, and the ``acp``/``exec`` subcommands.
FORBIDDEN_ARGS = frozenset({
    "--yolo", "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions", "--allowedtools", "--allowed-tools",
    "--add-dir", "--enable-web-search", "--resume", "--continue", "--api-key",
    "--base-url", "--api", "--json-schema", "acp", "exec",
})


def argv_digest(argv: Sequence[str]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(list(argv))).hexdigest()


def read_only_attested(provider: str, argv: Sequence[str]) -> bool:
    """Whether this lane's argv carries its whole read-only restriction set."""

    required = READ_ONLY_REQUIREMENTS.get(provider)
    if required is None or not argv or any(not isinstance(item, str) for item in argv):
        return False
    args = list(argv[1:])
    governed = {*required["values"], *required["switches"], *required["lists"],
                *required["repeated"]}
    if any(item.split("=", 1)[0].lower() in FORBIDDEN_ARGS for item in args):
        return False
    # Only the space-separated form the repository uses is recognised.
    if any("=" in item and item.split("=", 1)[0] in governed for item in args):
        return False

    def values(flag: str) -> list[str] | None:
        found = []
        for index, item in enumerate(args):
            if item == flag:
                if index + 1 >= len(args):
                    return None
                found.append(args[index + 1])
        return found

    for flag, value in required["values"].items():
        seen = values(flag)
        if not seen or any(item != value for item in seen):
            return False
    if any(flag not in args for flag in required["switches"]):
        return False
    for flag, names in required["lists"].items():
        seen = values(flag)
        if not seen or any(not set(names) <= set(item.split(",")) for item in seen):
            return False
    for flag, names in required["repeated"].items():
        seen = values(flag)
        if seen is None or not set(names) <= set(seen):
            return False
    return True


# Host commands known to enforce DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD and
# DIRECTOR_PROVIDER_TOTAL_TOKEN_LIMIT before any inference (tokenize, bound,
# then send with a hard output cap). The Grok CLI has no token or dollar cap
# flag in this repository, so an argv that runs it cannot honor them before
# launch. Reported only: by the owner's ruling of 2026-09-24 ("Accept
# post-hoc cap") an attested Grok reviewer is admitted and its cap is checked
# on the result.
ATTEMPT_LIMIT_COMMANDS = frozenset({"xai-responses-command.py"})


def enforces_attempt_limits(argv: Sequence[str]) -> bool:
    """Whether this argv runs a command that enforces the attempt limits pre-launch."""

    return any(PurePath(str(item)).name in ATTEMPT_LIMIT_COMMANDS for item in argv)


# Lanes whose exact host argv is reported (and signed) without any read-only
# claim: the Z.ai entry names its GLM model, bounds and key-ID digest, which the
# Director checks against its own expectation before dispatching. It never
# joins the small read-only class.
IDENTITY_REPORTED_LANES = frozenset({"zai"})


def lane_attestations(commands: Mapping[str, Sequence[str]]) -> dict[str, dict[str, object]]:
    """What the bridge reports for each lane that has a restriction set or identity."""

    return {
        lane: {"argv": list(argv), "argv_digest": argv_digest(argv),
               "read_only_attested": read_only_attested(lane, argv),
               "enforces_attempt_limits": enforces_attempt_limits(argv)}
        for lane, argv in sorted(commands.items())
        if lane in READ_ONLY_REQUIREMENTS or lane in IDENTITY_REPORTED_LANES
    }
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 22:31 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:15 UTC | END]]
