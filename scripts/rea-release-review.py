"""Opt-in CLI entry point for static release artifact review."""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rea_release_lab.review import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
