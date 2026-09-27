# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:19 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | BEGIN]]
# [[Codex | GPT-6 | 2026-09-06 | 00:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 18:44 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:52 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:46 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:40 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:35 UTC | BEGIN]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:22 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-04 | 12:37 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:37 UTC | BEGIN]]
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
import io
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
        self.assertIsNone(handoff["provider_identity"]["standing_role"])
        self.assertEqual(handoff["provider_identity"]["provider_label"], "Claude")
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
            "| OpenAI / Codex production | 27% | 40% | 20% | 32% |",
            "| Claude | 33% | 25% | 25% | 38% |",
            "| Cursor | 10% | 15% | 5% | 5% |",
            "| Grok | 10% | 10% | 5% | 5% |",
            "| Gemini / Antigravity | 5% | 10% | 5% | 5% |",
            "| Perplexity | 5% | 0% | 25% | 0% |",
        ):
            self.assertIn(expected, charter)
        self.assertIn("Freeze one approved allocation profile and its digest before plan approval", prompt)
        self.assertIn("historical share bounds are maintained but non-enforcing (retired by owner 2026-09-21)", prompt)
        self.assertIn("startup uses the full 20-slot denominator with real assignments only", prompt)
        self.assertIn("cheapest capable trusted model first", prompt)
        self.assertIn("GitHub Copilot Lite", prompt)
        self.assertIn("Contributed-by:", charter)
        self.assertIn("never authenticate provider", charter.lower())

    def test_director_provider_authorship_boundary_is_synchronized(self):
        """Every durable rule surface must carry the same authorship boundary.

        The boundary lives only in prose, so a surface that quietly drops it
        would let delegation, routing, review, or merge activity be reported as
        Director authorship again. Compare whitespace-normalized text so a
        reflowed paragraph does not defeat the check.
        """

        root = Path(__file__).resolve().parents[1]
        surfaces = {
            "agents": root / "AGENTS.md",
            "prompt": root / "prompts" / "director-system.md",
            "charter": root / "docs" / "team-operating-charter.md",
            "policy": root / "docs" / "contribution-attribution-policy.md",
            "rule": root / ".cursor" / "rules" / "contribution-attribution.mdc",
        }
        normalized = {
            name: " ".join(path.read_text(encoding="utf-8").split())
            for name, path in surfaces.items()
        }

        for name, text in normalized.items():
            self.assertIn(
                "does not inherit authorship through delegation, routing, "
                "coordination, QA, review, acceptance, relay, merge eligibility "
                "evaluation, or merge execution",
                text,
                name,
            )
            self.assertIn("create no code authorship", text, name)
            self.assertIn("acknowledge actual contributions only", text, name)

        for name in ("agents", "prompt", "charter", "policy"):
            self.assertIn(
                "may not independently accept or merge code it actually "
                "materially authored",
                normalized[name],
                name,
            )

        for name in ("agents", "prompt", "charter"):
            self.assertIn("substantial majority of coding", normalized[name], name)
            self.assertIn("removes no deterministic gate", normalized[name], name)

        self.assertIn(
            "alwaysApply: true", surfaces["rule"].read_text(encoding="utf-8")
        )

    def test_openai_authorship_ban_is_scoped_to_control_plane_inference(self):
        """Both sides of the authorship boundary must stay pinned.

        An unconditional "never record OpenAI as the logical producer, a
        material author, or a co-author" would order the Director to strip
        accurate authorship from a separately owner-authorized OpenAI
        production lane, and to keep the reviewer and merge independence that
        lane no longer has. Scope the prohibition to authorship inferred from
        control-plane activity alone, and keep the affirmative rule for actual
        production explicit.
        """

        root = Path(__file__).resolve().parents[1]
        prompt = " ".join(
            (root / "prompts" / "director-system.md")
            .read_text(encoding="utf-8")
            .split()
        )

        # Coordination side: supervising delegated work is still not authorship.
        self.assertIn(
            "does not inherit authorship through delegation, routing, "
            "coordination, QA, review, acceptance, relay, merge eligibility "
            "evaluation, or merge execution",
            prompt,
        )
        self.assertIn("create no code authorship", prompt)
        self.assertIn(
            "Never infer acting Director logical production, material authorship, or "
            "co-authorship from control-plane activity alone",
            prompt,
        )
        self.assertNotIn(
            "Never record OpenAI as the logical producer, a material author, "
            "or a co-author of a provider candidate",
            prompt,
        )

        # Production side: actual OpenAI production keeps OpenAI attribution
        # and costs the control plane its independence for that candidate.
        self.assertIn(
            "actually produces or materially edits a candidate, record OpenAI "
            "as its logical producer or material author",
            prompt,
        )
        self.assertIn(
            "removes OpenAI reviewer and merge independence for that "
            "candidate",
            prompt,
        )
        self.assertIn(
            "may not independently accept or merge code it actually materially "
            "authored",
            prompt,
        )

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

