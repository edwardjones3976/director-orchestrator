import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/scope-and-context-discipline.html"
SPEC = importlib.util.spec_from_file_location("check_scope_tools", ROOT / "scripts" / "check_scope_tools.py")
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


class ScopeDisciplineTest(unittest.TestCase):
    def test_every_instruction_surface_points_to_the_canonical_document(self):
        self.assertTrue((ROOT / DOC).is_file())
        for surface in ("AGENTS.md", "CLAUDE.md", ".cursor/rules/scope-and-context-discipline.mdc",
                        ".agents/skills/scope-discipline/SKILL.md"):
            self.assertIn(DOC, (ROOT / surface).read_text(encoding="utf-8"), surface)

    def test_document_states_the_authority_boundary_and_the_pinned_version(self):
        text = (ROOT / DOC).read_text(encoding="utf-8")
        self.assertIn("Beads is not a second queue", text)
        self.assertIn("never widens one", text)
        self.assertIn(tools.OPENSPEC_VERSION, text)

    def test_repository_carries_the_openspec_layout(self):
        self.assertEqual((ROOT / "openspec" / "config.yaml").read_text(encoding="utf-8").split("\n")[0],
                         "schema: spec-driven")
        for directory in ("specs", "changes/archive"):
            self.assertTrue((ROOT / "openspec" / directory).is_dir(), directory)

    def test_check_reports_each_missing_tool_and_a_wrong_version(self):
        with tempfile.TemporaryDirectory() as empty:
            problems = tools.check(Path(empty), which=lambda tool: None)
        self.assertEqual(len(problems), 3)
        problems = tools.check(ROOT, which=lambda tool: tool, version=lambda path: "1.3.1")
        self.assertEqual(len(problems), 1)
        self.assertIn("pinned", problems[0])

    def test_check_is_ready_when_both_tools_are_present(self):
        self.assertEqual(tools.check(ROOT, which=lambda tool: tool,
                                     version=lambda path: tools.OPENSPEC_VERSION), [])


if __name__ == "__main__":
    unittest.main()
