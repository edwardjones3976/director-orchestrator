# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:49 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:23 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:44 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:14 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:08 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:05 UTC | BEGIN]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-22 | 14:50 UTC | BEGIN]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | BEGIN]]
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
    TAG_PATTERN,
    contribution_tag_policy,
    iter_contribution_tags,
    neutralize_contribution_tags,
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
        self.assertIsNone(
            parse_contribution_tag(
                "[[     | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]]"
            )
        )
        self.assertIsNone(
            parse_contribution_tag(
                "[[Codex |      | 2026-08-21 | 23:05 UTC | END]]"
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
        inner_begin = "# [[Cursor | Agent | 2026-08-22 | 14:50 UTC | BEGIN]]"
        inner_end = "# [[Cursor | Agent | 2026-08-22 | 14:50 UTC | END]]"
        nested = f"{begin}\n{inner_begin}\nprint('ok')\n{inner_end}\n{end}\n"
        self.assertTrue(
            any(
                "outer BEGIN/END pair" in error
                for error in validate_inline_contribution_tags(
                    "src/example.py", nested, added_lines=[inner_begin, inner_end]
                )
            )
        )
        revised = f"{inner_begin}\n{begin}\nprint('ok')\n{end}\n{inner_end}\n"
        self.assertEqual(
            validate_inline_contribution_tags(
                "src/example.py", revised, added_lines=[inner_begin, inner_end]
            ),
            [],
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

        query = "public evidence only"
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
            plan=DirectorPlan(
                project="demo",
                normalized_outcome="verify tags",
                risk_level=RiskLevel.LOW,
                approval_required=False,
                approval_reasons=[],
                max_hops=1,
                max_revisions=0,
                assignments=[
                    Assignment(
                        sequence=1,
                        agent=ProviderName.PERPLEXITY,
                        task="Collect public evidence",
                        acceptance=["Return cited public evidence"],
                        public_research_query=query,
                        requires_approval=True,
                    )
                ],
                final_acceptance=["Evidence is cited"],
            ),
            updated_at=STAMP,
        )
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

    async def test_notifier_keeps_wrapped_collaboration_text_within_slack_budget(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "ts": "1.3"}

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
            task_id="DIR-TAGS-LONG",
            status=TaskStatus.RUNNING,
            intake=SlackIntake(
                source_event_id="evt-tags-long",
                channel_id="C1",
                thread_ts="1.1",
                user_id="U1",
                text="DIRECTOR Project: demo Outcome: verify long slack tags",
            ),
            updated_at=STAMP,
        )
        notifier = SlackNotifier(replace(Settings(), slack_bot_token="test-slack"))
        with patch("director_orchestrator.engine.httpx.AsyncClient", RecordingClient):
            await notifier.post(
                record,
                "x" * 3_900,
                contributor="claude (Chief Programmer)",
                system_label="claude-provider-run/0123456789abcdef",
                contribution_at=STAMP,
            )

        body = RecordingClient.body
        emitted = body["text"]
        self.assertLessEqual(len(emitted), 3_900)
        self.assertTrue(emitted.startswith("*[["))
        self.assertIn("| BEGIN]]*", emitted)
        self.assertTrue(emitted.endswith("| END]]*"))
        begin_idx = emitted.find("| BEGIN]]*")
        end_idx = emitted.rfind("| END]]*")
        self.assertGreater(end_idx, begin_idx)
        inner = emitted.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
        self.assertTrue(inner)
        self.assertTrue(set(inner) <= {"x"})
        self.assertEqual(inner, "x" * len(inner))
        self.assertLess(len(inner), 3_900)


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
            "Chief Programmer | Claude | 25% | 36%",
            "Project Secretary | Cursor | 20% | 20%",
            "Cursor-family primary teammate | Grok | 11% | 20%",
            "Specialist and Backup Programmer | Gemini/Antigravity | 15% | 11%",
            "Research Programmers | Perplexity.ai | 4% | 5%",
            "Director and separately authorized production lane | OpenAI/Codex | 25% | 8%",
        ):
            self.assertIn(expected, charter)
        self.assertIn("Maintain two distinct ledgers", prompt)
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
            payload["head_commit"]["message"] = (
                payload["head_commit"]["message"]
                + "\n\nCo-authored-by: Edward L Jones IV <owner@example.com>\n"
            )
            event.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(checker._validate_github_event(event), [])
            payload["head_commit"]["message"] = "subject\n" + payload["head_commit"]["message"]
            event.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(checker._validate_github_event(event))