class GitAttributionTests(unittest.TestCase):
    def setUp(self):
        self.checker = ContributionAttributionFollowupTests._checker("git_attribution")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        isolated_git = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        })
        isolated_git.start()
        self.addCleanup(isolated_git.stop)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.base = self.commit("Baseline")
        self.git("update-ref", "refs/remotes/origin/main", self.base)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "commit.gpgsign=false", *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_every_commit_needs_trailer_not_only_head(self):
        missing = self.commit("Missing attribution")
        head = self.commit("Good\n\nContributed-by: OpenAI Codex | GPT-6")
        errors = self.checker.validate_commits(self.base, head)
        self.assertEqual(len(errors), 1)
        self.assertIn(missing, errors[0])

    def test_trailer_grammar_and_real_git_parsing(self):
        cases = (
            ("Change\n\nContributed-by: Codex | GPT-6", True),
            ("Change\n\nContributed-by: Codex | GPT-6\nContributed-by: Tool | System\nCo-Authored-By: Fixture <fixture@example.invalid>", True),
            ("Change\n\nContributed-by: Codex", False),
            ("Change\n\nContributed-by:   | GPT-6", False),
            ("Change\n\nContributed-by: Codex |   ", False),
            ("Change\n\nContributed-by: Codex | GPT-6 | extra", False),
            ("Change\n\nContributed-by: <assistant> | <model>", False),
            ("Change\n\nContributed-by: Codex | GPT-6\nContributed-by: broken", False),
            ("Change\n\nContributed-by: Codex | GPT-6\nContributed-by:", False),
            ("Change\n\nContributed-by: Codex | GPT-6\n\nLater prose", False),
            ("Contributed-by: Codex | GPT-6\n\nOrdinary prose", False),
        )
        for message, valid in cases:
            with self.subTest(message=message):
                base = self.git("rev-parse", "HEAD")
                head = self.commit(message)
                self.assertEqual(self.checker.validate_commits(base, head) == [], valid)

    def test_added_and_stripped_file_tags_do_not_affect_result(self):
        path = self.root / "example.md"
        path.write_text("<!-- [[Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]] -->\nText\n", encoding="utf-8")
        tagged = self.commit("Add\n\nContributed-by: Codex | GPT-6")
        self.assertEqual(self.checker.validate_commits(self.base, tagged), [])
        path.write_text("Text\n", encoding="utf-8")
        stripped = self.commit("Strip\n\nContributed-by: Codex | GPT-6")
        self.assertEqual(self.checker.validate_commits(tagged, stripped), [])

    def test_zero_base_excludes_main_and_invalid_revision_fails_closed(self):
        missing = self.commit("Missing attribution")
        head = self.commit("Good\n\nContributed-by: Codex | GPT-6")
        errors = self.checker.validate_commits("0" * 40, head)
        self.assertEqual(len(errors), 1)
        self.assertIn(missing, errors[0])
        with patch("sys.argv", ["checker", "--base", "missing-ref", "--head", head]), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(self.checker.main(), 1)

    def test_branch_merging_main_excludes_unattributed_main_commits(self):
        main = self.commit("Legacy main change")
        self.git("update-ref", "refs/remotes/origin/main", main)
        self.git("checkout", "-b", "feature", self.base)
        branch = self.commit("Feature\n\nContributed-by: Codex | GPT-6")
        self.git("merge", "--no-ff", "-m", "Sync main\n\nContributed-by: Codex | GPT-6", main)
        self.assertEqual(self.checker.validate_commits(branch, "HEAD"), [])

    def test_main_push_github_merge_and_legacy_commits_pass(self):
        main = self.commit("Legacy main change")
        self.git("checkout", "-b", "legacy", self.base)
        legacy = self.commit("Feature checked under old tag rule")
        self.git("checkout", "-b", "main-push", main)
        with patch.dict(os.environ, {
            "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com",
        }):
            self.git("merge", "--no-ff", "-m", "Merge pull request #123 from fixture/legacy", legacy)
        head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", head)
        event = self.root / "event.json"
        event.write_text(json.dumps({"ref": "refs/heads/main"}), encoding="utf-8")
        with patch("sys.argv", ["checker", "--base", main, "--head", head,
                                "--github-event", str(event)]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.checker.main(), 0)

    def test_github_merge_exemption_requires_committer_and_subject(self):
        main = self.commit("Main change")
        self.git("update-ref", "refs/remotes/origin/main", main)
        self.git("checkout", "-b", "feature", self.base)
        branch = self.commit("Feature\n\nContributed-by: Codex | GPT-6")
        subject = "Merge pull request #123 from fixture/main"
        with patch.dict(os.environ, {
            "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com",
        }):
            self.git("merge", "--no-ff", "-m", subject, main)
            self.assertEqual(self.checker.validate_commits(branch, "HEAD"), [])
            self.git("commit", "--amend", "-m", "Local integration")
            self.assertTrue(self.checker.validate_commits(branch, "HEAD"))
        self.git("commit", "--amend", "-m", subject)
        self.assertTrue(self.checker.validate_commits(branch, "HEAD"))

    def test_single_parent_github_subject_without_trailer_fails(self):
        with patch.dict(os.environ, {
            "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com",
        }):
            head = self.commit("Merge pull request #123 from fixture/feature")
        self.assertIn(head, self.checker.validate_commits(self.base, head)[0])

    def test_local_merge_with_resolution_without_trailer_fails(self):
        path = self.root / "conflict.txt"
        path.write_text("baseline\n", encoding="utf-8")
        base = self.commit("Base\n\nContributed-by: Codex | GPT-6")
        path.write_text("main\n", encoding="utf-8")
        main = self.commit("Legacy main change")
        self.git("update-ref", "refs/remotes/origin/main", main)
        self.git("checkout", "-b", "feature", base)
        path.write_text("feature\n", encoding="utf-8")
        branch = self.commit("Feature\n\nContributed-by: Codex | GPT-6")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "--no-ff", main)
        path.write_text("combined resolution\n", encoding="utf-8")
        head = self.commit("Resolve integration")
        self.assertEqual(len(self.git("show", "-s", "--format=%P", head).split()), 2)
        errors = self.checker.validate_commits(branch, head)
        self.assertEqual(len(errors), 1)
        self.assertIn(head, errors[0])

    def test_missing_default_branch_fails_closed_with_setup_error(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        with patch("sys.argv", ["checker", "--base", self.base, "--head", self.base]), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(self.checker.main(), 1)
        self.assertIn("setup error: default-branch ref unavailable", stderr.getvalue())

    def test_explicit_default_branch_ref(self):
        self.git("update-ref", "refs/remotes/origin/trunk", self.base)
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        head = self.commit("Good\n\nContributed-by: Codex | GPT-6")
        with patch("sys.argv", ["checker", "--base", self.base, "--head", head,
                                "--default-branch-ref", "origin/trunk"]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.checker.main(), 0)

    def test_pr_headers(self):
        header = "Sole author: OpenAI Codex (GPT-6)"
        for body in (header, header + "; assistance: tool-a, tool-b", "Summary\n\n" + header):
            self.assertEqual(self.checker.validate_pr_body(body), [])
        for body in ("", "Sole author: Codex", "Sole author:  (GPT-6)",
                     "Sole author: Codex ( )", "Sole author: <assistant> (<model>)",
                     header + "\n" + header, header + "; assistance: ",
                     header + "; assistance: one, ", "<!--\n" + header + "\n-->",
                     "```text\n" + header + "\n```", "> " + header, "    " + header):
            with self.subTest(body=body):
                self.assertTrue(self.checker.validate_pr_body(body))

    def test_event_file_pr_and_push_and_malformed_input(self):
        event = self.root / "event.json"
        for payload, valid in (({"pull_request": {"body": "Sole author: Codex (GPT-6)"}}, True),
                               ({"pull_request": {"body": None}}, False),
                               ({"ref": "refs/heads/main"}, True)):
            event.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(self.checker._validate_github_event(event) == [], valid)
        head = self.commit("Good\n\nContributed-by: Codex | GPT-6")
        event.write_text("invalid JSON", encoding="utf-8")
        with patch("sys.argv", ["checker", "--base", self.base, "--head", head, "--github-event", str(event)]), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(self.checker.main(), 1)

    def test_push_main_still_checks_trailers_and_pr_checks_both(self):
        event = self.root / "event.json"
        head = self.commit("No trailer")
        for payload, valid in (({"ref": "refs/heads/main"}, False),
                               ({"pull_request": {"body": "Sole author: Codex (GPT-6)"}}, False)):
            event.write_text(json.dumps(payload), encoding="utf-8")
            with patch("sys.argv", ["checker", "--base", self.base, "--head", head, "--github-event", str(event)]), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(self.checker.main(), 1)
        base = head
        head = self.commit("Good\n\nContributed-by: Codex | GPT-6")
        for payload, expected in (({"ref": "refs/heads/main"}, 0),
                                  ({"pull_request": {"body": "Sole author: Codex (GPT-6)"}}, 0),
                                  ({"pull_request": {"body": "No header"}}, 1)):
            event.write_text(json.dumps(payload), encoding="utf-8")
            with patch("sys.argv", ["checker", "--base", base, "--head", head, "--github-event", str(event)]), patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(self.checker.main(), expected)

    def test_policy_and_templates_use_git_attribution(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("AGENTS.md", "CLAUDE.md", "docs/team-operating-charter.md",
                     "docs/contribution-attribution-policy.md", ".github/pull_request_template.md",
                     "docs/templates/contribution-submission.md"):
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("Contributed-by:", text)
                self.assertIn("Sole author:", text)
                self.assertIn("Co-Authored-By", text)
                self.assertIn("#284", text)
                self.assertNotIn("add a fresh outer", text)


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
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 03:37 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 23:05 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-04 | 11:56 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-04 | 12:37 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:22 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:35 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:40 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:46 UTC | END]]
# [[Cursor | Composer 2.5 | 2026-09-04 | 18:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 18:44 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 00:23 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-05 | 22:57 UTC | END]]
# [[Codex | GPT-6 | 2026-09-06 | 00:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:34 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:24 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 07:29 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-09 | 18:19 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 22:35 UTC | END]]
