# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | BEGIN]]
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


class DenyRuleTests(unittest.TestCase):
    def test_a_review_cannot_change_what_it_is_reviewing(self):
        """A review that can merge is not a review, whatever else it can do.

        Providers now run with full capability, so this is the only thing
        standing between a reviewer and the pull request it is judging. Each
        verb below either merges it, alters its state, or moves the branch out
        from under the SHA the verdict is bound to.
        """

        denied = " ".join(dispatch.TASK_DENY_RULES["review"])
        for verb in ("gh pr merge", "gh api", "gh pr edit", "gh pr close", "git push"):
            self.assertIn(verb, denied, verb)

    def test_the_merge_task_is_the_only_one_that_may_merge(self):
        may_merge = {
            task for task, rules in dispatch.TASK_DENY_RULES.items()
            if not any("gh pr merge" in rule for rule in rules)
        }
        self.assertEqual(may_merge, {"merge"})

    def test_gh_api_is_denied_because_it_reaches_the_merge_endpoint(self):
        """gh api takes -X PUT, so holding it is holding a merge.

        Denying the literal "gh pr merge" alone would not have prevented a
        review from merging through the REST pull-request endpoint.
        """

        self.assertIn("Bash(gh api:*)", dispatch.TASK_DENY_RULES["review"])

    def test_rules_are_scoped_never_blanket(self):
        for task, rules in dispatch.TASK_DENY_RULES.items():
            for rule in rules:
                self.assertNotIn("Bash(gh:*)", rule)
                self.assertNotIn("Bash(*", rule)
                self.assertTrue(rule.endswith(":*)"), f"{task}: {rule}")

    def test_a_provider_that_cannot_enforce_a_deny_list_is_refused(self):
        """Dropping the gate silently is worse than refusing the dispatch.

        codex carries no deny flag, so it is admitted only on the separate,
        measured ground that its sandbox withholds the GitHub credential. A
        provider with neither must not be dispatched to review at all.
        """

        spec = dict(dispatch.PROVIDERS["codex"])
        spec["github_unreachable"] = False
        with unittest.mock.patch.dict(dispatch.PROVIDERS, {"codex": spec}):
            with self.assertRaises(SystemExit) as caught:
                dispatch.build_command("codex", "review", "p.txt", 25)
        self.assertIn("cannot enforce", str(caught.exception))


class EnforcementTests(unittest.TestCase):
    """Test the command that is built, not the table it was built from.

    The earlier tests derived their expectations from the same production table
    they were checking, so switching --allow to --deny, removing the eligibility
    gate, and emptying material_authors all left 10 of 10 passing.
    """

    def test_every_denied_rule_reaches_the_built_command_as_a_deny(self):
        for task in dispatch.TASK_DENY_RULES:
            command = dispatch.build_command("grok", task, "p.txt", 25)
            self.assertNotIn("--allow", command, task)
            for rule in dispatch.TASK_DENY_RULES[task]:
                position = command.index(rule)
                self.assertEqual(command[position - 1], "--deny", f"{task}: {rule}")

    def test_the_review_command_carries_full_capability_and_the_gate(self):
        """Full capability is the point; the gate is what makes it safe.

        The owner asked for full capabilities after a read-only grant cancelled
        a review mid-run. Both halves must be in the command that is actually
        dispatched, not merely in the table it was built from.
        """

        command = dispatch.build_command("grok", "review", "p.txt", 25)
        self.assertIn("--always-approve", command)
        self.assertNotIn("--permission-mode", command)
        merge = command.index("Bash(gh pr merge:*)")
        self.assertEqual(command[merge - 1], "--deny")

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
    def test_the_merge_command_subtracts_nothing(self):
        """The acting agent needs every verb the merge preconditions require.

        A merge dispatch verifies the head SHA, the state and the six required
        checks for itself before merging, so subtracting a GitHub verb here
        would break the verification rather than protect anything.
        """

        command = dispatch.build_command("grok", "merge", "p.txt", 25)
        self.assertEqual(dispatch.TASK_DENY_RULES["merge"], ())
        self.assertNotIn("--deny", command)
        self.assertIn("--always-approve", command)

    def test_cursor_is_absent_because_it_demands_a_disabled_sandbox(self):
        """Never dispatch to a CLI that requires switching a sandbox off."""

        self.assertNotIn("cursor", dispatch.PROVIDERS)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:03 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | END]]
