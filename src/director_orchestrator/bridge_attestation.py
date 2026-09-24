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
# Flags that widen what the CLI may do, refused wherever they appear.
_WIDENING = frozenset({
    "--dangerously-skip-permissions", "--allowedTools", "--allowed-tools",
    "--add-dir", "--enable-web-search",
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
    if any(item.split("=", 1)[0] in _WIDENING for item in args):
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


def lane_attestations(commands: Mapping[str, Sequence[str]]) -> dict[str, dict[str, object]]:
    """What the bridge reports for each lane that has a restriction set."""

    return {
        lane: {"argv": list(argv), "argv_digest": argv_digest(argv),
               "read_only_attested": read_only_attested(lane, argv)}
        for lane, argv in sorted(commands.items())
        if lane in READ_ONLY_REQUIREMENTS
    }
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 16:48 UTC | END]]
