# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:17 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:23 UTC | BEGIN]]
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


class TurnBudgetTests(unittest.TestCase):
    """Exhaustion must be detected, not just made less likely.

    Raising the cap only moves the cliff. What cost the time was that hitting
    it is indistinguishable from a hang, a denial or a broken provider, so the
    detector is the fix and the number is a knob.
    """

    def _session(self, stop, turns):
        import json

        return json.dumps({"stopReason": stop, "num_turns": turns}).encode()

    def test_a_session_that_died_on_the_cap_is_recognised(self):
        for turns in (25, 80):
            self.assertEqual(
                dispatch.turn_budget_exhausted(self._session("cancelled", turns), turns),
                turns,
            )

    def test_a_denial_below_the_cap_is_not_reported_as_exhaustion(self):
        """Both report `cancelled`; only the turn count separates them.

        A permission denial ends the session early -- one measured at turn 3 --
        and calling that a budget problem would send the next person to raise a
        number that was never the cause.
        """

        self.assertIsNone(dispatch.turn_budget_exhausted(self._session("cancelled", 3), 80))

    def test_a_completed_session_is_never_reported_as_exhausted(self):
        for turns in (12, 23, 79):
            self.assertIsNone(
                dispatch.turn_budget_exhausted(self._session("end_turn", turns), 80)
            )

    def test_the_real_recorded_sessions_are_classified_correctly(self):
        """The nine dispatches this was diagnosed from, replayed.

        Cancelled at exactly the cap of 25 in two of them; the rest completed
        between 12 and 23 turns.
        """

        measured = [
            ("cancelled", 25, True), ("end_turn", 12, False), ("end_turn", 15, False),
            ("end_turn", 12, False), ("end_turn", 16, False), ("end_turn", 23, False),
            ("end_turn", 14, False), ("end_turn", 20, False), ("cancelled", 25, True),
        ]
        for stop, used, exhausted in measured:
            found = dispatch.turn_budget_exhausted(self._session(stop, used), 25)
            self.assertEqual(found is not None, exhausted, (stop, used))

    def test_concatenated_and_truncated_output_does_not_crash_the_detector(self):
        """Providers emit several objects, and a killed one leaves a partial tail."""

        good = self._session("end_turn", 12)
        bad = self._session("cancelled", 80)
        self.assertEqual(dispatch.turn_budget_exhausted(good + b"\n" + bad, 80), 80)
        self.assertIsNone(dispatch.turn_budget_exhausted(good + b'\n{"stopRea', 80))
        self.assertIsNone(dispatch.turn_budget_exhausted(b"", 80))
        # Codex emits prose, not JSON, and ignores --turns entirely.
        self.assertIsNone(dispatch.turn_budget_exhausted(b"CHANGES_REQUIRED\n", 80))

    def test_a_merge_is_not_given_a_review_sized_budget(self):
        """A merge checks a SHA, a state and six checks. It never nears 80."""

        self.assertEqual(dispatch.TASK_TURNS["merge"], 15)
        self.assertEqual(dispatch.TASK_TURNS["review"], 80)
        self.assertEqual(set(dispatch.TASK_TURNS), set(dispatch.TASK_DENY_RULES))
        # Whatever the numbers become: a review must never get less headroom
        # than a merge, and never fall back to the 25 that caused this. The
        # merge budget is deliberately small -- the delegated merge of #154
        # completed in 4 turns -- but must stay clear of that measured cost.
        self.assertGreater(dispatch.TASK_TURNS["review"], dispatch.TASK_TURNS["merge"])
        self.assertGreater(dispatch.TASK_TURNS["review"], 25)
        self.assertGreaterEqual(dispatch.TASK_TURNS["merge"], 10)
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:14 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:23 UTC | END]]


