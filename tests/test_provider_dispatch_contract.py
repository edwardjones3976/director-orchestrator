# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:03 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | BEGIN]]
"""The dispatcher must grant the least privilege a task needs, and no more."""

from __future__ import annotations

import importlib.util
import unittest.mock
import pathlib
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


class EnforcementTests(unittest.TestCase):
    """Test the command that is built, not the table it was built from.

    The earlier tests derived their expectations from the same production table
    they were checking, so switching --allow to --deny, removing the eligibility
    gate, and emptying material_authors all left 10 of 10 passing.
    """

    def test_every_rule_is_introduced_by_allow_not_deny(self):
        for task in dispatch.TASK_ALLOW_RULES:
            command = dispatch.build_command("grok", task, "p.txt", 25)
            self.assertNotIn("--deny", command, task)
            for rule in dispatch.TASK_ALLOW_RULES[task]:
                position = command.index(rule)
                self.assertEqual(command[position - 1], "--allow", f"{task}: {rule}")

    def test_no_task_may_reach_a_github_write_verb(self):
        """gh api accepts -X PUT, so holding it is holding a merge."""

        for task, rules in dispatch.TASK_ALLOW_RULES.items():
            joined = " ".join(rules)
            self.assertNotIn("gh api", joined, task)
            if task != "merge":
                for verb in ("gh pr merge", "gh pr close", "gh pr edit", "git push"):
                    self.assertNotIn(verb, joined, f"{task} grants {verb}")

    def test_unknown_authorship_is_refused_rather_than_cleared(self):
        """An empty range proves nothing, so it must not read as eligible."""

        eligible, reason = dispatch.is_eligible("grok", set())
        self.assertFalse(eligible)
        self.assertIn("cannot establish authorship", reason)

    def test_a_tag_variant_still_identifies_its_author(self):
        """Repository history records 'Cursor Grok 4.6', not a bare 'Grok'."""

        eligible, _ = dispatch.is_eligible("grok", {"Cursor Grok 4.6"})
        self.assertFalse(eligible)

    def test_material_authors_reads_real_history(self):
        """Mocking the extractor cannot prove the extractor works.

        An earlier version of this test patched material_authors and asserted
        the gate refused, which stayed true after the real function was stubbed
        to return nothing. A later version ran it over HEAD~1..HEAD of the
        checkout, which errored in CI: the test job checks out at the default
        depth of 1, so HEAD has no parent and git log exits 128. This builds a
        two-commit repository and runs the real function over it, so it depends
        on git rather than on how the checkout was fetched.
        """

        import contextlib
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            repository = pathlib.Path(raw)

            def git(*arguments):
                return subprocess.run(
                    ["git", *arguments], cwd=repository, capture_output=True, text=True,
                )

            if git("init", "-q", "-b", "main").returncode != 0:
                self.skipTest("git is unavailable in this environment")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (repository / "a.txt").write_text("base", encoding="utf-8")
            git("add", "-A")
            # The base commit carries a different author's tag. Without it the
            # range is unfalsifiable: "git log HEAD" and "git log HEAD~1..HEAD"
            # return the same authorship when only the head commit is tagged,
            # so dropping the range entirely still passed.
            base_message = "\n".join((
                "[[OpenAI Codex | GPT-6 | 2026-09-08 | 00:00 UTC | BEGIN]]",
                "",
                "base commit",
                "",
                "[[OpenAI Codex | GPT-6 | 2026-09-08 | 00:00 UTC | END]]",
            ))
            git("commit", "--no-gpg-sign", "-q", "-m", base_message)
            (repository / "a.txt").write_text("changed", encoding="utf-8")
            git("add", "-A")
            message = "\n".join((
                "[[Claude Code | Claude Opus 5 | 2026-09-09 | 00:00 UTC | BEGIN]]",
                "",
                "subject",
                "",
                "[[Claude Code | Claude Opus 5 | 2026-09-09 | 00:00 UTC | END]]",
            ))
            git("commit", "--no-gpg-sign", "-q", "-m", message)
            with contextlib.chdir(repository):
                authors = dispatch.material_authors("HEAD~1", "HEAD")

        # Exactly, not "contains": the base commit is tagged by a different
        # author and is outside the range, so reading the whole history instead
        # of the range fails here rather than passing.
        self.assertEqual(authors, {"Claude Code"})

    def test_unknown_authorship_refuses_a_merge_dispatch(self):
        eligible, reason = dispatch.is_eligible("grok", set())
        self.assertFalse(eligible)
        self.assertIn("cannot establish authorship", reason)

    def test_the_command_builds_without_the_provider_cli_installed(self):
        """CI hosts no provider CLIs, so construction must not fail on PATH.

        build_command called shutil.which and exited, which failed every
        command test on a runner rather than on a real dispatch.
        """

        with unittest.mock.patch.object(dispatch.shutil, "which", return_value=None):
            command = dispatch.build_command("grok", "review", "p.txt", 25)
        self.assertEqual(command[0], "grok")

    def test_a_resolved_executable_is_dispatched_not_the_bare_name(self):
        """Windows CLIs are .cmd shims; CreateProcess ignores PATHEXT.

        Dropping the resolved path made every real dispatch die with
        WinError 2 while every test still passed.
        """

        with unittest.mock.patch.object(
            dispatch.shutil, "which", return_value="C:/tools/grok.cmd"
        ):
            command = dispatch.build_command("grok", "review", "p.txt", 25)
        self.assertEqual(command[0], "C:/tools/grok.cmd")

    def test_the_codex_command_carries_the_prompt(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "p.txt"
            path.write_text("REVIEW THIS EXACT TEXT", encoding="utf-8")
            command = dispatch.build_command("codex", "review", str(path), 25)
        self.assertIn("REVIEW THIS EXACT TEXT", command)


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
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:03 UTC | END]]
