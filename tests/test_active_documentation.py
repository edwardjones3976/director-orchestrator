# [[OpenAI Codex | GPT-6 | 2026-09-27 | 22:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 17:41 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:10 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 21:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:08 UTC | BEGIN]]
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("doc_guard", ROOT / "scripts/check_active_documentation.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class ActiveDocumentationTests(unittest.TestCase):
    def test_bootstrap_and_pr246_docs_do_not_assign_claude_standing_leadership(self):
        bootstrap = ROOT / "docs/templates/project-bootstrap"
        surfaces = [
            ROOT / "docs/director-user-guide.md",
            ROOT / "docs/langgraph-primary-operations.md",
            ROOT / "docs/project-bootstrap-protocol.md",
            ROOT / "prompts/codex-engineering.md",
            *sorted(bootstrap.glob("*")),
        ]
        for path in surfaces:
            if not path.is_file():
                continue
            text = " ".join(path.read_text(encoding="utf-8").split()).lower()
            with self.subTest(surface=path.name):
                for stale in (
                    "claude is the director", "claude is the acting director",
                    "claude holds all three functions", "claude owns moderator",
                    "acting claude director",
                    "acting project director | claude",
                    "single existing claude advisory vote", "claude's one vote",
                ):
                    self.assertNotIn(stale, text)
                if path != ROOT / "prompts/codex-engineering.md":
                    self.assertNotIn("claude as second in command", text)
        for name in ("intake.md", "project-profile.yaml"):
            with self.subTest(template=name):
                text = (bootstrap / name).read_text(encoding="utf-8")
                self.assertIn("project-bootstrap-protocol.md#provider-fit-menu", text)
                if name == "intake.md":
                    self.assertIn("<ChatGPT/Codex Director or owner-appointed project Director>", text)
        profile = (bootstrap / "project-profile.yaml").read_text(encoding="utf-8")
        director = profile.split("\n  director:\n", 1)[1].split("\n  lanes:", 1)[0]
        self.assertNotIn('provider: "claude"', director)
        self.assertEqual(
            (
                "<openai | claude | canonical provider key from provider-roles-contract.yaml>",
                "<owner appointment reference (date/record), or none>",
            ),
            tuple(yaml.safe_load(profile)["team"]["director"][key] for key in ("provider", "appointment")),
        )

    def test_codex_director_fallback_requires_explicit_owner_appointment(self):
        text = " ".join((ROOT / "prompts/codex-engineering.md").read_text(
            encoding="utf-8"
        ).split())
        for clause in (
            "only under the owner's explicit appointment of Claude as second in command",
            "owner directive, 2026-09-22",
            "only while the ChatGPT/Codex Director is unavailable",
            "This appointment is current and scoped",
            "in effect until the owner revokes or replaces it",
            "This later owner appointment takes precedence, for that scope only, over the general no-standing-assignment statement in docs/project-leadership.md",
            "never removes deterministic gates or permits self-approval or self-merge",
            "Every lane routing still passes capability, authority, availability, budget, repository, and independence checks",
            "Without that appointment, decisions wait for the ChatGPT/Codex Director or the owner",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, text)

        self.assertIn(
            "Return scope and sequencing decisions to the acting Director: the "
            "ChatGPT/Codex Director, or, only while the owner's current scoped appointment "
            "of Claude as second in command is in effect and the ChatGPT/Codex Director "
            "is unavailable, Claude; otherwise the owner.",
            text,
        )

    def test_provider_fit_menu_covers_canonical_providers(self):
        contract = yaml.safe_load((ROOT / "docs/templates/project-bootstrap/provider-roles-contract.yaml").read_text(
            encoding="utf-8"
        ))
        text = (ROOT / "docs/project-bootstrap-protocol.md").read_text(encoding="utf-8")
        menu = text.split("## Provider-fit menu\n", 1)[1].split("\n## ", 1)[0]
        providers = {
            alias.strip().lower().replace(" ", "-")
            for line in menu.splitlines() if line.startswith("| ")
            for alias in line.split("|")[1].split("/")
        }
        # Allocation entries do not create provider contracts; Lite is a separate observation row.
        canonical = {
            key.replace("_", "-")
            for key in set(contract["providers"]) | set(contract["supplemental_reviewers"])
        }
        self.assertFalse(canonical - providers, f"Missing provider-fit rows: {sorted(canonical - providers)}")

    def test_owner_local_adoption_is_documented_as_owner_signed(self):
        text = " ".join((ROOT / "docs/langgraph-primary-operations.md").read_text(
            encoding="utf-8"
        ).split())
        self.assertIn("owner-signed, not provider-signed `owner-local-evidence-v1`", text)
        self.assertNotIn("unsigned `owner-local-evidence-v1`", text)
        self.assertIn("`load_pinned_owner_local_adoption` rejects unsigned documents", text)
        self.assertIn("`owner_adoption` attestation", text)

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
            "The owner placed no limits on either channel.",
            "Maintenance is assistance, not authorship.",
            "maintenance assistance: OpenAI Codex",
            "It applies to every Director:",
            "all maintenance from 2026-09-26 onward, including work already in flight",
            "It adds no authority and removes no gate.",
            "Change this to all work and immediately",
        ):
            self.assertIn(clause, section)
        self.assertNotIn("new work only", section.lower().replace('"new work only"', ""))
        self.assertNotIn("co-author's share", section)
        self.assertNotIn("status updates and owner decisions only", section)
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
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:10 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 17:41 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 18:03 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:59 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 22:17 UTC | END]]
