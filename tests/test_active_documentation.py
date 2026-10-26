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

from director_orchestrator.documentation import read_document

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("doc_guard", ROOT / "scripts/check_active_documentation.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class ActiveDocumentationTests(unittest.TestCase):
    def test_current_leadership_separates_openai_roles_and_sole_foreman(self):
        leadership = " ".join(read_document(ROOT / "docs/project-leadership.html").replace("**", "").split())
        staff = " ".join(read_document(ROOT / "docs/team-staff-and-briefings.html").replace("**", "").split())
        for clause in (
            "OpenAI retains the overall lead through distinct Director and Orchestrator roles",
            "Claude is the full-time and sole Foreman; Cursor is Warden",
            "Foreman replacement requires an explicit owner appointment",
            "other projects retain their appointments",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, leadership)
        for stale in (
            "immediate ChatGPT/Codex Director authority for priorities",
            "immediate ChatGPT/Codex appointment supersedes historical prose assigning project leadership to Claude",
        ):
            self.assertNotIn(stale, leadership)
        self.assertIn("| 2 | Claude | Full-time sole Foreman", staff)
        self.assertIn("Overall lead through distinct Director and Orchestrator roles", staff)
        self.assertIn("| 3 | Cursor | Warden", staff)
        self.assertNotIn("Director and site foreman; owns outcomes, priorities, routing", staff)
        self.assertNotIn("| 2 | Claude | Deputy; performs the Director's duties while Codex is unavailable", staff)

    def test_mandatory_research_keeps_inter_provider_scope(self):
        leadership = " ".join(read_document(ROOT / "docs/project-leadership.html").split())
        self.assertIn(
            "Technical/development questions between providers require Perplexity or Firecrawl research before an answer",
            leadership,
        )
        self.assertIn("If those routes are unavailable, return the explicit research gap", leadership)
        self.assertIn("Coordination questions are answered directly", leadership)
        self.assertIn("conditional research policy", leadership)
        self.assertNotIn("Technical questions still require Perplexity or Firecrawl research", leadership)

    def test_bootstrap_and_pr246_docs_do_not_assign_claude_standing_leadership(self):
        bootstrap = ROOT / "docs/templates/project-bootstrap"
        surfaces = [
            ROOT / "docs/director-user-guide.html",
            ROOT / "docs/langgraph-primary-operations.html",
            ROOT / "docs/project-bootstrap-protocol.html",
            ROOT / "prompts/codex-engineering.md",
            *sorted(bootstrap.glob("*")),
        ]
        for path in surfaces:
            if not path.is_file():
                continue
            text = " ".join(read_document(path).split()).lower()
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
        for name in ("intake.html", "project-profile.yaml"):
            with self.subTest(template=name):
                text = read_document(bootstrap / name)
                self.assertIn("project-bootstrap-protocol.html#provider-fit-menu", text)
                if name == "intake.html":
                    self.assertIn("<ChatGPT/Codex Director or owner-appointed project Director>", text)
        profile = read_document(bootstrap / "project-profile.yaml")
        director = profile.split("\n  director:\n", 1)[1].split("\n  lanes:", 1)[0]
        self.assertNotIn('provider: "claude"', director)
        self.assertEqual(
            (
                "<openai | claude | canonical provider key from provider-roles-contract.yaml>",
                "<owner appointment reference (date/record), or none>",
            ),
            tuple(yaml.safe_load(profile)["team"]["director"][key] for key in ("provider", "appointment")),
        )

    def test_codex_prompt_routes_to_four_roles_without_automatic_takeover(self):
        text = " ".join(read_document(ROOT / "prompts/codex-engineering.md").split())
        for clause in (
            "OpenAI retains overall leadership through two distinct roles",
            "OpenAI Director owns direction, priorities and accountability",
            "OpenAI Orchestrator coordinates approved execution",
            "Claude is the full-time and sole Foreman",
            "Cursor is Warden",
            "Other projects retain their own appointments",
            "replacement of Claude as Foreman requires a new owner appointment",
            "never remove deterministic gates or permit self-approval or self-merge",
            "Every lane routing still passes capability, authority, availability, budget, repository, and independence checks",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, text)

        self.assertIn(
            "Return direction and priority decisions to the OpenAI Director, "
            "approved-work coordination to the OpenAI Orchestrator, work-order and repair "
            "decisions to Claude, and Warden evidence to Cursor.",
            text,
        )
        self.assertNotIn("The ChatGPT/Codex Director holds project coordination", text)
        self.assertNotIn("Return scope and sequencing decisions to the acting Director: the ChatGPT/Codex Director", text)

    def test_current_role_guide_is_linked_by_participant_instructions(self):
        guide = ROOT / "docs/leadership-owner-20261026.html"
        source = guide.read_text(encoding="utf-8")
        for anchor in ("openai-director", "openai-orchestrator", "claude-foreman", "cursor-warden", "cursor-pr-issues", "cursor-merge-exception"):
            self.assertIn(f'id="{anchor}"', source)
        for name in ("AGENTS.md", "CLAUDE.md", ".cursor/rules/director-leadership.mdc",
                     "prompts/codex-engineering.md", "docs/project-leadership.html",
                     "docs/team-operating-charter.html", "docs/foreman-repair-work-orders.html",
                     "docs/site-coordination.html", "docs/team-staff-and-briefings.html"):
            with self.subTest(surface=name):
                self.assertIn("leadership-owner-20261026.html", (ROOT / name).read_text(encoding="utf-8"))

    def test_cursor_queue_ownership_and_coauthor_merge_exception(self):
        guide = " ".join(read_document(ROOT / "docs/leadership-owner-20261026.html").replace("**", "").split())
        for clause in (
            "all Director-Orchestrator PRs and issues",
            "Issues are not merged",
            "No contribution-percentage cap applies to Cursor",
            "including above 52%",
            "genuine recorded contribution to the candidate, however minor",
            "Cursor must not merge work entirely authored by Cursor",
            "for Cursor only",
            "It changes merge eligibility, not independent-review eligibility",
            "Unknown or disputed authorship must be reconciled before merge",
            "do not bypass it or claim this instruction changed the runtime",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, guide)
        for name in ("AGENTS.md", "CLAUDE.md", ".cursor/rules/director-leadership.mdc",
                     "docs/project-leadership.html", "docs/site-coordination.html",
                     "docs/foreman-repair-work-orders.html", "docs/team-operating-charter.html",
                     "docs/contribution-attribution-policy.html", "docs/human-out-of-the-loop-operation.html"):
            with self.subTest(surface=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertRegex(source, r"leadership-owner-20261026\.html#cursor-(?:pr-issues|merge-exception)")

    def test_warden_standalone_role_page_is_complete(self):
        page = ROOT / "docs/roles/warden.html"
        source = page.read_text(encoding="utf-8")
        self.assertIn('href="../assets/role-pages.css"', source)
        self.assertIn('class="role-page role-warden"', source)
        self.assertNotIn("documentation.css", source)
        text = " ".join(read_document(page).replace("**", "").split())
        for clause in (
            "Held by Cursor",
            "objective drift",
            "repeated unproductive work",
            "suspicious delegation",
            "inaccurate completion claims",
            "prompt-injection or manipulation",
            "interference with monitoring or enforcement",
            "no live WARDEN service",
            "all Director-Orchestrator PRs and issues",
            "Issues are not merged",
            "No contribution-percentage cap applies to Cursor",
            "including above 52%",
            "genuine recorded contribution to the candidate, however minor",
            "Cursor must not merge work entirely authored by Cursor",
            "for Cursor only",
            "It changes merge eligibility, not independent-review eligibility",
            "Unknown or disputed authorship must be reconciled before merge",
            "do not bypass it or claim this instruction changed the runtime",
            "dual_control.awaits_corroboration",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, text)
        library = (ROOT / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn("roles/warden.html", library)
        self.assertIn("roles/warden.html", (ROOT / "docs/leadership-owner-20261026.html").read_text(encoding="utf-8"))
        self.assertIn("roles/warden.html", (ROOT / "docs/project-leadership.html").read_text(encoding="utf-8"))

    def test_provider_fit_menu_covers_canonical_providers(self):
        contract = yaml.safe_load(read_document(ROOT / "docs/templates/project-bootstrap/provider-roles-contract.yaml"))
        text = read_document(ROOT / "docs/project-bootstrap-protocol.html")
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
        text = " ".join(read_document(ROOT / "docs/langgraph-primary-operations.html").split())
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

    def test_html_inline_markup_cannot_hide_obsolete_authority(self):
        source = '<main><p>The <strong>legacy Director</strong> remains the <a href="x.html">sole writer</a>.</p></main>'
        self.assertTrue(guard.check_text(source, html=True))
        history = '<main><!-- director-doc-history BEGIN: superseded claim --><p>The legacy Director remains the sole writer.</p><!-- director-doc-history END --><p>Current facts required.</p></main>'
        self.assertEqual([], guard.check_text(history, html=True))
        self.assertTrue(guard.check_text(history.replace('Current facts required.', 'Use 25/25/20/15/11/4.'), html=True))

    def test_visible_table_caption_cannot_hide_obsolete_authority(self):
        source = '<main><table><caption>The <strong>legacy Director</strong> remains the sole writer.</caption><tr><th>Check</th><td>Pass</td></tr></table></main>'
        self.assertTrue(guard.check_text(source, html=True))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'README.html').write_text(source, encoding='utf-8')
            self.assertTrue(guard.check_tree(root))

    def test_canonical_html_is_scanned_and_missing_main_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, source in (
                ("README.html", "<main><p>Use 25/25/20/15/11/4.</p></main>"),
                ("docs/current.html", "<main><p>Antigravity may not <em>write</em> project files.</p></main>"),
                ("docs/archive/old.html", "<main><p>Use 25/25/20/15/11/4.</p></main>"),
                ("docs/broken.html", "<nav>All passes</nav>"),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            errors = guard.check_tree(root)
            self.assertEqual({error.split(":", 1)[0] for error in errors}, {"README.html", "docs/current.html", "docs/broken.html"})

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
            for relative in ("docs/archive/old.md", "docs/archive/old.yaml", "docs/archive-neighbor/current.md", "docs/audit-2020-01-01.md", "prompts/director.md", "docs/templates/nested/profile.yaml", "prompts/nested/contract.yml", "README.html"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("25/25/20/15/11/4", encoding="utf-8")
            errors = guard.check_tree(root)
            self.assertEqual(6, len(errors))
            self.assertFalse(any("docs/archive/old.md" in error for error in errors))

    def test_director_maintenance_routing_is_canonical_and_referenced(self):
        charter = read_document(ROOT / "docs/team-operating-charter.html")
        section = " ".join(charter.split("## Director maintenance routing", 1)[1].split("\n## ", 1)[0].split())
        for clause in (
            "Maintenance goes to OpenAI Codex.",
            "The owner placed no limits on either channel.",
            "Maintenance is assistance, not authorship.",
            "Existing Git and authenticated task/run records establish the actual author and maintenance assistance",
            "no prescribed author header is required",
            "Do not promote assistance into co-authorship",
            "It applies to every Director:",
            "all maintenance from 2026-09-26 onward, including work already in flight",
            "It adds no authority and removes no gate.",
            "Change this to all work and immediately",
        ):
            self.assertIn(clause, section)
        self.assertNotIn("new work only", section.lower().replace('"new work only"', ""))
        self.assertNotIn("co-author's share", section)
        self.assertNotIn('Records say "Sole author:', section)
        self.assertNotIn("status updates and owner decisions only", section)
        for relative in (
            "AGENTS.md", "CLAUDE.md", "README.html", "docs/project-leadership.html",
            "docs/director-user-guide.html", "docs/project-bootstrap-protocol.html",
            "projects/homemade-llm/README.md",
        ):
            with self.subTest(surface=relative):
                text = read_document(ROOT / relative)
                # The separate project is explicitly outside this migration.
                suffix = "md" if relative.startswith("projects/homemade-llm/") else "html"
                self.assertIn(f"team-operating-charter.{suffix}#director-maintenance-routing", text)


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