class ContributionAttributionFollowupTests(unittest.TestCase):
    """Delayed review findings on PR #52, tracked by issue #62."""

    BEGIN = "[[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN]]"
    END = "[[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]]"

    @staticmethod
    def _checker(name: str):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            name, root / "scripts" / "check_contribution_tags.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_neutralization_defuses_only_semantically_valid_tags(self):
        body = "\n".join(
            [
                f"real {self.BEGIN}",
                "wiki [[Some Page]] stays",
                "code matrix[[0]][[1]] stays",
                "partial [[Codex | GPT-5.6 | BEGIN]] stays",
                "unterminated [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN stays",
                "malformed [[Codex | GPT-5.6 | 2026-13-90 | 99:99 UTC | END]]",
                "blank identity [[   | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]]",
                f"nested [[outer {self.BEGIN} outer]]",
                f"adjacent {self.BEGIN}{self.END}",
            ]
        )
        wrapped = wrap_human_submission(
            body,
            "Aesop Director",
            DIRECTOR_SYSTEM_LABEL,
            at=STAMP,
            neutralize_embedded_tags=True,
        )
        inner = wrapped.splitlines()[2:-2]

        for preserved in (
            "wiki [[Some Page]] stays",
            "code matrix[[0]][[1]] stays",
            "partial [[Codex | GPT-5.6 | BEGIN]] stays",
            "unterminated [[Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN stays",
            # Tag-shaped text the parser rejects is not a contribution tag, so
            # it keeps its exact bytes even though the grammar regex matches it.
            "malformed [[Codex | GPT-5.6 | 2026-13-90 | 99:99 UTC | END]]",
            "blank identity [[   | GPT-5.6 | 2026-08-21 | 23:05 UTC | END]]",
        ):
            self.assertIn(preserved, wrapped)
        # Every sequence the parser would accept is defused, including the ones
        # hidden behind a decoy prefix and the adjacent pair. One pass is enough:
        # a defused tag can never be parsed again.
        body_text = "\n".join(inner)
        self.assertEqual(neutralize_contribution_tags(body_text), body_text)
        self.assertIn("[ [Codex | GPT-5.6 | 2026-08-21 | 23:05 UTC | BEGIN] ]", wrapped)
        self.assertNotIn(self.BEGIN, body_text)
        self.assertNotIn(self.END, body_text)
        for line in inner:
            self.assertIsNone(parse_contribution_tag(line))
        # A real tag reached through an invalid outer candidate is still defused.
        decoy = f"[[decoy [[ | 2026-13-90 | 99:99 UTC | BEGIN]] {self.BEGIN}"
        self.assertIsNotNone(TAG_PATTERN.search(decoy))
        self.assertIsNone(parse_contribution_tag(neutralize_contribution_tags(decoy)))
        # Only the two boundaries the Director itself rendered remain parseable.
        self.assertEqual(
            [
                tag.boundary
                for line in wrapped.splitlines()
                if (tag := parse_contribution_tag(line))
            ],
            ["BEGIN", "END"],
        )

    def test_trailer_stripping_requires_a_supported_block_after_the_end_tag(self):
        checker = self._checker("check_contribution_tags_trailers")
        envelope = f"{self.BEGIN}\n\nImplement the fix\n\n{self.END}"
        supported = (
            "Co-authored-by: Edward L Jones IV <owner@example.com>\n"
            "Claude-Session: https://claude.ai/code/session_0123456789\n"
            "Signed-off-by: Codex <codex@example.com>"
        )

        for label, message in (
            ("blank separator", f"{envelope}\n\n{supported}"),
            ("whitespace-only separator", f"{envelope}\n   \n{supported}"),
            ("no separator", f"{envelope}\n{supported}"),
            ("trailing blank line", f"{envelope}\n\n{supported}\n\n"),
            # The delayed trailing-whitespace regression: a whitespace-only line
            # after the trailer block used to stop the scan, leaving the trailers
            # visible and failing an otherwise valid submission.
            ("trailing whitespace-only line", f"{envelope}\n\n{supported}\n   \n"),
        ):
            with self.subTest(label):
                self.assertEqual(
                    checker._without_trailing_git_trailers(message), envelope
                )
                self.assertEqual(
                    checker._validate_submission_envelope(
                        checker._without_trailing_git_trailers(message),
                        require_outer=True,
                        require_markdown_boundary=False,
                    ),
                    [],
                )

        for label, tail in (
            ("arbitrary prose trailer", "Note: extra visible content"),
            ("unsupported key", "Fixes: #62"),
            ("mixed supported and arbitrary", f"{supported}\nNote: extra"),
            ("resumed block after a blank line", f"{supported}\n\nNote: extra"),
            ("plain content", "still visible"),
        ):
            with self.subTest(label):
                message = f"{envelope}\n\n{tail}"
                self.assertEqual(
                    checker._without_trailing_git_trailers(message), message
                )
                self.assertTrue(
                    checker._validate_submission_envelope(
                        checker._without_trailing_git_trailers(message),
                        require_outer=True,
                        require_markdown_boundary=False,
                    )
                )

        # Trailer-shaped lines inside the envelope are body text, not trailers.
        inside = f"{self.BEGIN}\n\nNote: kept\n\n{self.END}\n\nSigned-off-by: Codex <c@example.com>"
        self.assertEqual(
            checker._without_trailing_git_trailers(inside),
            f"{self.BEGIN}\n\nNote: kept\n\n{self.END}",
        )
        # Without an END tag nothing is treated as a trailer block.
        headless = "Subject line\n\nCo-authored-by: Edward L Jones IV <owner@example.com>"
        self.assertEqual(checker._without_trailing_git_trailers(headless), headless)

    def test_submission_outer_identities_must_match(self):
        checker = self._checker("check_contribution_tags_identity")
        other_begin = "[[Cursor | Agent | 2026-08-22 | 14:50 UTC | BEGIN]]"
        other_end = "[[Gemini | Antigravity | 2026-08-22 | 15:10 UTC | END]]"
        errors = checker._validate_submission_envelope(
            f"{other_begin}\n\n{self.BEGIN}\ninner submission\n{self.END}\n\n{other_end}",
            require_outer=True,
            require_markdown_boundary=False,
        )
        self.assertEqual(
            errors, ["submission outer BEGIN and END contribution tags do not match"]
        )
        self.assertEqual(
            checker._validate_submission_envelope(
                f"{self.BEGIN}\n\nsubmission\n\n{self.END}",
                require_outer=True,
                require_markdown_boundary=False,
            ),
            [],
        )
        bold_begin = "**[[Cursor | Agent | 2026-08-22 | 14:50 UTC | BEGIN]]**"
        bold_end = "**[[Cursor | Agent | 2026-08-22 | 15:10 UTC | END]]**"
        self.assertEqual(
            checker._validate_submission_envelope(
                f"{bold_begin}\n\n**{self.BEGIN}**\nbody\n**{self.END}**\n\n{bold_end}",
                require_outer=True,
                require_markdown_boundary=True,
            ),
            ["submission outer BEGIN and END contribution tags do not match"],
        )

    def test_pure_rename_and_copy_cannot_reuse_existing_contribution_tags(self):
        checker = self._checker("check_contribution_tags_rename")
        begin = f"# {self.BEGIN}"
        end = f"# {self.END}"
        fresh_begin = "# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:30 UTC | BEGIN]]"
        fresh_end = "# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:30 UTC | END]]"

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
            (repo / "source.py").write_text(
                f"{begin}\nprint('ok')\n{end}\n", encoding="utf-8"
            )
            git("add", "source.py")
            git("commit", "--quiet", "-m", "seed")
            base = git("rev-parse", "HEAD").strip()

            git("mv", "source.py", "renamed.py")
            git("commit", "--quiet", "-m", "pure rename")
            renamed_head = git("rev-parse", "HEAD").strip()

            cwd = Path.cwd()
            try:
                os.chdir(repo)
                self.assertEqual(
                    checker.changed_paths(base, renamed_head),
                    [("R100", "source.py", "renamed.py")],
                )
                # The old path is diffed against the new one, so pre-existing
                # tags are not reported as additions by a pure rename.
                self.assertEqual(
                    checker._added_lines(base, renamed_head, "renamed.py", "source.py"),
                    [],
                )
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", renamed_head],
                ):
                    self.assertEqual(checker.main(), 1)
            finally:
                os.chdir(cwd)

            # A rename that also records a fresh outer pair is accepted.
            (repo / "renamed.py").write_text(
                f"{fresh_begin}\n{begin}\nprint('ok')\n{end}\n{fresh_end}\n",
                encoding="utf-8",
            )
            git("add", "renamed.py")
            git("commit", "--quiet", "-m", "rename with attribution")
            tagged_head = git("rev-parse", "HEAD").strip()

            git("reset", "--quiet", "--hard", base)
            (repo / "copy.py").write_text(
                f"{begin}\nprint('ok')\n{end}\n", encoding="utf-8"
            )
            git("add", "copy.py")
            git("commit", "--quiet", "-m", "pure copy")
            copied_head = git("rev-parse", "HEAD").strip()

            try:
                os.chdir(repo)
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", tagged_head],
                ):
                    self.assertEqual(checker.main(), 0)
                self.assertEqual(
                    checker.changed_paths(base, copied_head),
                    [("C100", "source.py", "copy.py")],
                )
                self.assertEqual(
                    checker._added_lines(base, copied_head, "copy.py", "source.py"), []
                )
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", copied_head],
                ):
                    self.assertEqual(checker.main(), 1)
            finally:
                os.chdir(cwd)

    def test_base_tag_lookup_separates_no_matches_from_a_real_git_failure(self):
        checker = self._checker("check_contribution_tags_base_lookup")
        begin = f"# {self.BEGIN}"
        end = f"# {self.END}"

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
            (repo / "untagged.py").write_text("print('ok')\n", encoding="utf-8")
            git("add", "untagged.py")
            git("commit", "--quiet", "-m", "seed")
            untagged_base = git("rev-parse", "HEAD").strip()

            (repo / "tagged.py").write_text(
                f"{begin}\nprint('ok')\n{end}\n", encoding="utf-8"
            )
            git("add", "tagged.py")
            git("commit", "--quiet", "-m", "add a tagged file")
            tagged_base = git("rev-parse", "HEAD").strip()

            cwd = Path.cwd()
            try:
                os.chdir(repo)
                # Exit status 1 is "no matching line", not a failure.
                self.assertEqual(checker._base_tag_pairs(untagged_base), set())
                self.assertEqual(
                    checker._base_tag_pairs(tagged_base),
                    {("Codex", "GPT-5.6", "2026-08-21", "23:05")},
                )
                # Any other status must surface instead of silently disabling
                # the inherited-pair check.
                with self.assertRaises(checker.BaseTagLookupError) as raised:
                    checker._base_tag_pairs("0" * 39 + "1")
                self.assertIn("git grep failed with status", str(raised.exception))
                with patch(
                    "sys.argv",
                    [
                        "check_contribution_tags.py",
                        "--base",
                        "0" * 39 + "1",
                        "--head",
                        tagged_base,
                    ],
                ):
                    self.assertEqual(checker.main(), 1)
            finally:
                os.chdir(cwd)

    def test_low_similarity_move_cannot_inherit_base_tree_contribution_tags(self):
        """Git reports an edited move below the similarity threshold as D + A.

        The destination then presents its inherited outer tags as additions, so
        the range check must reject a pair the base tree already carries.
        """

        checker = self._checker("check_contribution_tags_low_similarity")
        begin = f"# {self.BEGIN}"
        end = f"# {self.END}"
        fresh_begin = "# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:30 UTC | BEGIN]]"
        fresh_end = "# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:30 UTC | END]]"
        original = "\n".join(f"value_{index} = {index}" for index in range(40))
        rewritten = "\n".join(
            f"renamed_symbol_{index} = compute({index}, mode='wholly different')"
            for index in range(40)
        )

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
            (repo / "source.py").write_text(
                f"{begin}\n{original}\n{end}\n", encoding="utf-8"
            )
            git("add", "source.py")
            git("commit", "--quiet", "-m", "seed")
            base = git("rev-parse", "HEAD").strip()

            git("rm", "--quiet", "source.py")
            (repo / "moved.py").write_text(
                f"{begin}\n{rewritten}\n{end}\n", encoding="utf-8"
            )
            git("add", "moved.py")
            git("commit", "--quiet", "-m", "move and rewrite below the rename threshold")
            moved_head = git("rev-parse", "HEAD").strip()

            cwd = Path.cwd()
            try:
                os.chdir(repo)
                # Git does not pair these paths, so the inherited tags look new.
                self.assertEqual(
                    sorted(checker.changed_paths(base, moved_head)),
                    [("A", None, "moved.py"), ("D", None, "source.py")],
                )
                self.assertIn(
                    "# " + self.BEGIN,
                    checker._added_lines(base, moved_head, "moved.py"),
                )
                self.assertEqual(
                    checker._base_tag_pairs(base),
                    {("Codex", "GPT-5.6", "2026-08-21", "23:05")},
                )
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", moved_head],
                ):
                    self.assertEqual(checker.main(), 1)
            finally:
                os.chdir(cwd)

            # Recording a fresh outer pair on the destination is accepted.
            (repo / "moved.py").write_text(
                f"{fresh_begin}\n{begin}\n{rewritten}\n{end}\n{fresh_end}\n",
                encoding="utf-8",
            )
            git("add", "moved.py")
            git("commit", "--quiet", "-m", "record the move")
            tagged_head = git("rev-parse", "HEAD").strip()

            try:
                os.chdir(repo)
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", tagged_head],
                ):
                    self.assertEqual(checker.main(), 0)
            finally:
                os.chdir(cwd)

    def test_scanning_reports_every_tag_including_one_behind_a_decoy(self):
        """The base-tree scan must not stop at the first tag on a line.

        ``parse_contribution_tag`` reports one tag, which is all a boundary
        check needs. A pair sitting on a single line, or a real tag preceded by
        a tag-shaped decoy the parser rejects, needs the full scan.
        """

        self.assertEqual(
            iter_contribution_tags(f"{self.BEGIN} {self.END}"),
            [
                parse_contribution_tag(self.BEGIN),
                parse_contribution_tag(self.END),
            ],
        )
        decoyed = f"[[Codex | GPT-5.6 | 2026-13-90 | 99:99 UTC | BEGIN]] {self.BEGIN}"
        self.assertEqual(
            iter_contribution_tags(decoyed), [parse_contribution_tag(self.BEGIN)]
        )
        self.assertEqual(iter_contribution_tags("wiki [[Some Page]] only"), [])

    def test_a_pair_sharing_one_base_line_is_still_seen_as_reuse(self):
        checker = self._checker("check_contribution_tags_single_line_pair")

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
            # Both halves on one line: the retired per-line parse saw only the
            # BEGIN, so the identity never joined begins & ends.
            (repo / "notes.txt").write_text(
                f"recorded {self.BEGIN} {self.END}\n", encoding="utf-8"
            )
            git("add", "notes.txt")
            git("commit", "--quiet", "-m", "seed")
            base = git("rev-parse", "HEAD").strip()

            cwd = Path.cwd()
            try:
                os.chdir(repo)
                self.assertEqual(
                    checker._base_tag_pairs(base),
                    {("Codex", "GPT-5.6", "2026-08-21", "23:05")},
                )
            finally:
                os.chdir(cwd)

    def test_padded_maximum_length_base_tags_are_visible_to_the_reuse_check(self):
        """A base grep with a grammar of its own missed padded maximum labels.

        ``TAG_PATTERN`` accepts an 80-character label and then the whitespace it
        also allows before the delimiter. The retired POSIX expression bounded
        that whole span at 80 characters, so these identities never reached
        ``_base_tag_pairs`` and the inherited-pair check failed open for exactly
        them. Git reports the move below as an unrelated delete and add, which
        leaves the base-tree comparison as the only defense.
        """

        checker = self._checker("check_contribution_tags_padded_labels")
        contributor = "C" * 80
        system = "s" * 80
        identity = (contributor, system, "2026-08-21", "23:05")
        padded_begin = f"[[{contributor}   |   {system}   | 2026-08-21 | 23:05 UTC | BEGIN]]"
        padded_end = f"[[{contributor}   |   {system}   | 2026-08-21 | 23:05 UTC | END]]"

        # The fixture must be a tag the repository genuinely accepts, not a
        # curiosity: maximum-length labels, padding stripped off the identity.
        for rendered, boundary in ((padded_begin, "BEGIN"), (padded_end, "END")):
            tag = parse_contribution_tag(rendered)
            self.assertIsNotNone(tag)
            self.assertEqual(
                (tag.contributor, tag.system, tag.date, tag.time), identity
            )
            self.assertEqual(tag.boundary, boundary)
            self.assertEqual(len(tag.contributor), 80)
            self.assertEqual(len(tag.system), 80)
            # The span before the delimiter exceeds the retired 80-character bound.
            self.assertGreater(len(rendered.split("|")[0]) - 2, 80)

        begin = f"# {padded_begin}"
        end = f"# {padded_end}"
        fresh_begin = "# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | BEGIN]]"
        fresh_end = "# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | END]]"
        original = "\n".join(f"value_{index} = {index}" for index in range(40))
        rewritten = "\n".join(
            f"renamed_symbol_{index} = compute({index}, mode='wholly different')"
            for index in range(40)
        )

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
            (repo / "source.py").write_text(
                f"{begin}\n{original}\n{end}\n", encoding="utf-8"
            )
            git("add", "source.py")
            git("commit", "--quiet", "-m", "seed")
            base = git("rev-parse", "HEAD").strip()

            git("rm", "--quiet", "source.py")
            (repo / "moved.py").write_text(
                f"{begin}\n{rewritten}\n{end}\n", encoding="utf-8"
            )
            git("add", "moved.py")
            git("commit", "--quiet", "-m", "move and rewrite below the rename threshold")
            moved_head = git("rev-parse", "HEAD").strip()

            cwd = Path.cwd()
            try:
                os.chdir(repo)
                # The identity is in the base tree, and the scan now finds it.
                self.assertEqual(checker._base_tag_pairs(base), {identity})
                # Git does not pair these paths, so nothing else would catch it.
                self.assertEqual(
                    sorted(checker.changed_paths(base, moved_head)),
                    [("A", None, "moved.py"), ("D", None, "source.py")],
                )
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", moved_head],
                ):
                    self.assertEqual(checker.main(), 1)
            finally:
                os.chdir(cwd)

            # Recording a fresh outer pair on the destination is still accepted.
            (repo / "moved.py").write_text(
                f"{fresh_begin}\n{begin}\n{rewritten}\n{end}\n{fresh_end}\n",
                encoding="utf-8",
            )
            git("add", "moved.py")
            git("commit", "--quiet", "-m", "record the move")
            tagged_head = git("rev-parse", "HEAD").strip()

            try:
                os.chdir(repo)
                with patch(
                    "sys.argv",
                    ["check_contribution_tags.py", "--base", base, "--head", tagged_head],
                ):
                    self.assertEqual(checker.main(), 0)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-21 | 23:40 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-22 | 00:50 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-22 | 14:50 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:05 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:08 UTC | END]]
# [[Cursor Grok 4.6 | cursor-grok-4.6-high-fast | 2026-08-23 | 08:14 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:25 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:35 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 09:44 UTC | END]]
# [[Claude Code | claude-code/chief-programmer | 2026-08-23 | 10:05 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 13:46 UTC | END]]
# [[Codex | GPT-5.6 | 2026-08-24 | 14:23 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-26 | 04:49 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 02:44 UTC | END]]
