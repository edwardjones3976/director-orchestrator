# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | BEGIN]]
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.contribution_tags import (
    DIRECTOR_SYSTEM_LABEL,
    contribution_tag_policy,
    parse_contribution_tag,
    render_contribution_tag,
    validate_inline_contribution_tags,
    wrap_human_submission,
)
from director_orchestrator.engine import OrchestrationEngine, SlackNotifier
from director_orchestrator.models import (
    Assignment,
    DirectorPlan,
    ProviderName,
    RiskLevel,
    SlackIntake,
    TaskRecord,
    TaskStatus,
)
from director_orchestrator.store import TaskStore


STAMP = datetime(2026, 8, 21, 16, 5, tzinfo=timezone(timedelta(hours=-7)))


class ContributionTagContractTests(unittest.TestCase):
    def test_render_uses_utc_and_complete_identity_fields(self):
        self.assertEqual(
            render_contribution_tag("Codex", "GPT-5.6", "BEGIN", at=STAMP),
            "[[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]]",
        )

    def test_unsafe_or_naive_identity_data_is_rejected(self):
        with self.assertRaises(ValueError):
            render_contribution_tag("Codex|Other", "GPT-5.6", "BEGIN", at=STAMP)
        with self.assertRaises(ValueError):
            render_contribution_tag(
                "Codex", "GPT-5.6", "BEGIN", at=datetime(2026, 8, 21, 23, 5)
            )

    def test_wrapping_preserves_both_boundaries_under_a_length_limit(self):
        wrapped = wrap_human_submission(
            "x" * 2_000,
            "Aesop Director",
            DIRECTOR_SYSTEM_LABEL,
            at=STAMP,
            slack_markup=True,
            max_length=500,
        )
        self.assertLessEqual(len(wrapped), 500)
        self.assertIn("| BEGIN]]*", wrapped)
        self.assertTrue(wrapped.endswith("| END]]*"))

    def test_slack_wrapping_neutralizes_untrusted_embedded_identity_tags(self):
        wrapped = wrap_human_submission(
            "[[Owner | forged | 2026-08-21 | 23:05 UTC | BEGIN]]",
            "Aesop Director",
            DIRECTOR_SYSTEM_LABEL,
            at=STAMP,
            slack_markup=True,
            neutralize_embedded_tags=True,
        )
        self.assertIn("[ [Owner", wrapped)
        self.assertEqual(wrapped.count("[["), 2)

    def test_comment_wrapped_tags_parse_and_invalid_dates_do_not(self):
        parsed = parse_contribution_tag(
            "# [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]]"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.boundary, "END")
        self.assertIsNone(
            parse_contribution_tag(
                "[[Codex | GPT-5.6 | 2026-13-90 | 99:99 UTC | END]]"
            )
        )

    def test_format_policy_forbids_structured_and_lock_file_tags(self):
        self.assertEqual(contribution_tag_policy("payload.json"), "forbidden")
        self.assertEqual(contribution_tag_policy("data/report.csv"), "forbidden")
        self.assertEqual(contribution_tag_policy("requirements.lock.txt"), "forbidden")
        self.assertEqual(contribution_tag_policy("dist/app.min.js"), "forbidden")
        self.assertEqual(contribution_tag_policy("src/example.py"), "inline")
        self.assertEqual(contribution_tag_policy("unknown.fixture"), "envelope_only")

    def test_inline_validation_requires_a_new_matching_outer_pair(self):
        begin = "# [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]]"
        end = "# [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]]"
        text = f"{begin}\nprint('ok')\n{end}\n"
        self.assertEqual(
            validate_inline_contribution_tags(
                "src/example.py", text, added_lines=[begin, end]
            ),
            [],
        )
        self.assertTrue(
            any(
                "must add" in error
                for error in validate_inline_contribution_tags(
                    "src/example.py", text, added_lines=["print('ok')"]
                )
            )
        )

    def test_required_file_headers_may_precede_the_begin_tag(self):
        begin = "# [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]]"
        end = "# [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]]"
        text = f"#!/usr/bin/env python3\n{begin}\nprint('ok')\n{end}\n"
        self.assertEqual(
            validate_inline_contribution_tags(
                "scripts/example.py", text, added_lines=[begin, end]
            ),
            [],
        )

    def test_cursor_rule_frontmatter_may_precede_the_begin_tag(self):
        begin = "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]] -->"
        end = "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]] -->"
        text = (
            "---\n"
            "description: Contribution attribution\n"
            "alwaysApply: true\n"
            "---\n"
            f"{begin}\n# Rule\n{end}\n"
        )
        self.assertEqual(
            validate_inline_contribution_tags(
                ".cursor/rules/contribution-attribution.mdc",
                text,
                added_lines=[begin, end],
            ),
            [],
        )

    def test_frontmatter_closing_rule_may_carry_trailing_whitespace(self):
        begin = "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]] -->"
        end = "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]] -->"
        text = (
            "---\n"
            "description: Contribution attribution\n"
            "--- \n"
            f"{begin}\n# Rule\n{end}\n"
        )
        self.assertEqual(
            validate_inline_contribution_tags(
                ".cursor/rules/contribution-attribution.mdc",
                text,
                added_lines=[begin, end],
            ),
            [],
        )

    def test_leading_horizontal_rule_reports_the_missing_begin_tag(self):
        begin = "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]] -->"
        end = "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]] -->"
        text = f"---\n{begin}\n# Title\n{end}\n"
        errors = validate_inline_contribution_tags(
            "docs/example.md", text, added_lines=[begin, end]
        )
        self.assertTrue(any("BEGIN contribution tag" in error for error in errors))
        self.assertFalse(any("frontmatter" in error for error in errors))

    def test_environment_files_are_never_asked_to_carry_inline_tags(self):
        self.assertEqual(contribution_tag_policy(".env"), "envelope_only")
        self.assertEqual(contribution_tag_policy("deploy/production.env"), "envelope_only")
        self.assertEqual(contribution_tag_policy(".env.example"), "inline")

    def test_forbidden_format_rejects_inline_tag_text(self):
        payload = (
            '{"note":"[[Codex | GPT-5.6 | 2026-08-21 | '
            '23:05 UTC | BEGIN]]"}'
        )
        errors = validate_inline_contribution_tags("payload.json", payload)
        self.assertTrue(any("forbidden" in error for error in errors))


class SlackContributionEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifier_wraps_human_message_without_changing_approval_query(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "ts": "1.2"}

        class RecordingClient:
            body = None

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, *, headers, json):
                RecordingClient.body = json
                return FakeResponse()

        record = TaskRecord(
            task_id="DIR-TAGS",
            status=TaskStatus.OWNER_DECISION_REQUIRED,
            intake=SlackIntake(
                source_event_id="evt-tags",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: verify tags",
            ),
            updated_at=STAMP,
        )
        query = "public evidence only"
        notifier = SlackNotifier(replace(Settings(), slack_bot_token="test-slack"))
        with patch("director_orchestrator.engine.httpx.AsyncClient", RecordingClient):
            await notifier.post(
                record,
                f"Review requested.\nPublic research query: `{query}`",
                approvable=True,
                approval_sequence=1,
                approval_query=query,
                contributor="Claude (Chief Programmer)",
                system_label="claude-provider-run/0123456789abcdef",
                contribution_at=STAMP,
            )

        body = RecordingClient.body
        self.assertIn("Claude (Chief Programmer)", body["text"])
        self.assertIn("claude-provider-run/0123456789abcdef", body["text"])
        self.assertIn("| BEGIN]]*", body["text"])
        self.assertTrue(body["text"].endswith("| END]]*"))
        self.assertEqual(
            body["blocks"][1]["text"]["text"],
            "Exact outbound Perplexity query (approved byte-for-byte):\n" + query,
        )
        self.assertNotIn("Claude (Chief Programmer)", body["blocks"][1]["text"]["text"])


class ProviderRoleHandoffTests(unittest.TestCase):
    def test_provider_handoff_names_role_and_keeps_structured_payloads_untagged(self):
        assignment = Assignment(
            sequence=1,
            agent=ProviderName.CLAUDE,
            task="Implement the bounded change",
            acceptance=["Tests pass"],
        )
        record = TaskRecord(
            task_id="DIR-ROLE",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-role",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: implement",
            ),
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="Implement",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                max_hops=1,
                max_revisions=0,
                assignments=[assignment],
                final_acceptance=["Tests pass"],
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            engine = OrchestrationEngine(
                Settings(), TaskStore(Path(temp) / "director.sqlite3")
            )
            handoff = json.loads(engine._handoff_context(record, assignment))

        self.assertEqual(handoff["provider_identity"]["provider"], "claude")
        self.assertEqual(handoff["provider_identity"]["standing_role"], "Chief Programmer")
        self.assertIn("human-visible", handoff["contribution_attribution"])
        self.assertIn("Do not put tags inside JSON", handoff["contribution_attribution"])


