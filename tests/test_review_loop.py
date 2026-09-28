# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:58 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:46 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 11:19 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | BEGIN]]
"""Review-loop controls: instruction-surface pointer and drift checks (issue #299).

Hosted CI runs these against fixtures only; nothing here reads a home directory.
"""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("review_loop_doc_guard", ROOT / "scripts/check_active_documentation.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

CHARTER_TABLE = """| Provider lane | Engineering (default) |
| --- | ---: |
| OpenAI / Codex production | 40% |
| Claude | 25% |
| Cursor | 15% |"""


def rules(errors):
    return {error.split(":", 1)[0] for error in errors}


class InstructionSurfaceDriftTests(unittest.TestCase):
    def test_current_repository_surfaces_point_and_do_not_copy(self):
        self.assertEqual([], guard.check_instruction_surfaces(ROOT))
        self.assertIn("CLAUDE.md", guard.POINTER_SURFACES)

    def test_copied_allocation_dispatch_and_merge_rules_fail(self):
        cases = {
            CHARTER_TABLE: "copied-allocation-shares",
            "The owner set engineering to OpenAI 40% / Claude 25%.": "copied-allocation-shares",
            "Aim for eight OpenAI, five Claude, three Cursor assignments.": "copied-dispatch-counts",
            "Keep each lane within the ±3-point band.": "copied-allocation-window",
            "Count the rolling twenty-assignment window.": "copied-allocation-window",
            "Never exceed target+5.": "copied-allocation-window",
            "Stay within plus or minus three percentage points.": "copied-allocation-window",
            "Merging requires one independent non-author reviewer.": "copied-merge-rule",
            "Owner: If they approve it, they should merge it as well.": "copied-merge-rule",
            "Each PR should be authored by one person and reviewed by a single reviewer.": "copied-merge-rule",
        }
        for text, rule in cases.items():
            with self.subTest(text=text):
                self.assertIn(rule, rules(guard.check_instruction_text(text)))

    def test_agents_rule_eleven_copied_into_a_local_file_is_drift(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        rule = next(line for line in agents.splitlines() if line.startswith("11. "))
        self.assertIn("copied-allocation-window", rules(guard.check_instruction_text(rule)))

    def test_arbitrary_numbers_and_technical_instructions_are_not_drift(self):
        for text in (
            "Run the suite with 4 workers; coverage must stay above 80%.",
            "Claude uses 25% fewer tokens with caching enabled.",
            "Ask one Claude session to reproduce the failure.",
            "Read the merge base before running git diff.",
            "Retry twice, then report the blocker.",
            "Point 3 of the plan covers the migration.",
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
            # Two providers with numbers, but no allocation or dispatch language.
            "In benchmarks, Claude passes 80% of cases and OpenAI passes 90%.",
            "Install one Claude CLI and two Codex plugins.",
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]
        ):
            with self.subTest(text=text):
                self.assertEqual([], guard.check_instruction_text(text))

# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
    def test_shares_and_counts_are_drift_only_with_routing_language(self):
        for text, rule in (
            ("Allocate work as Claude 25% and OpenAI 40%.", "copied-allocation-shares"),
            ("Dispatch two Claude and three Codex assignments per week.", "copied-dispatch-counts"),
            ("Lane targets: OpenAI 40%, Cursor 15%.", "copied-allocation-shares"),
        ):
            with self.subTest(text=text):
                self.assertIn(rule, rules(guard.check_instruction_text(text)))
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]

    def test_pointer_surface_requires_a_canonical_pointer(self):
        self.assertIn("missing-canonical-pointer", rules(guard.check_instruction_text("Be concise.", pointer="always")))
        for pointer in ("@AGENTS.md", "See AGENTS.md.", "[charter](docs/team-operating-charter.md#review-to-merge-handoff)"):
            with self.subTest(pointer=pointer):
                self.assertEqual([], guard.check_instruction_text(pointer, pointer="always"))
        # A pointer inside a historical block does not count as an active pointer.
        historical = "<!-- director-doc-history BEGIN: old pointer -->\n@AGENTS.md\n<!-- director-doc-history END -->"
        self.assertIn("missing-canonical-pointer", rules(guard.check_instruction_text(historical, pointer="always")))

    def test_topic_pointer_applies_only_when_governance_is_discussed(self):
        self.assertEqual([], guard.check_instruction_text("Prefer small diffs.", pointer="topic"))
        self.assertIn("missing-canonical-pointer", rules(guard.check_instruction_text("Wait for review before merge.", pointer="topic")))
        self.assertEqual([], guard.check_instruction_text("Merge rules live in AGENTS.md.", pointer="topic"))

    def test_git_merge_commands_are_not_merge_governance(self):
        for text in (
            "Run git merge-base before diff.",
            "Use git merge-tree to inspect conflicts.",
            "Run git merge --no-ff feature locally.",
        ):
            with self.subTest(text=text):
                self.assertEqual([], guard.check_instruction_text(text, pointer="topic"))
        for text in ("Wait for review before merge.", "Merge only after ACCEPT."):
            with self.subTest(text=text):
                self.assertIn("missing-canonical-pointer", rules(guard.check_instruction_text(text, pointer="topic")))

    def test_historical_exclusions_are_kept(self):
        quoted = "<!-- director-doc-history BEGIN: 2026-09-08 owner quote -->\nIf they approve it, they should merge it as well.\n<!-- director-doc-history END -->"
        self.assertEqual([], guard.check_instruction_text(quoted))
        self.assertTrue(guard.check_instruction_text(quoted + "\n\nIf they approve it, they should merge it as well."))
        self.assertTrue(guard.check_instruction_text("<!-- director-doc-history BEGIN: -->\nold"))

    def test_named_surfaces_in_a_fixture_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "CLAUDE.md": "Be brief.",
                "GEMINI.md": "@AGENTS.md",
                ".cursor/rules/merge.mdc": "Merging requires two reviewers.",
                ".agents/skills/tool/SKILL.md": "Run git merge-base first.",
                "projects/demo/CLAUDE.md": "Dispatch eight OpenAI, five Claude assignments.",
                "docs/notes.md": "Merging requires two reviewers.",
                "AGENTS.md": "Merging requires two reviewers.",
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
                "src/AGENTS.md": "Merging requires two reviewers.",
                "src/nested/AGENTS.md": "Run git merge-base first.",
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]
            }
            for relative, text in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            errors = guard.check_instruction_surfaces(root)
            flagged = {error.split(": ", 1)[0] for error in errors}
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
            # Scoped AGENTS.md files are scanned; the root AGENTS.md is the
            # canonical home and belongs to check_tree, not this scan.
            self.assertEqual(
                {"CLAUDE.md", ".cursor/rules/merge.mdc", "projects/demo/CLAUDE.md", "src/AGENTS.md"},
                flagged,
            )
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]

    def test_named_surfaces_also_apply_the_legacy_authority_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claim = "Antigravity may not write project files."
            history = f"<!-- director-doc-history BEGIN: dated quotation -->\n{claim}\n<!-- director-doc-history END -->"
            files = {
                "GEMINI.md": "@AGENTS.md\n\n" + claim,
                ".cursor/rules/old.mdc": "The legacy Director remains the sole writer.",
                ".agents/skills/tool/SKILL.md": "Use 25/25/20/15/11/4.",
                "CLAUDE.md": "@AGENTS.md\n\n" + history,
            }
            for relative, text in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            errors = guard.check_instruction_surfaces(root)
            self.assertEqual(
                {
                    "GEMINI.md: antigravity-blanket-write-denial",
                    ".cursor/rules/old.mdc: legacy-sole-writer",
                    ".agents/skills/tool/SKILL.md: obsolete-allocation",
                },
                {error.split(":", 2)[0] + ":" + error.split(":", 2)[1] for error in errors},
            )
            # A malformed history marker is reported once, not once per rule set.
            (root / "CLAUDE.md").write_text("@AGENTS.md\n\n<!-- director-doc-history END -->", encoding="utf-8")
            self.assertEqual(1, sum("CLAUDE.md" in error for error in guard.check_instruction_surfaces(root)))

    def test_repository_scans_reject_symlinks_without_reading_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            outside = Path(directory) / "private.md"
            outside.write_text("Antigravity may not write project files.", encoding="utf-8")
            surface = root / ".agents/skills/tool/SKILL.md"
            document = root / "docs/notes.md"
            for link in (surface, document):
                link.parent.mkdir(parents=True, exist_ok=True)
                try:
                    link.symlink_to(outside)
                except OSError as error:
                    if getattr(error, "winerror", None) == 1314:
                        self.skipTest("symlink creation requires Windows Developer Mode or privilege")
                    raise
            for errors, name in (
                (guard.check_instruction_surfaces(root), ".agents/skills/tool/SKILL.md"),
                (guard.check_tree(root), "docs/notes.md"),
            ):
                self.assertIn(f"{name}: symlink or path outside repository", errors)
                self.assertFalse(any("antigravity-blanket-write-denial" in error for error in errors))

            # Glob prefixes and project wildcard directories must be checked before descent.
            outside_dir = Path(directory) / "outside_dir"
            outside_dir.mkdir()
            (outside_dir / "CLAUDE.md").write_text("Antigravity may not write project files.", encoding="utf-8")
            (outside_dir / "leak.md").write_text("Antigravity may not write project files.", encoding="utf-8")
            nested = Path(directory) / "nested_repo"
            nested.mkdir()
            for link in (nested / ".agents", nested / "docs", nested / "projects/demo"):
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside_dir, target_is_directory=True)
            surface_errors = guard.check_instruction_surfaces(nested)
            tree_errors = guard.check_tree(nested)
            self.assertIn(".agents: symlink or path outside repository", surface_errors)
            self.assertIn("projects/demo: symlink or path outside repository", surface_errors)
            self.assertIn("docs: symlink or path outside repository", tree_errors)
            self.assertFalse(any("antigravity-blanket-write-denial" in error for error in surface_errors + tree_errors))

    def test_local_agent_file_mode_is_explicit_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory) / "clean.md"
            clean.write_text("Follow AGENTS.md for merge rules.", encoding="utf-8")
            drift = Path(directory) / "drift.md"
            drift.write_text("Before merge: " + CHARTER_TABLE + "\n\nUse 25/25/20/15/11/4.", encoding="utf-8")
            missing = Path(directory) / "absent.md"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, guard.main(["--local-agent-file", str(clean)]))
            self.assertIn("1 explicit file", output.getvalue())
            found = guard.check_local_agent_files([drift, missing])
            self.assertTrue(any("copied-allocation-shares" in error for error in found))
            self.assertTrue(any("missing-canonical-pointer" in error for error in found))
            self.assertTrue(any("obsolete-allocation" in error for error in found))
            self.assertTrue(any("unreadable local agent file" in error for error in found))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, guard.main(["--local-agent-file", str(drift)]))


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 11:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:46 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 19:58 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]
