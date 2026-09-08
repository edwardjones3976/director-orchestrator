# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | BEGIN]]
"""The dispatcher must grant the least privilege a task needs, and no more."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "dispatch_provider_task", ROOT / "scripts" / "dispatch_provider_task.py"
)
dispatch = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(dispatch)


class AllowRuleTests(unittest.TestCase):
    def test_review_is_read_only(self):
        """A review that can merge is not a review."""

        rules = dispatch.TASK_ALLOW_RULES["review"]
        # Named write operations, not substrings: git merge-base is read-only
        # and a review genuinely needs it, so matching on "merge" alone would
        # reject a correct rule and teach nothing.
        writes = (
            "gh pr merge", "gh pr close", "gh pr edit", "gh pr review",
            "git push", "git commit", "git merge ", "git rebase", "git reset",
            "rm ", "del ",
        )
        for rule in rules:
            for forbidden in writes:
                self.assertNotIn(forbidden, rule.lower(), rule)

    def test_merge_is_the_only_task_granted_merge(self):
        granted = {
            task for task, rules in dispatch.TASK_ALLOW_RULES.items()
            if any("gh pr merge" in rule for rule in rules)
        }
        self.assertEqual(granted, {"merge"})

    def test_gh_is_granted_explicitly_because_the_shared_settings_omit_it(self):
        """The whole failure mode was gh being absent from the shared allowlist.

        A denied command surfaces as a cancelled session, which reads like a
        broken provider, so every task that talks to GitHub must carry its own
        rule rather than relying on inherited settings.
        """

        for task, rules in dispatch.TASK_ALLOW_RULES.items():
            self.assertTrue(
                any(rule.startswith("Bash(gh ") for rule in rules),
                f"{task} cannot reach GitHub",
            )

    def test_rules_are_scoped_never_blanket(self):
        for task, rules in dispatch.TASK_ALLOW_RULES.items():
            for rule in rules:
                self.assertNotIn("Bash(gh:*)", rule)
                self.assertNotIn("Bash(*", rule)
                self.assertTrue(rule.endswith(":*)"), f"{task}: {rule}")


class EligibilityTests(unittest.TestCase):
    def test_a_material_author_may_never_merge_its_own_work(self):
        eligible, reason = dispatch.is_eligible("codex", {"OpenAI Codex", "Claude Code"})
        self.assertFalse(eligible)
        self.assertIn("materially authored", reason)

    def test_a_non_author_is_eligible(self):
        eligible, reason = dispatch.is_eligible("grok", {"OpenAI Codex", "Claude Code"})
        self.assertTrue(eligible)
        self.assertEqual(reason, "")

    def test_claude_is_barred_by_its_own_tag_identity(self):
        self.assertFalse(dispatch.is_eligible("claude", {"Claude Code"})[0])

    def test_every_provider_has_a_tag_identity(self):
        """A provider with no known identity would look eligible for everything."""

        for provider in dispatch.PROVIDERS:
            self.assertIn(provider, dispatch.TAG_IDENTITIES, provider)


class CommandTests(unittest.TestCase):
    def test_grok_receives_each_allow_rule(self):
        command = dispatch.build_command("grok", "merge", "p.txt", 25)
        for rule in dispatch.TASK_ALLOW_RULES["merge"]:
            self.assertIn(rule, command)
        self.assertIn("--permission-mode", command)
        self.assertIn("dontAsk", command)

    def test_cursor_is_absent_because_it_demands_a_disabled_sandbox(self):
        """Never dispatch to a CLI that requires switching a sandbox off."""

        self.assertNotIn("cursor", dispatch.PROVIDERS)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | END]]
