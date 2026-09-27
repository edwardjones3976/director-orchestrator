# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | BEGIN]]
"""scripts/ci_local.py: review-lock/frozen transitions and the merge-base preflight."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_local_under_test", ROOT / "scripts/ci_local.py")
ci_local = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_local)

REPOSITORY = "edwardjones3976/director-orchestrator"
IDENTITY = "Test Author | fixture | 2026-09-27 | 10:00 UTC"
BODY = f"**[[{IDENTITY} | BEGIN]]**\nPull request body.\n**[[{IDENTITY} | END]]**"
EVIDENCE = "https://github.com/edwardjones3976/director-orchestrator/actions/runs/1"


def envelope(*lines: str) -> str:
    return "\n".join([f"[[{IDENTITY} | BEGIN]]", *lines, f"[[{IDENTITY} | END]]"]) + "\n"


def exception(previous: str, severity: str = "P1", evidence: str = EVIDENCE,
              reason: str = "Required gate failed on the frozen head.") -> str:
    return envelope(
        "Repair the frozen pull request.",
        f"Review-exception: {severity}",
        f"Previous-head: {previous}",
        f"Evidence: {evidence}",
        f"Reason: {reason}",
    )


def doc(*times: str) -> str:
    # The newest pair is outermost, as a revision records it.
    begins = [f"<!-- [[Test | fixture | 2026-09-27 | {time} UTC | BEGIN]] -->" for time in reversed(times)]
    ends = [f"<!-- [[Test | fixture | 2026-09-27 | {time} UTC | END]] -->" for time in times]
    return "\n".join([*begins, "Document text.", *(["Revision."] if len(times) > 1 else []), *ends]) + "\n"


def run(callable_, *args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = callable_(*args)
    return code, stdout.getvalue() + stderr.getvalue()


class FixtureRepository(unittest.TestCase):
    """main: M0 -> M1 (unrelated doc revision); PR branch: M0 -> H1."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.repo = Path(self._temp.name)
        self._cwd = Path.cwd()
        os.chdir(self.repo)
        self.git("init", "--quiet", "-b", "main")
        self.git("remote", "add", "origin", "https://mirror.example.invalid/director-orchestrator.git")
        self.git("remote", "add", "github", f"https://github.com/{REPOSITORY}.git")
        self.m0 = self.commit(None, envelope("Seed."), {"doc.md": doc("09:00"), "seed.fixture": "seed\n"})
        self.m1 = self.commit(self.m0, envelope("Revise the document."), {"doc.md": doc("09:00", "09:30")})
        self.h1 = self.commit(self.m0, envelope("Add the feature."), {"feature.fixture": "one\n"})

    def tearDown(self):
        os.chdir(self._cwd)
        self._temp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
             "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.decode("utf-8")

    def commit(self, parent, message: str, files: dict[str, str]) -> str:
        if parent is not None:
            self.git("checkout", "--quiet", "--detach", parent)
        for name, text in files.items():
            (self.repo / name).write_text(text, encoding="utf-8")
            self.git("add", name)
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def pull(self, head: str, labels=(), base: str | None = None) -> dict:
        return {
            "number": 7,
            "state": "open",
            "body": BODY,
            "labels": [{"name": label} for label in labels],
            "base": {"sha": base or self.m1, "ref": "main", "repo": {"full_name": REPOSITORY}},
            "head": {"sha": head, "ref": "feature", "repo": {"full_name": REPOSITORY}},
        }

    def event_file(self, event: dict) -> Path:
        path = self.repo.parent / f"{self.repo.name}-event.json"
        path.write_text(json.dumps(event), encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def synchronize(self, before: str, after: str, labels=()) -> dict:
        return ci_local.build_event(self.pull(before, labels), before=before, after=after)

    def ci(self, event: dict):
        path = self.event_file(event)
        with patch.object(ci_local, "run_checker", wraps=ci_local.run_checker) as checker:
            code, output = run(ci_local.ci_main, path)
        return code, output, checker


class PullRequestEventTests(FixtureRepository):
    def test_attribution_uses_the_merge_base_and_keeps_the_event_base(self):
        event = {"action": "opened", "pull_request": self.pull(self.h1)}
        code, output, checker = self.ci(event)
        self.assertEqual(0, code, output)
        base, head, path = checker.call_args.args
        self.assertEqual((self.m0, self.h1), (base, head))
        self.assertEqual(self.m1, json.loads(path.read_text(encoding="utf-8"))["pull_request"]["base"]["sha"])
        self.assertIn(f"event-base: {self.m1}", output)
        self.assertIn(f"merge-base: {self.m0}", output)
        # The drift the merge base removes: the unrelated main revision would
        # otherwise read as this pull request reverting a tagged document.
        code, output = run(ci_local.run_checker, self.m1, self.h1, path)
        self.assertEqual(1, code, output)
        self.assertIn("doc.md", output)

    def test_unchanged_locked_head_and_newly_opened_frozen_pr_are_not_transitions(self):
        for event in (
            self.synchronize(self.h1, self.h1, ["review-lock", "frozen"]),
            {"action": "opened", "pull_request": self.pull(self.h1, ["frozen", "review-lock"])},
            {"action": "reopened", "pull_request": self.pull(self.h1, ["frozen"])},
        ):
            with self.subTest(action=event["action"]):
                self.assertEqual([], ci_local.transition_errors(event))
                code, output, _ = self.ci(event)
                self.assertEqual(0, code, output)

    def test_changed_head_under_review_lock_is_refused_before_attribution(self):
        h2 = self.commit(self.h1, exception(self.h1), {"feature.fixture": "two\n"})
        for labels in (["review-lock"], ["review-lock", "frozen"]):
            with self.subTest(labels=labels):
                code, output, checker = self.ci(self.synchronize(self.h1, h2, labels))
                self.assertEqual(1, code)
                self.assertIn("review-lock is present", output)
                checker.assert_not_called()

    def test_unlabelled_head_change_needs_no_exception(self):
        h2 = self.commit(self.h1, envelope("Ordinary follow-up."), {"feature.fixture": "two\n"})
        code, output, checker = self.ci(self.synchronize(self.h1, h2))
        self.assertEqual(0, code, output)
        checker.assert_called_once()

    def test_frozen_repair_with_a_valid_exception_passes(self):
        for severity, evidence in (("P0", EVIDENCE), ("required-gate", "receipt:ci-36286137586")):
            with self.subTest(severity=severity):
                h2 = self.commit(self.h1, exception(self.h1, severity, evidence), {"feature.fixture": severity})
                code, output, checker = self.ci(self.synchronize(self.h1, h2, ["frozen"]))
                self.assertEqual(0, code, output)
                self.assertEqual((self.m0, h2), checker.call_args.args[:2])

    def test_frozen_transition_rejections(self):
        def rejected(before, after, expected):
            errors = ci_local.transition_errors(self.synchronize(before, after, ["frozen"]))
            self.assertTrue(any(expected in error for error in errors), errors)

        cases = {
            "P2 severity": (exception(self.h1, "P2"), "must be P0, P1, or required-gate"),
            "stale previous head": (exception(self.m0), "is not the previous remote head"),
            "no exception": (envelope("Ordinary churn."), "lacks a Review-exception field"),
            "vague evidence": (exception(self.h1, evidence="see CI"), "Evidence must be"),
            "bare domain evidence": (exception(self.h1, evidence="https://github.com"), "Evidence must be"),
            "unbounded reason": (exception(self.h1, reason="x " * 200), "Reason must be"),
            "one-word reason": (exception(self.h1, reason="fix"), "Reason must be"),
            "missing envelope": (exception(self.h1).splitlines()[1] + "\n" + "\n".join(exception(self.h1).splitlines()[2:5]), "review exception commit:"),
            "fields outside envelope": (envelope("Repair.") + "Review-exception: P1\n", "review exception commit:"),
        }
        for name, (message, expected) in cases.items():
            with self.subTest(case=name):
                h2 = self.commit(self.h1, message, {"feature.fixture": name})
                rejected(self.h1, h2, expected)
        duplicated = exception(self.h1).replace("Reason:", "Review-exception: P0\nReason:")
        rejected(self.h1, self.commit(self.h1, duplicated, {"feature.fixture": "dup"}), "repeats Review-exception")

    def test_multi_commit_push_and_rewrite_cannot_hide_a_transition(self):
        hidden = self.commit(self.h1, envelope("Unapproved change."), {"feature.fixture": "hidden\n"})
        approved = self.commit(hidden, exception(self.h1), {"feature.fixture": "approved\n"})
        errors = ci_local.transition_errors(self.synchronize(self.h1, approved, ["frozen"]))
        self.assertTrue(any("introduces 2" in error for error in errors), errors)
        rewritten = self.commit(self.m0, exception(self.h1), {"feature.fixture": "rewritten\n"})
        errors = ci_local.transition_errors(self.synchronize(self.h1, rewritten, ["frozen"]))
        self.assertTrue(any("history was rewritten" in error for error in errors), errors)

    def test_missing_objects_and_malformed_events_fail_closed(self):
        absent = "b" * 40
        errors = ci_local.transition_errors(self.synchronize(self.h1, absent, ["frozen"]))
        self.assertTrue(any("not present locally" in error for error in errors), errors)
        code, output, _ = self.ci({"action": "opened", "pull_request": self.pull(absent)})
        self.assertEqual(1, code)
        self.assertIn("not present locally", output)
        bad = self.synchronize(self.h1, self.h1)
        bad["after"] = "HEAD"
        self.assertTrue(ci_local.transition_errors(bad))
        bad = self.synchronize(self.h1, self.m0)
        bad["pull_request"]["head"]["sha"] = self.h1
        self.assertTrue(ci_local.transition_errors(bad))
        missing = self.repo.parent / "absent-event.json"
        self.assertEqual(1, run(ci_local.ci_main, missing)[0])
        self.assertEqual(1, self.ci({"ref": "refs/heads/feature", "after": self.h1})[0])

    def test_head_commit_without_an_envelope_fails_attribution(self):
        untagged = self.commit(self.m0, "untagged change", {"feature.fixture": "untagged\n"})
        code, output, checker = self.ci({"action": "opened", "pull_request": self.pull(untagged)})
        self.assertEqual(1, code)
        self.assertIn("pull-request head commit", output)
        checker.assert_called_once()

    def test_main_push_keeps_its_exemption_and_range(self):
        event = {"ref": "refs/heads/main", "before": self.m0, "after": self.m1, "head_commit": {"message": "Merge #7"}}
        code, output, checker = self.ci(event)
        self.assertEqual(0, code, output)
        self.assertEqual((self.m0, self.m1), checker.call_args.args[:2])
        self.assertIn("mode: ci-main-push", output)

    def test_cli_reads_the_actual_event_path(self):
        path = self.event_file({"action": "opened", "pull_request": self.pull(self.h1)})
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(path)}):
            self.assertEqual(0, run(ci_local.main, ["--ci"])[0])
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": ""}), self.assertRaises(SystemExit):
            run(ci_local.main, ["--ci"])