class DispatchGateTests(unittest.TestCase):
    """main() itself, which nothing tested.

    Every earlier test here exercised build_command or a helper. A delta review
    of #143 disconnected main() from the exhaustion detector, hardcoded turns=25
    inside it, and removed the stdin pipe -- all three left the suite green,
    because main() was never called.
    """

    def _run(self, task, prompt_text, authors, extra=()):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "p.txt"
            path.write_text(prompt_text, encoding="utf-8")
            argv = [
                "--pr", "143", "--task", task, "--provider", "grok",
                "--prompt-file", str(path), "--print-command", *extra,
            ]
            with unittest.mock.patch.object(
                dispatch, "pull_request_range", lambda pr: ("base", "head")
            ), unittest.mock.patch.object(
                dispatch, "material_authors", lambda base, head: set(authors)
            ):
                import contextlib
                import io

                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = dispatch.main(argv)
                return code, out.getvalue(), err.getvalue()

    def test_an_ineligible_reviewer_is_refused_not_merely_noted(self):
        """It printed a NOTE and dispatched anyway, at full capability.

        That text was written when a review was read-only. It no longer is, so
        "dispatching read-only review anyway" described a grant that had ceased
        to exist. Reproduced by the review: exit 0 with the command built.
        """

        code, _, err = self._run("review", "review this", {"Cursor Grok 4.6"})
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", err)
        self.assertNotIn("anyway", err)

    def test_an_ineligible_merge_is_refused(self):
        code, _, err = self._run("merge", "merge it", {"Cursor Grok 4.6"})
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", err)

    def test_a_review_prompt_asking_for_a_merge_is_refused(self):
        """Eligibility is computed for --task; the prompt is free text.

        A review dispatch whose prompt said "please merge this PR now" cleared
        the review gate and then asked for the merge.
        """

        for asking in (
            "please merge this pull request now",
            "run gh pr merge when done",
            "review it, then squash and merge",
        ):
            code, _, err = self._run("review", asking, {"Claude Code"})
            self.assertEqual(code, 3, asking)
            self.assertIn("asks for a merge", err, asking)

    def test_a_merge_prompt_may_of_course_ask_for_a_merge(self):
        code, out, _ = self._run("merge", "merge this pull request", {"Claude Code"})
        self.assertEqual(code, 0)
        self.assertIn("--always-approve", out)

    def test_main_uses_the_per_task_budget_it_was_not_given(self):
        """Hardcoding turns inside main() left every earlier test green."""

        _, review, _ = self._run("review", "review this", {"Claude Code"})
        self.assertIn(str(dispatch.TASK_TURNS["review"]), review.split())
        _, merge, _ = self._run("merge", "merge this pr", {"Claude Code"})
        self.assertIn(str(dispatch.TASK_TURNS["merge"]), merge.split())
        # And an explicit --turns still wins.
        _, pinned, _ = self._run(
            "review", "review this", {"Claude Code"}, extra=("--turns", "7")
        )
        self.assertIn("7", pinned.split())

    def _dispatch(self, stdout: bytes = b"", returncode: int = 0, *,
                  timeout: bool = False, prompt: str = "review this"):
        """Run main() to completion against a stubbed child process.

        Asserting on source text cannot show that main() calls anything. A
        delta review of #143 disconnected main() from the exhaustion detector
        and every test stayed green, so this drives the real function and reads
        its exit code.
        """

        import subprocess
        import tempfile

        class Completed:
            def __init__(self):
                self.stdout = stdout
                self.stderr = b""
                self.returncode = returncode

        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            if timeout:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return Completed()

        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "p.txt"
            path.write_text(prompt, encoding="utf-8")
            argv = [
                "--pr", "143", "--task", "review", "--provider", "grok",
                "--prompt-file", str(path),
            ]
            with unittest.mock.patch.object(
                dispatch, "pull_request_range", lambda pr: ("base", "head")
            ), unittest.mock.patch.object(
                dispatch, "material_authors", lambda base, head: {"Claude Code"}
            ), unittest.mock.patch.object(
                dispatch.shutil, "which", lambda name: name
            ), unittest.mock.patch.object(
                dispatch.subprocess, "run", fake_run
            ):
                import contextlib
                import io

                # main() writes the provider's output as bytes through
                # sys.stderr.buffer, so a plain StringIO is not a stand-in.
                class Text(io.StringIO):
                    def __init__(self):
                        super().__init__()
                        self.buffer = io.BytesIO()

                out, err = Text(), Text()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = dispatch.main(argv)
                return code, err.getvalue() + err.buffer.getvalue().decode(
                    "utf-8", errors="replace"
                ), captured

    def test_main_refuses_merge_imperatives_in_a_review(self):
        for prompt in (
            "go ahead and merge",
            "Merge the PR if CI is green",
            "please merge PR 143",
            "run `gh pr merge` when done",
            "do not merge it; then merge PR 143",
        ):
            with self.subTest(prompt=prompt):
                with unittest.mock.patch.object(dispatch, "build_command") as build:
                    code, err, captured = self._dispatch(prompt=prompt)
                self.assertEqual(code, 3)
                self.assertIn("asks for a merge", err)
                build.assert_not_called()
                self.assertEqual(captured, {})

    def test_main_dispatches_negation_and_quoted_merge_evidence(self):
        for prompt in (
            "do not merge it if tests fail",
            "(a review packet that quotes `gh pr merge` as evidence)",
            'The earlier instruction was "please merge PR 143". Review the refusal.',
            "Review this reproduction:\n```sh\ngh pr merge 143\n```",
            "Review this reproduction:\n    go ahead and merge\nExplain the defect.",
            "> Merge the PR if CI is green\nReview this quoted instruction.",
            "please don't merge the PR",
        ):
            with self.subTest(prompt=prompt):
                code, err, captured = self._dispatch(prompt=prompt)
                self.assertEqual(code, 0, err)
                self.assertNotIn("REFUSED", err)
                self.assertEqual(captured["kwargs"]["input"], prompt.encode("utf-8"))

    def test_main_reports_and_refuses_a_wall_clock_timeout(self):
        code, err, captured = self._dispatch(timeout=True)
        self.assertEqual(code, 5)
        self.assertIn("WALL-CLOCK TIMEOUT", err)
        self.assertEqual(captured["kwargs"]["timeout"], 3600)

    def test_main_reports_exhaustion_rather_than_passing_the_exit_code_through(self):
        """Disconnecting the detector from main() left every test green."""

        import json

        exhausted = json.dumps(
            {"stopReason": "cancelled", "num_turns": dispatch.TASK_TURNS["review"]}
        ).encode()
        code, err, _ = self._dispatch(exhausted)
        self.assertEqual(code, 4)
        self.assertIn("TURN BUDGET EXHAUSTED", err)
        self.assertIn("not a hang", err)

    def test_main_passes_a_completed_run_straight_through(self):
        import json

        finished = json.dumps({"stopReason": "end_turn", "num_turns": 12}).encode()
        code, err, _ = self._dispatch(finished, returncode=0)
        self.assertEqual(code, 0)
        self.assertNotIn("EXHAUSTED", err)

    def test_main_pipes_the_prompt_and_bounds_the_child(self):
        """The stdin pipe and the timeout must reach the actual call."""

        import json

        _, _, captured = self._dispatch(
            json.dumps({"stopReason": "end_turn", "num_turns": 3}).encode()
        )
        self.assertEqual(captured["kwargs"]["input"], b"review this")
        self.assertEqual(captured["kwargs"]["timeout"], 3600)
        # And the prompt text is nowhere in argv.
        self.assertNotIn("review this", captured["command"])

    def test_a_wall_clock_timeout_is_always_passed_to_the_child(self):
        """A turn cap bounds steps, not time, and codex gets no cap at all."""

        import inspect

        source = inspect.getsource(dispatch.main)
        self.assertIn("timeout=args.timeout", source)
        self.assertIn("TimeoutExpired", source)
        self.assertIn('"--timeout", type=int, default=3600', inspect.getsource(dispatch.main))