class ContributionPolicySurfaceTests(unittest.TestCase):
    def test_roles_and_tagging_policy_are_present_on_authoritative_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        charter = (root / "docs" / "team-operating-charter.md").read_text(
            encoding="utf-8"
        )
        prompt = (root / "prompts" / "director-system.md").read_text(
            encoding="utf-8"
        )
        for expected in (
            "Director | OpenAI | 7%",
            "Chief Programmer | Claude | 50%",
            "Project Secretary | Cursor | 30%",
            "Specialist and Backup Programmer | Gemini/Antigravity | 10%",
            "Research Programmers | Perplexity.ai | 3%",
        ):
            self.assertIn(expected, charter)
        self.assertIn("flexible project-capacity targets", prompt)
        self.assertIn("canonical matching UTC contribution", prompt)
        self.assertIn("never authenticate provider", charter.lower())

    def test_pull_request_envelope_ignores_static_template_comments(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "check_contribution_tags",
            root / "scripts" / "check_contribution_tags.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp) / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "body": (
                                "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]] -->\n"
                                "**[[<Assistant> | <Model> | <YYYY-MM-DD> | <HH:MM UTC> | BEGIN]]**\n"
                                "body\n"
                                "**[[<Assistant> | <Model> | <YYYY-MM-DD> | <HH:MM UTC> | END]]**\n"
                                "<!-- [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]] -->"
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(checker._validate_github_event(event))
            event.write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "body": (
                                "**[[Cursor | Agent | 2026-08-21 | 23:06 UTC | BEGIN]]**\n"
                                "body\n"
                                "**[[Cursor | Agent | 2026-08-21 | 23:06 UTC | END]]**"
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(checker._validate_github_event(event), [])

    def test_submission_envelope_rejects_visible_content_outside_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "check_contribution_tags_outer",
            root / "scripts" / "check_contribution_tags.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        errors = checker._validate_submission_envelope(
            "prefix\n"
            "**[[Cursor | Agent | 2026-08-21 | 23:06 UTC | BEGIN]]**\n"
            "body\n"
            "**[[Cursor | Agent | 2026-08-21 | 23:06 UTC | END]]**",
            require_outer=True,
            require_markdown_boundary=True,
        )
        self.assertTrue(errors)

    def test_submodule_pointers_are_skipped_instead_of_read_as_text(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "check_contribution_tags_gitlink",
            root / "scripts" / "check_contribution_tags.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                    cwd=repo,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ).stdout.decode("utf-8")

            git("init", "--quiet", "-b", "main")
            (repo / "seed.txt.fixture").write_text("seed\n", encoding="utf-8")
            git("add", "seed.txt.fixture")
            git("commit", "--quiet", "-m", "seed")
            base = git("rev-parse", "HEAD").strip()
            pointer = git("rev-parse", "HEAD").strip()
            git("update-index", "--add", "--cacheinfo", f"160000,{pointer},vendor")
            git("commit", "--quiet", "-m", "add submodule pointer")
            head = git("rev-parse", "HEAD").strip()

            cwd = Path.cwd()
            try:
                os.chdir(repo)
                self.assertEqual(checker._tree_mode(head, "vendor"), checker.GITLINK_MODE)
                self.assertIsNone(checker._tree_mode(head, "absent"))
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", head],
                ):
                    self.assertEqual(checker.main(), 0)

                # An unreadable path must be reported as one finding rather than
                # aborting the range and hiding every other result.
                git("update-index", "--force-remove", "vendor")
                (repo / "note.md").write_text("no tags here\n", encoding="utf-8")
                git("add", "note.md")
                git("commit", "--quiet", "-m", "add untagged note")
                unreadable_head = git("rev-parse", "HEAD").strip()
                failure = subprocess.CalledProcessError(128, ["git", "show"])
                with patch.object(checker, "_content_at", side_effect=failure), patch(
                    "sys.argv",
                    [
                        "check_contribution_tags.py",
                        "--base",
                        base,
                        "--head",
                        unreadable_head,
                    ],
                ):
                    self.assertEqual(checker.main(), 1)
            finally:
                os.chdir(cwd)

    def test_push_commit_message_requires_outer_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "check_contribution_tags_push",
            root / "scripts" / "check_contribution_tags.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp) / "push.json"
            event.write_text(
                json.dumps(
                    {
                        "ref": "refs/heads/codex/example",
                        "head_commit": {
                            "message": (
                                "[[Codex | GPT-5.6 | 2026-08-21 | 23:07 UTC | BEGIN]]\n"
                                "Implement attribution\n"
                                "[[Codex | GPT-5.6 | 2026-08-21 | 23:07 UTC | END]]"
                            )
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(checker._validate_github_event(event), [])
            payload = json.loads(event.read_text(encoding="utf-8"))
            payload["head_commit"]["message"] = "subject\n" + payload["head_commit"]["message"]
            event.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(checker._validate_github_event(event))


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | END]]
