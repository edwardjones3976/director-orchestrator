# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | BEGIN]]
"""Owner-installed launcher for the twenty-check scorecard (review 4112412081).

This repository copy is only the reviewed source. The owner copies it from the reviewed evaluator commit to
``~/.director-scorecard/launcher.py`` and runs THAT copy, isolated from the checkout:

    python -I ~/.director-scorecard/launcher.py <repository> --candidate <sha> --output-dir <folder> [flags]

It reads ``evaluator_sha`` from ``~/.director-scorecard/release-descriptor.json``, requires that commit on
github/main in <repository>, extracts exactly that commit's tree with ``git archive`` into a fresh temporary
directory, and runs the archived ``scripts/director-scorecard.py`` with ``PYTHONPATH`` set to the archived ``src``.
No file of the working checkout ever executes; git itself is trusted. Standard library only.
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

RELEASE_DESCRIPTOR = Path.home() / ".director-scorecard" / "release-descriptor.json"
DESCRIPTOR_VERSION = "director-cli-release-descriptor/v1"
TREE_ENV = "DIRECTOR_SCORECARD_TREE"  # the archived evaluator tree the scorecard must be running from
REPO_ENV = "DIRECTOR_SCORECARD_REPO"  # the repository git reads candidate and evaluator commits from
SHA_ENV = "DIRECTOR_SCORECARD_EVALUATOR_SHA"  # the exact pin the tree was extracted from; the child must still see it
REFUSAL = "scorecard not run from the pinned evaluator checkout"


def pinned_evaluator(descriptor: Path = RELEASE_DESCRIPTOR) -> str | None:
    """The descriptor's evaluator_sha (40 lowercase hex), or None: absent, a link, oversized, malformed, another
    version or no pin."""
    try:
        if descriptor.is_symlink() or not descriptor.is_file() or descriptor.stat().st_size > 1_048_576:
            return None
        value = json.loads(descriptor.read_bytes())
    except (OSError, ValueError):
        return None
    pin = value.get("evaluator_sha") if isinstance(value, dict) and value.get("version") == DESCRIPTOR_VERSION else None
    return pin if isinstance(pin, str) and len(pin) == 40 and all(c in "0123456789abcdef" for c in pin) else None


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, timeout=120, check=True,
                          stdin=subprocess.DEVNULL).stdout


def launch(repo: Path, arguments: list[str], descriptor: Path = RELEASE_DESCRIPTOR) -> int:
    pin = pinned_evaluator(descriptor)
    if pin is None:
        return _refuse("evaluator revision not pinned")
    try:
        _git(repo, "merge-base", "--is-ancestor", pin, "github/main")
    except (OSError, subprocess.SubprocessError):
        return _refuse("the pinned evaluator revision is not on github/main")
    here = Path.cwd().resolve()
    if here == repo or repo in here.parents:
        return _refuse("run the launcher from a working directory outside the checkout")
    with tempfile.TemporaryDirectory(prefix="director-scorecard-") as folder:
        tree = Path(folder) / "tree"
        try:
            archive = _git(repo, "archive", "--format=tar", pin)
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(tree, filter="data")
        except (OSError, subprocess.SubprocessError, tarfile.TarError):
            return _refuse("the pinned evaluator tree could not be extracted")
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
        env.update({"PYTHONPATH": str(tree / "src"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    TREE_ENV: str(tree), REPO_ENV: str(repo), SHA_ENV: pin})
        return subprocess.run([sys.executable, "-s", str(tree / "scripts" / "director-scorecard.py"), *arguments],
                              env=env, cwd=here).returncode


def _refuse(reason: str) -> int:
    print(f"{REFUSAL}: {reason}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if not sys.flags.isolated:
        return _refuse("run the launcher with python -I so nothing on PYTHONPATH or in the checkout is imported")
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    return launch(Path(argv[0]).resolve(), argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | END]]
