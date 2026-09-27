# [[OpenAI Codex | GPT-6 | 2026-09-27 | 16:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:03 UTC | BEGIN]]
"""Runner contracts using small disposable unittest modules and mocked DBs."""
from collections import Counter
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run-tests-parallel.py"
SPEC = importlib.util.spec_from_file_location("parallel_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.start = self.root / "tests"
        self.start.mkdir()

    def module(self, name, body):
        (self.start / (name + ".py")).write_text(body, encoding="utf-8")

    def test_discovery_parity_including_imported_classes_and_hook(self):
        self.module("test_parallel_a", "import unittest\nclass Example(unittest.TestCase):\n def test_ok(self): pass\n")
        self.module("test_parallel_b", "from test_parallel_a import Example\n")
        self.module("test_parallel_c", "import unittest\nfrom test_parallel_a import Example\ndef load_tests(loader, tests, pattern):\n return unittest.TestSuite([Example('test_ok')])\n")
        original_path = sys.path[:]
        try:
            serial = unittest.TestLoader().discover(str(self.start))
            modules = runner.discover_modules(str(self.start))
            self.assertEqual(Counter(runner.test_ids(serial)), Counter(t for ids in modules.values() for t in ids))
            self.assertEqual(3, sum(map(len, modules.values())))
        finally:
            sys.path[:] = original_path
            for name in ("test_parallel_a", "test_parallel_b", "test_parallel_c"):
                sys.modules.pop(name, None)

    def test_shards_are_complete_disjoint_and_duration_balanced(self):
        modules = {"a": ["a"] * 10, "b": ["b"] * 5, "c": ["c"] * 3, "d": ["d"]}
        for durations in ({}, {"a": 1, "b": 20, "c": 2, "d": 1}):
            shards = runner.make_shards(modules, 2, durations)
            self.assertEqual(Counter(modules.keys()), Counter(m for shard in shards for m in shard))
            self.assertTrue(all(shards))
        # Partial history estimates 4 seconds/test: a=40, b=20, c=12, d=4.
        cases = (
            ({}, [["a"], ["b", "c", "d"]]),
            ({"b": 20}, [["a"], ["b", "c", "d"]]),
            ({"a": 1, "b": 20, "c": 2, "d": 1}, [["b"], ["c", "a", "d"]]),
        )
        for durations, expected in cases:
            with self.subTest(durations=durations):
                self.assertEqual(expected, runner.make_shards(modules, 2, durations))
                self.assertEqual(expected, runner.make_shards(dict(reversed(list(modules.items()))), 2, durations))
        self.assertEqual([["a", "c"], ["b", "d"]],
                         runner.make_shards({m: [m] for m in "dcba"}, 2, {}))

    def invoke(self, body, *arguments):
        self.module("test_child", body)
        environment = os.environ.copy()
        environment.pop(runner.DSN_VARIABLE, None)
        environment.pop("DIRECTOR_TEST_SHARDS", None)
        return subprocess.run([sys.executable, str(SCRIPT), "--shards", "1", *arguments],
                              cwd=self.root, env=environment, capture_output=True,
                              text=True, timeout=30)

    def test_failure_names_test_and_returns_nonzero(self):
        result = self.invoke("import unittest\nclass Example(unittest.TestCase):\n def test_bad(self): self.fail('private assertion value')\n")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("[s0] FAIL: test_child.Example.test_bad", result.stdout)
        self.assertIn("failures=1", result.stdout)
        self.assertIn("stdout/stderr tail", result.stdout)
        self.assertIn("AssertionError: [message redacted]", result.stdout)
        self.assertNotIn("private assertion value", result.stdout + result.stderr)

    def test_crash_and_timeout_return_nonzero(self):
        for body, args, status in (
            ("import os; os._exit(9)", (), "crash/incomplete"),
            ("import time; time.sleep(10)", ("--timeout", "1"), "timeout"),
        ):
            with self.subTest(status=status):
                result = self.invoke("import unittest\nclass Example(unittest.TestCase):\n def test_child(self): " + body + "\n", *args)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(status, result.stdout)

    def test_success_skip_duration_and_isolation(self):
        result = self.invoke(
            "import unittest, os, tempfile\n"
            "class Example(unittest.TestCase):\n"
            " def test_state(self):\n"
            "  self.assertEqual('0', os.environ['DIRECTOR_TEST_SHARD'])\n"
            "  self.assertEqual(os.environ['TMP'], tempfile.gettempdir())\n"
            "  self.assertEqual(os.path.dirname(os.environ['SQLITE_PATH']), tempfile.gettempdir())\n"
            " @unittest.skip('fixture')\n"
            " def test_skip(self): pass\n", "--record-durations")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("tests=2, failures=0, errors=0, skips=1", result.stdout)
        measured = json.loads((self.start / ".durations.json").read_text(encoding="utf-8"))
        self.assertEqual({"test_child"}, set(measured))
        self.assertIsInstance(measured["test_child"], float)
        self.assertGreaterEqual(measured["test_child"], 0)

    def test_duration_parser_accepts_unittest_padded_columns(self):
        _, _, durations, complete = runner.parse_output(
            "Slowest test durations\n"
            "----------------------------------------------------------------------\n"
            "0.375s     test_second (test_child.Example.test_second)\n"
            "0.125s     test_first (test_child.Example.test_first)\n"
            "0.000s     test_fast (test_child.Example.test_fast)\n"
            "\nRan 3 tests in 0.500s\n\nOK\n")
        self.assertTrue(complete)
        self.assertEqual([
            ("0.375", "test_second", "test_child.Example.test_second"),
            ("0.125", "test_first", "test_child.Example.test_first"),
            ("0.000", "test_fast", "test_child.Example.test_fast"),
        ], durations)

    def test_dsn_rewriting_preserves_other_fields(self):
        from psycopg.conninfo import conninfo_to_dict
        original = dict(dbname="fixture", host="localhost", port="12345", user="fixture", sslmode="disable")
        from psycopg.conninfo import make_conninfo
        for index in range(3):
            rewritten = conninfo_to_dict(runner.shard_dsn(make_conninfo(**original), index))
            self.assertEqual({**original, "dbname": f"director_test_s{index}"}, rewritten)

    def test_database_cleanup_on_failure_and_partial_setup(self):
        import psycopg
        for partial in (False, True):
            connection = MagicMock()
            cleanup = MagicMock()
            connection.execute.side_effect = [None, None, RuntimeError("refused")] if partial else None
            with patch.object(psycopg, "connect") as connect:
                connect.side_effect = [connection, cleanup, cleanup]
                connection.__enter__.return_value = connection
                cleanup.__enter__.return_value = cleanup
                with self.assertRaises(RuntimeError):
                    with runner.shard_databases("dbname=fixture", 2):
                        raise RuntimeError("shard failure")
            statements = [call.args[0].as_string() for call in cleanup.execute.call_args_list
                          if not isinstance(call.args[0], str)]
            self.assertTrue(any('DROP DATABASE IF EXISTS "director_test_s0"' in sql for sql in statements))
            if partial:
                self.assertFalse(any('DROP DATABASE IF EXISTS "director_test_s1"' in sql for sql in statements))
            else:
                self.assertTrue(any('DROP DATABASE IF EXISTS "director_test_s1"' in sql for sql in statements))
            self.assertEqual(3 if not partial else 2, connect.call_count)
            self.assertFalse(any("DROP" in str(call) for call in connection.execute.call_args_list))

    def test_discovery_failure_is_loud(self):
        self.module("test_parallel_broken", "raise ImportError('private diagnostic')\n")
        original_path = sys.path[:]
        try:
            with self.assertRaisesRegex(runner.RunnerError, "discovery/import failed"):
                runner.discover_modules(str(self.start))
        finally:
            sys.path[:] = original_path
            sys.modules.pop("test_parallel_broken", None)

    def test_count_preserving_identity_mismatch_is_rejected(self):
        self.module("test_parallel_hook", "import unittest\nclass Example(unittest.TestCase):\n def test_a(self): pass\n def test_b(self): pass\ncalls = 0\ndef load_tests(loader, tests, pattern):\n global calls\n calls += 1\n return unittest.TestSuite([Example('test_a' if calls == 1 else 'test_b')])\n")
        original_path = sys.path[:]
        try:
            with self.assertRaisesRegex(runner.RunnerError, "Discovery parity failed"):
                runner.discover_modules(str(self.start))
        finally:
            sys.path[:] = original_path
            sys.modules.pop("test_parallel_hook", None)

    def test_docs_classifier(self):
        self.assertTrue(runner.docs_only(["README.md", "docs/guide.md", "docs/schemas/example.json"]))
        for paths in ([], ["AGENTS.md"], ["CLAUDE.md"], ["docs/AGENTS.md"],
                      ["src/app.py"], ["README.md", "n8n/workflows/example.json"]):
            self.assertFalse(runner.docs_only(paths))

    def test_docs_diff_keeps_deleted_and_renamed_source_paths(self):
        output = io.StringIO()
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"src/old.py\0docs/new.md\0")) as run:
            with redirect_stdout(output):
                self.assertEqual(0, runner.main(["--docs-only", "a" * 40, "b" * 40]))
        self.assertEqual("docs_only=false\n", output.getvalue())
        self.assertEqual(["git", "diff", "--name-only", "--no-renames", "-z", "a" * 40 + "..." + "b" * 40, "--"], run.call_args.args[0])

    def test_error_and_subtest_identifiers_are_reported_without_values(self):
        counts, failures, _, complete = runner.parse_output(
            "ERROR: setUpClass (test_fixture.Example)\n"
            "FAIL: test_sub (test_fixture.Example.test_sub) (value='private')\n"
            "Ran 1 test in 0.01s\nFAILED (failures=1, errors=1, skipped=2)\n")
        self.assertTrue(complete)
        self.assertEqual(dict(tests=1, failures=1, errors=1, skips=2), counts)
        self.assertEqual([("ERROR", "test_fixture.Example"), ("FAIL", "test_fixture.Example.test_sub")], failures)

    def test_failed_database_drop_still_attempts_remaining_cleanup(self):
        import psycopg
        setup, broken, cleanup = (MagicMock() for _ in range(3))
        for connection in (setup, broken, cleanup):
            connection.__enter__.return_value = connection
        broken.execute.side_effect = [None, None, None, None, RuntimeError("private diagnostic")]
        with patch.object(psycopg, "connect") as connect:
            connect.side_effect = [setup, broken, cleanup]
            with self.assertRaisesRegex(runner.RunnerError, "cleanup failed") as caught:
                with runner.shard_databases("dbname=fixture", 2):
                    pass
        self.assertIn('DROP DATABASE IF EXISTS "director_test_s0"', cleanup.execute.call_args.args[0].as_string())
        self.assertIn("director_test_s1: RuntimeError", str(caught.exception))
        self.assertNotIn("private diagnostic", str(caught.exception))
        self.assertNotIn("dbname", str(caught.exception))

    def test_cleanup_retries_transient_drop_failure_once(self):
        import psycopg
        for error_type in (psycopg.errors.QueryCanceled, psycopg.OperationalError,
                           psycopg.errors.LockNotAvailable, psycopg.InterfaceError):
            for recover in (True, False):
                with self.subTest(error=error_type.__name__, recover=recover):
                    setup, broken, retry = (MagicMock() for _ in range(3))
                    for connection in (setup, broken, retry):
                        connection.__enter__.return_value = connection
                    broken.execute.side_effect = [None] * 4 + [error_type("private diagnostic")]
                    if not recover:
                        retry.execute.side_effect = [None] * 4 + [error_type("private diagnostic")]
                    with patch.object(psycopg, "connect", side_effect=[setup, broken, retry]) as connect, \
                            patch.object(runner.time, "sleep") as sleep:
                        def exercise():
                            with runner.shard_databases("dbname=fixture", 1):
                                setup.__exit__.assert_called_once()
                                self.assertEqual(1, connect.call_count)
                        if recover:
                            exercise()
                        else:
                            with self.assertRaises(runner.CleanupError) as caught:
                                exercise()
                            self.assertIn(f"director_test_s0: {error_type.__name__}", str(caught.exception))
                            self.assertNotIn("private diagnostic", str(caught.exception))
                        self.assertEqual(3, connect.call_count)
                        sleep.assert_called_once_with(0.2)
                    for connection in (broken, retry):
                        calls = connection.execute.call_args_list
                        self.assertEqual(5, len(calls))
                        self.assertEqual(("SET statement_timeout = '30s'",), calls[0].args)
                        self.assertEqual(("SET lock_timeout = '10s'",), calls[1].args)
                        self.assertEqual((
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = %s AND pid <> pg_backend_pid()", ("director_test_s0",)),
                            calls[2].args)
                        self.assertEqual(("SET statement_timeout = '5min'",), calls[3].args)
                        self.assertEqual('DROP DATABASE IF EXISTS "director_test_s0" WITH (FORCE)',
                                         calls[4].args[0].as_string())
                    self.assertEqual("SET statement_timeout = '30s'", setup.execute.call_args_list[0].args[0])

    def test_report_precedes_cleanup_failure_and_exit_classification(self):
        for failed in (False, True):
            for warn in (False, True):
                with self.subTest(failed=failed, warn=warn):
                    output = io.StringIO()
                    result = dict(index=0, counts=dict(tests=2, failures=int(failed), errors=0, skips=0),
                                  failures=[("FAIL", "test_a.Example.test_a")] if failed else [],
                                  progress=[("test_a.Example.test_a", "FAIL" if failed else "ok")],
                                  durations=[], status="complete", failed=failed,
                                  tail=["AssertionError: [message redacted]"] if failed else [])

                    @contextmanager
                    def databases(*args):
                        yield [None]
                        self.assertIn("TOTAL: tests=2", output.getvalue())
                        raise runner.CleanupError("Shard database cleanup failed")

                    original_path = sys.path[:]
                    try:
                        with patch.object(runner, "discover_modules", return_value={"test_a": ["a", "b"]}), \
                                patch.object(runner, "run_shard", return_value=result), \
                                patch.object(runner, "shard_databases", databases), \
                                redirect_stdout(output), redirect_stderr(output):
                            args = ["--shards", "1"] + (["--cleanup-failure-warn"] if warn else [])
                            self.assertEqual(0 if warn and not failed else 1, runner.main(args))
                    finally:
                        sys.path[:] = original_path
                    rendered = output.getvalue()
                    self.assertIn("[s0] complete: tests=2", rendered)
                    self.assertIn("[s0] test_a.Example.test_a ...", rendered)
                    classification = "TESTS FAILED" if failed else "TESTS PASSED"
                    level = "WARNING" if warn and not failed else "ERROR"
                    self.assertIn(f"{level}: {classification}; cleanup failed", rendered)
                    self.assertLess(rendered.index("TOTAL:"), rendered.index("cleanup failed"))
                    if failed:
                        self.assertIn("FAIL: test_a.Example.test_a", rendered)
                        self.assertIn("AssertionError: [message redacted]", rendered)

    def test_warn_mode_does_not_hide_setup_or_execution_failure(self):
        for stage in ("setup", "execution"):
            with self.subTest(stage=stage):
                @contextmanager
                def databases(*args):
                    try:
                        if stage == "setup":
                            raise RuntimeError("private setup diagnostic")
                        yield [None]
                    finally:
                        raise runner.CleanupError("Shard database cleanup failed")

                original_path = sys.path[:]
                try:
                    with patch.object(runner, "discover_modules", return_value={"test_a": ["a"]}), \
                            patch.object(runner, "run_shard", side_effect=RuntimeError("private execution diagnostic")), \
                            patch.object(runner, "shard_databases", databases), redirect_stdout(io.StringIO()):
                        with self.assertRaises(runner.CleanupError):
                            runner.main(["--shards", "1", "--cleanup-failure-warn"])
                finally:
                    sys.path[:] = original_path

    def test_tail_retains_locations_but_redacts_arbitrary_values(self):
        output = "old line\n" * 65 + (
            "FAIL: test_a (test_a.Example.test_a) (value='private')\n"
            "Traceback (most recent call last):\n"
            '  File "/private/location/test_a.py", line 12, in test_a\n'
            "    self.fail('private assertion value')\n"
            "AssertionError: private assertion value\n"
            "arbitrary database row\n"
            "Ran 1 test in 0.01s\nFAILED (failures=1)\n")
        tail = runner.sanitized_tail(output)
        self.assertEqual(60, len(tail))
        rendered = "\n".join(tail)
        self.assertNotIn("private", rendered)
        self.assertNotIn("database row", rendered)
        self.assertNotIn("old line", rendered)
        self.assertIn("File test_a.py, line 12, in test_a", rendered)
        self.assertIn("AssertionError: [message redacted]", rendered)
        self.assertIn("FAILED (failures=1)", rendered)

    def test_serial_escape_hatch_is_exact(self):
        with patch.object(runner.subprocess, "call", return_value=7) as call:
            self.assertEqual(7, runner.main(["--serial"]))
        call.assert_called_once_with([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:43 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 16:10 UTC | END]]
