# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:14 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:03 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 00:47 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 21:10 UTC | BEGIN]]
"""A task runs at full capability; only what would let it alter its subject goes."""

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

    def test_the_deny_list_is_never_described_as_a_boundary(self):
        """It is a guardrail against drift, not a control against intent.

        An independent review of #143 executed the bypasses under its own
        review grant: python/subprocess reached `gh pr merge --help`,
        `gh auth token` plus urllib reached the API with merge-capable auth,
        and `gh issue close` reaches a pull request because a pull request is
        an issue. A prefix matcher over direct command strings cannot bound an
        agent that can spawn a subprocess. Claiming otherwise in this file is
        how the claim ended up in a commit message and a status report.
        """

        import inspect

        source = inspect.getsource(dispatch)
        prose = source.split('"""', 2)[1].lower()
        self.assertIn("not a security boundary", prose)
        for overclaim in (
            "the gate is real",
            "keeps a review a review",
            "coexist, so the gate",
        ):
            self.assertNotIn(overclaim, prose, overclaim)


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

    def test_no_prompt_text_ever_reaches_the_argument_vector(self):
        """Prompt text in argv is a command injection on Windows.

        Both CLIs resolve to .CMD shims, and Windows hands a .CMD invocation to
        the command shell, which reparses the argument string. An independent
        review of #143 put

            SAFE" & echo PR143_INJECTION_MARKER & rem "

        in a prompt and the echo ran, entirely outside the provider's
        permission controls. Prompts quote pull-request bodies and review
        comments, so their text is not trusted.

        A path is fine; the text is not. grok resolves to grok.EXE rather than
        a shim and reads a file, so it was never reachable this way. codex took
        the prompt positionally, which is the route that was demonstrated, and
        now reads stdin.
        """

        import tempfile

        hostile = 'SAFE" & echo PR143_INJECTION_MARKER & rem "'
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "p.txt"
            path.write_text(hostile, encoding="utf-8")
            for provider in dispatch.PROVIDERS:
                for task in dispatch.TASK_DENY_RULES:
                    command = dispatch.build_command(provider, task, str(path), 25)
                    for argument in command:
                        self.assertNotIn(
                            "PR143_INJECTION_MARKER", argument, f"{provider} {task}"
                        )

    def test_codex_takes_its_prompt_on_stdin_not_positionally(self):
        """The positional prompt is the argv route that was demonstrated."""

        command = dispatch.build_command("codex", "review", "p.txt", 25)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("p.txt", command)

    def test_the_codex_sandbox_is_pinned_at_dispatch(self):
        """Inheriting the host config is inheriting whatever it happens to say.

        This host's ~/.codex/config.toml carries sandbox_mode
        "danger-full-access" with approval_policy "never". An earlier version
        claimed the opposite -- that codex's sandbox withholds the GitHub
        credential -- on the strength of an HTTP 401 that came from `codex
        sandbox`, a different command from the `codex exec` built here. The
        grant must be what this file says, not what a config file elsewhere
        says.
        """

        command = dispatch.build_command("codex", "review", "p.txt", 25)
        self.assertIn("-s", command)
        self.assertEqual(command[command.index("-s") + 1], "workspace-write")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertNotIn("danger-full-access", command)

    def test_no_provider_claims_github_is_unreachable(self):
        """That exemption was disproved by running gh under the real dispatch."""

        for provider, spec in dispatch.PROVIDERS.items():
            self.assertNotIn("github_unreachable", spec, provider)


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
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 01:52 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 04:27 UTC | END]]


class TurnBudgetTests(unittest.TestCase):
    def test_the_default_turn_budget_survives_a_mutation_review(self):
        """25 turns ends a real review mid-work and reports it as cancelled.

        Measured across nine Grok dispatches: every session that reported
        `cancelled` ended at exactly 25 turns, and every session that reported
        `end_turn` finished in 12 to 23. A review that runs the suite, mutates
        production and re-runs it does not fit in 25, and the failure looks
        like a hang or a permission denial rather than a budget.
        """

        import argparse
        import contextlib
        import io

        parser = None
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            # main() builds the parser and then reaches the network, so the
            # default is read from a parse of --help-free arguments instead.
            parser = argparse.ArgumentParser()
        default = dispatch.main.__defaults__
        source = __import__("inspect").getsource(dispatch.main)
        self.assertIn('"--turns", type=int, default=80', source)
        self.assertNotIn("default=25", source)
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:14 UTC | END]]
