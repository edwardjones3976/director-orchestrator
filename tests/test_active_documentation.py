# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | BEGIN]]
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("doc_guard", ROOT / "scripts/check_active_documentation.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class ActiveDocumentationTests(unittest.TestCase):
    def test_current_active_documents(self):
        self.assertEqual([], guard.check_tree(ROOT))

    def test_obsolete_authority_claims_fail(self):
        for claim in (
            "Target: 25/25/20/15/11/4.",
            "The legacy Director remains the\nsole writer.",
            "The **legacy Director** remains the **sole writer**.",
            "Antigravity may not write project files.",
            "Microsoft 365 Copilot is an active provider.",
            "Grok is a full-spectrum Cursor-family peer.",
        ):
            with self.subTest(claim=claim):
                self.assertTrue(guard.check_text(claim))

    def test_current_evidence_qualified_language_is_allowed(self):
        self.assertEqual([], guard.check_text(
            "Verify the sole writer from current health. Antigravity can edit "
            "authorized files. Microsoft 365 Copilot is retired. Engineering "
            "targets are 40/25/15/10/10. Grok Bot is distinct from Cursor."
        ))

    def test_explicit_history_does_not_exempt_surrounding_instructions(self):
        quote = "<!-- director-doc-history BEGIN: superseded allocation quotation -->\n25/25/20/15/11/4\n<!-- director-doc-history END -->"
        self.assertEqual([], guard.check_text(quote))
        self.assertTrue(guard.check_text(quote + "\n\nUse 25/25/20/15/11/4 now."))

    def test_malformed_or_unbounded_history_fails(self):
        for text in (
            "<!-- director-doc-history BEGIN: -->\nold",
            "<!-- director-doc-history BEGIN: dated quotation -->\nold",
            "<!-- director-doc-history END -->",
            "<!-- director-doc-history BEGIN: dated quotation -->\n<!-- director-doc-history END -->",
            "<!-- director-doc-history BEGIN: first -->\n<!-- director-doc-history BEGIN: nested -->\nold\n<!-- director-doc-history END -->",
        ):
            with self.subTest(text=text):
                self.assertTrue(guard.check_text(text))

    def test_archive_exclusion_is_exact_and_dated_docs_are_not_exempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("docs/archive/old.md", "docs/archive/old.yaml", "docs/archive-neighbor/current.md", "docs/audit-2020-01-01.md", "prompts/director.md", "docs/templates/nested/profile.yaml", "prompts/nested/contract.yml", "README.md"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("25/25/20/15/11/4", encoding="utf-8")
            errors = guard.check_tree(root)
            self.assertEqual(6, len(errors))
            self.assertFalse(any("docs/archive/old.md" in error for error in errors))

    def test_director_maintenance_routing_is_canonical_and_referenced(self):
        charter = (ROOT / "docs/team-operating-charter.md").read_text(encoding="utf-8")
        section = " ".join(charter.split("## Director maintenance routing", 1)[1].split("\n## ", 1)[0].split())
        for clause in (
            "Maintenance goes to OpenAI Codex.",
            "Never route it through the ChatGPT operator chat",
            "Cursor by default, Grok when funded",
            "It applies to every Director:",
            "all maintenance from 2026-09-26 onward, including work already in flight",
            "It adds no authority and removes no gate.",
            "Change this to all work and immediately",
        ):
            self.assertIn(clause, section)
        self.assertNotIn("new work only", section.lower().replace('"new work only"', ""))
        for relative in (
            "AGENTS.md", "CLAUDE.md", "README.md", "docs/project-leadership.md",
            "docs/director-user-guide.md", "docs/project-bootstrap-protocol.md",
            "projects/homemade-llm/README.md",
        ):
            with self.subTest(surface=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("team-operating-charter.md#director-maintenance-routing", text)


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:37 UTC | END]]