class AuthorshipTrailerTests(unittest.TestCase):
    def test_main_refuses_a_real_trailer_coauthor_before_building_a_command(self):
        import contextlib
        import io
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            repository = pathlib.Path(raw)

            def git(*arguments):
                return subprocess.run(
                    ["git", *arguments], cwd=repository, capture_output=True,
                    text=True, check=True,
                ).stdout.strip()

            git("init", "-q", "-b", "main")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            git("commit", "--allow-empty", "--no-gpg-sign", "-q", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("commit", "--allow-empty", "--no-gpg-sign", "-q", "-m",
                "[[Claude Code | Claude Opus 5 | 2026-09-09 | 00:00 UTC | BEGIN]]\n"
                "candidate\n"
                "[[Claude Code | Claude Opus 5 | 2026-09-09 | 00:00 UTC | END]]\n\n"
                "Co-Authored-By: OpenAI Codex <noreply@example.invalid>")
            head = git("rev-parse", "HEAD")
            prompt = repository / "p.txt"
            prompt.write_text("review this", encoding="utf-8")
            for task in ("review", "merge"):
                with self.subTest(task=task), contextlib.chdir(repository), \
                     unittest.mock.patch.object(dispatch, "pull_request_range", return_value=(base, head)), \
                     unittest.mock.patch.object(dispatch, "build_command") as build:
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = dispatch.main([
                            "--pr", "143", "--task", task, "--provider", "codex",
                            "--prompt-file", str(prompt), "--print-command",
                        ])
                    self.assertEqual(code, 2)
                    self.assertIn("REFUSED", err.getvalue())
                    self.assertIn("materially authored", err.getvalue())
                    self.assertIn("OpenAI Codex", err.getvalue())
                    self.assertEqual(out.getvalue(), "")
                    build.assert_not_called()

    def test_a_co_authored_by_trailer_counts_as_authorship(self):
        """Reading only the contribution tags missed a stated coauthor.

        A review of #143 built a commit carrying a Codex trailer and no Codex
        tag; is_eligible("codex", ...) returned True on it. The repository's own
        policy says tags never replace git provenance.
        """

        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            repository = pathlib.Path(raw)

            def git(*arguments):
                return subprocess.run(
                    ["git", *arguments], cwd=repository, capture_output=True, text=True
                )

            if git("init", "-q", "-b", "main").returncode != 0:
                self.skipTest("git is unavailable in this environment")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (repository / "a.txt").write_text("base", encoding="utf-8")
            git("add", "-A")
            git("commit", "--no-gpg-sign", "-q", "-m", "base")
            (repository / "a.txt").write_text("changed", encoding="utf-8")
            git("add", "-A")
            # A tag naming Claude, and a trailer naming Codex. The trailer is
            # the only record of the coauthor.
            message = "\n".join((
                "[[Claude Code | Claude Opus 5 | 2026-09-09 | 00:00 UTC | BEGIN]]",
                "",
                "subject",
                "",
                "[[Claude Code | Claude Opus 5 | 2026-09-09 | 00:00 UTC | END]]",
                "",
                "Co-Authored-By: OpenAI Codex <noreply@example.invalid>",
            ))
            git("commit", "--no-gpg-sign", "-q", "-m", message)

            import os

            cwd = os.getcwd()
            os.chdir(repository)
            try:
                authors = dispatch.material_authors("HEAD~1", "HEAD")
            finally:
                os.chdir(cwd)

        self.assertIn("Claude Code", authors)
        self.assertIn("OpenAI Codex", authors)
        eligible, reason = dispatch.is_eligible("codex", authors)
        self.assertFalse(eligible)
        self.assertIn("materially authored", reason)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 16:17 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:20 UTC | END]]
