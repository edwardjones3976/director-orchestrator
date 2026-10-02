"""Committed text must not carry a personal Windows home path."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {ROOT / "projects" / "homemade-llm" / "decision-log.md"}
TEXT_SUFFIXES = {
    ".md", ".py", ".ps1", ".example", ".yaml", ".yml", ".txt", ".toml", ".ini", ".sh",
}
HOME = ("Users" + "/edwar").encode()
HOME_WIN = ("Users" + "\\edwar").encode()


class PersonalHomePathTests(unittest.TestCase):
    def test_committed_text_has_no_personal_home_path(self) -> None:
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path in ALLOWED:
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            data = path.read_bytes()
            if HOME in data or HOME_WIN in data:
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, [])


