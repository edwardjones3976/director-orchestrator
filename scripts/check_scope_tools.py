"""Report whether this host has the scope-discipline tools; installs nothing.

See docs/scope-and-context-discipline.html. Exit 0 = ready, 1 = something missing.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

OPENSPEC_VERSION = "2.0.0"
ROOT = Path(__file__).resolve().parents[1]
INSTALL = {
    "openspec": f"npm install -g @fission-ai/openspec@{OPENSPEC_VERSION}",
    "bd": "see https://github.com/gastownhall/beads (installation section)",
}


def _version(executable: str) -> str:
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30)
    return (result.stdout or result.stderr).strip()


def check(root: Path = ROOT, which=shutil.which, version=_version) -> list[str]:
    """Return one line per problem; an empty list means the host is ready."""
    problems = []
    if not (root / "openspec" / "config.yaml").is_file():
        problems.append("openspec/config.yaml is missing from this checkout")
    for tool, command in INSTALL.items():
        path = which(tool)
        if path is None:
            problems.append(f"{tool} is not on PATH: {command}")
        elif tool == "openspec" and OPENSPEC_VERSION not in version(path):
            problems.append(f"openspec is not the pinned {OPENSPEC_VERSION}: {command}")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print("MISSING: " + problem)
    if not problems:
        print(f"READY: openspec {OPENSPEC_VERSION} and bd are installed; openspec/ is present")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