class LocalPreflightTests(FixtureRepository):
    def local(self, pull, *args: str):
        with patch.object(ci_local, "fetch_pull", return_value=pull):
            return run(ci_local.main, ["--pr", "7", *args])

    def test_pushed_pr_mode_reports_the_exact_range(self):
        code, output = self.local(self.pull(self.h1, ["review-lock"]))
        self.assertEqual(0, code, output)
        for line in ("mode: pushed-pr", "remote: github", f"event-base: {self.m1}",
                     f"merge-base: {self.m0}", f"head: {self.h1}", "head-change: none"):
            self.assertIn(line, output)

    def test_candidate_mode_compares_the_local_sha_with_the_remote_head(self):
        self.git("checkout", "--quiet", "--detach", self.h1)
        code, output = self.local(self.pull(self.h1, ["review-lock"]), "--candidate", "HEAD")
        self.assertEqual(0, code, output)
        self.assertIn("mode: pre-push-candidate", output)
        self.commit(self.h1, exception(self.h1), {"feature.fixture": "two\n"})
        code, output = self.local(self.pull(self.h1, ["review-lock"]), "--candidate", "HEAD")
        self.assertEqual(1, code)
        self.assertIn("review-lock is present", output)
        kept = self.repo.parent / f"{self.repo.name}-kept.json"
        self.addCleanup(kept.unlink, missing_ok=True)
        code, output = self.local(self.pull(self.h1, ["frozen"]), "--candidate", "HEAD", "--event-out", str(kept))
        self.assertEqual(0, code, output)
        event = json.loads(kept.read_text(encoding="utf-8"))
        self.assertEqual((self.h1, self.m1), (event["before"], event["pull_request"]["base"]["sha"]))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), event["pull_request"]["head"]["sha"])

    def test_api_payload_and_remote_identity_fail_closed(self):
        def mutate(change):
            pull = self.pull(self.h1)
            change(pull)
            return pull

        cases = {
            "lacks head.sha": mutate(lambda p: p["head"].pop("sha")),
            "lacks labels": mutate(lambda p: p.pop("labels")),
            "lacks body": mutate(lambda p: p.pop("body")),
            "is not edwardjones3976": mutate(lambda p: p["head"]["repo"].update(full_name="fork/director-orchestrator")),
            "is not open": mutate(lambda p: p.update(state="closed")),
            "is not PR #7": mutate(lambda p: p.update(number=8)),
            "not a full commit SHA": mutate(lambda p: p["base"].update(sha="main")),
            "not present locally": self.pull("c" * 40),
        }
        for expected, pull in cases.items():
            with self.subTest(expected=expected):
                code, output = self.local(pull)
                self.assertEqual(1, code)
                self.assertIn(expected, output)
        code, output = self.local(self.pull(self.h1), "--remote", "origin")
        self.assertEqual(1, code)
        self.assertIn("is not " + REPOSITORY, output)
        self.git("remote", "remove", "github")
        code, output = self.local(self.pull(self.h1))
        self.assertEqual(1, code)
        self.assertIn("no local remote", output)

    def test_gh_api_failures_are_preflight_errors(self):
        def runner(stdout=b"", returncode=0, error=None):
            def call(*args, **kwargs):
                if error:
                    raise error
                return subprocess.CompletedProcess(args, returncode, stdout, b"")
            return call

        for fake, expected in (
            (runner(returncode=1), "exited with status 1"),
            (runner(stdout=b"not json"), "invalid JSON"),
            (runner(error=FileNotFoundError("gh")), "could not run gh api"),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(ci_local.PreflightError, expected):
                ci_local.fetch_pull(7, runner=fake)
        seen = []
        ci_local.fetch_pull(7, runner=lambda command, **kwargs: seen.append(command) or subprocess.CompletedProcess(command, 0, b"{}", b""))
        self.assertEqual([["gh", "api", f"repos/{REPOSITORY}/pulls/7"]], seen)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | END]]
