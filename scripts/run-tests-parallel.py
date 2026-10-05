# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 15:14 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:47 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:43 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:03 UTC | BEGIN]]
"""Module-sharded unittest discovery; only PostgreSQL provisioning needs psycopg."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest


DSN_VARIABLE = "LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN"
# Eight shards retain at most this much stdout each. Past the cap the rest is
# discarded and the shard cannot pass.
OUTPUT_LIMIT = 1 << 20
_LIVE_PROCESSES = set()
_LIVE_LOCK = threading.Lock()


class RunnerError(RuntimeError):
    """Messages authored here contain no child output or connection details."""


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


class ModuleLoader(unittest.TestLoader):
    def __init__(self):
        super().__init__()
        self.modules = {}

    def loadTestsFromModule(self, module, *, pattern=None):
        suite = super().loadTestsFromModule(module, pattern=pattern)
        self.modules[module.__name__] = list(test_ids(suite))
        return suite


def discover_modules(start="tests"):
    # Match discover's default pattern AND implicit top-level directory (tests).
    serial_loader = unittest.TestLoader()
    serial = serial_loader.discover(start, pattern="test*.py")
    loader = ModuleLoader()
    discovered = loader.discover(start, pattern="test*.py")
    modules = {name: ids for name, ids in loader.modules.items() if ids}
    expected = Counter(test_ids(serial))
    reconstructed = unittest.TestLoader()
    loaded = reconstructed.loadTestsFromNames(list(modules))
    if serial_loader.errors or loader.errors or reconstructed.errors:
        raise RunnerError("Test discovery/import failed; use --serial for diagnosis")
    if (Counter(test_ids(discovered)) != expected
            or Counter(t for ids in modules.values() for t in ids) != expected
            or Counter(test_ids(loaded)) != expected):
        raise RunnerError("Discovery parity failed: module shards differ from serial discovery")
    if not expected:
        raise RunnerError("Discovery found no tests")
    return modules


def make_shards(modules, count, durations):
    shards = [[] for _ in range(min(count, len(modules)))]
    weights = [0.0] * len(shards)
    # Missing duration entries use the observed average seconds/test.
    known_tests = sum(len(modules[m]) for m in modules if m in durations)
    scale = sum(durations[m] for m in modules if m in durations) / known_tests if known_tests else 1
    costs = {m: durations.get(m, len(ids) * scale) for m, ids in modules.items()}
    for module in sorted(modules, key=lambda m: (-costs[m], m)):
        index = min(range(len(shards)), key=lambda i: (weights[i], len(shards[i]), i))
        shards[index].append(module)
        weights[index] += costs[module]
    return shards


def shard_dsn(dsn, index):
    from psycopg.conninfo import make_conninfo
    return make_conninfo(dsn, dbname=f"director_test_s{index}")


@contextmanager
def shard_databases(dsn, count):
    if not dsn:
        yield [None] * count
        return
    import psycopg
    from psycopg import sql

    created = []
    # Use exactly the supplied server; never drop pre-existing databases.
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as connection:
            connection.execute("SET statement_timeout = '30s'")
            for index in range(count):
                name = f"director_test_s{index}"
                connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
                created.append(name)
        yield [shard_dsn(dsn, i) for i in range(count)]
    finally:
        # The CI workflow always removes this owned, disposable container after
        # the test step. Dropping its shard databases first can hang for five
        # minutes even after every test passed.
        names_to_drop = () if (os.environ.get("GITHUB_ACTIONS") == "true"
                               and os.environ.get("DIRECTOR_CI_PG_LANE")) else reversed(created)
        deadline = time.monotonic() + 300
        failures = []
        # Worst case: about 300s total, plus one in-flight attempt's overhead.
        for name in names_to_drop:
            for attempt in range(3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failures.append(f"{name}: CleanupDeadline")
                    break
                remaining = max(1, int(remaining))
                try:
                    # Tests can terminate admin sessions. Reconnect for every
                    # drop/retry; IF EXISTS tolerates a lost successful reply.
                    with psycopg.connect(dsn, autocommit=True, connect_timeout=min(15, remaining)) as cleanup:
                        cleanup.execute(f"SET statement_timeout = '{min(120, remaining)}s'")
                        cleanup.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
                    break
                except Exception as error:
                    if attempt < 2 and isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
                        time.sleep(0.2)
                        continue
                    # Type only: libpq messages and server diagnostics can
                    # contain connection details or arbitrary user values.
                    failures.append(f"{name}: {type(error).__name__}")
                    break
        if failures:
            raise RunnerError("Shard database cleanup failed (" + "; ".join(failures)
                              + "); remove the disposable lane") from None


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait(timeout=5)
    stream = process.stdout
    if stream is not None and not stream.closed:
        try:
            stream.close()
        except OSError:
            pass


def register_process(process):
    with _LIVE_LOCK:
        _LIVE_PROCESSES.add(process)


def unregister_process(process):
    with _LIVE_LOCK:
        _LIVE_PROCESSES.discard(process)


def stop_registered_processes():
    with _LIVE_LOCK:
        processes = list(_LIVE_PROCESSES)
    for process in processes:
        try:
            stop_process(process)
        except (OSError, subprocess.TimeoutExpired):
            pass
        unregister_process(process)


def collect_output(process, timeout, limit=None):
    """Drain stdout in chunks. Retain at most `limit` bytes; keep draining."""
    cap = OUTPUT_LIMIT if limit is None else limit
    state = {"retained": bytearray(), "truncated": False}

    def drain():
        retained = bytearray()
        truncated = False
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                if truncated:
                    continue
                room = cap - len(retained)
                if len(chunk) <= room:
                    retained.extend(chunk)
                else:
                    if room > 0:
                        retained.extend(chunk[:room])
                    truncated = True
        except (OSError, ValueError):
            truncated = True
        state["retained"] = retained
        state["truncated"] = truncated

    reader = threading.Thread(target=drain)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_process(process)
    reader.join(timeout=30)
    if reader.is_alive():
        try:
            process.stdout.close()
        except OSError:
            pass
        reader.join(timeout=5)
        state["truncated"] = True
    retained = bytes(state["retained"])
    text = retained.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text, state["truncated"], timed_out, len(retained)


def parse_output(output):
    counts = dict(tests=0, failures=0, errors=0, skips=0)
    ran = re.findall(r"^Ran (\d+) tests? in .+$", output, re.MULTILINE)
    summaries = re.findall(r"^(?:OK|FAILED)(?: \(([^\n]*)\))?$", output, re.MULTILINE)
    if ran:
        counts["tests"] = int(ran[-1])
    if summaries:
        fields = dict(re.findall(r"(\w+)=(\d+)", summaries[-1]))
        for key, field in (("failures", "failures"), ("errors", "errors"), ("skips", "skipped")):
            counts[key] = int(fields.get(field, 0))
    # Emit identifiers only: tracebacks, assertion values and subtest parameters
    # can contain credentials or database rows. Keep child output in memory.
    ids = re.findall(r"^(FAIL|ERROR): ([\w.]+) \(([\w.]+)\)", output, re.MULTILINE)
    failures = [(kind, identity) for kind, _, identity in ids]
    durations = re.findall(r"^(\d+\.\d+)s[ \t]+([\w.]+) \(([\w.]+)\)$", output, re.MULTILINE)
    return counts, failures, durations, bool(ran and summaries)


def sanitized_tail(output):
    # Allow only unittest structure and traceback locations. Arbitrary log,
    # source and assertion lines can contain secrets or database rows.
    lines = []
    for line in output.splitlines()[-60:]:
        match = re.match(r"^(?:FAIL|ERROR): [\w.]+ \([\w.]+\)", line)
        progress = re.match(r"^[\w.]+ \([\w.]+\) \.\.\. (?:ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b", line)
        frame = re.fullmatch(r'  File "[^"\n]*[/\\]([\w.-]+\.py)", line (\d+), in ([\w<>]+)', line)
        error = re.match(r"^([\w.]*(?:Error|Exception|Interrupt|Exit))(?::|$)", line)
        if match or progress:
            lines.append((match or progress).group())
        elif frame:
            lines.append(f"  File {frame[1]}, line {frame[2]}, in {frame[3]}")
        elif error:
            lines.append(error[1] + ": [message redacted]")
        elif re.fullmatch(r"(?:Traceback \(most recent call last\):|[=\-]+|Ran \d+ tests? in [\d.]+s|(?:OK|FAILED)(?: \((?:(?:failures|errors|skipped|expected failures|unexpected successes)=\d+)(?:, (?:failures|errors|skipped|expected failures|unexpected successes)=\d+)*\))?|)", line):
            lines.append(line)
        else:
            lines.append("[output redacted]")
    return lines


def run_shard(index, modules, dsn, timeout, record, start="tests", expected_count=None):
    with tempfile.TemporaryDirectory(prefix=f"director-s{index}-") as directory:
        environment = os.environ.copy()
        environment.update(TMP=directory, TEMP=directory, TMPDIR=directory,
                           SQLITE_PATH=str(Path(directory) / "director.sqlite3"),
                           DIRECTOR_TEST_SHARD=str(index), PYTHONUNBUFFERED="1")
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(Path(start).resolve()), str(Path.cwd()), environment.get("PYTHONPATH", "")])
        if dsn:
            environment[DSN_VARIABLE] = dsn
        command = [sys.executable, "-m", "unittest", "-v"]
        if record:
            command += ["--durations", "0"]
        command += modules
        try:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
        except OSError:
            return dict(index=index, counts=dict(tests=0, failures=0, errors=0, skips=0),
                        failures=[], durations=[], progress=[], status="launch failed", failed=True,
                        output_bytes=0)
        register_process(process)
        timed_out = False
        truncated = False
        output = ""
        output_bytes = 0
        try:
            try:
                output, truncated, timed_out, output_bytes = collect_output(process, timeout)
            except BaseException:
                stop_process(process)
                raise
        finally:
            unregister_process(process)
            if process.poll() is None:
                stop_process(process)
            stream = process.stdout
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        counts, failures, durations, complete = parse_output(output)
        progress = re.findall(r"^[\w.]+ \(([\w.]+)\) \.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)", output, re.MULTILINE)
        if timed_out:
            status = "timeout"
        elif truncated:
            status = "output truncated"
        elif complete:
            status = "complete"
        else:
            status = "crash/incomplete"
        failed = timed_out or truncated or not complete or process.returncode != 0 or counts["failures"] or counts["errors"]
        fixture_skip = re.search(r"^setUp(?:Class|Module) \([\w.]+\) \.\.\. skipped", output, re.MULTILINE)
        if complete and not truncated and expected_count is not None and counts["tests"] != expected_count and not fixture_skip:
            status = "execution count differs from discovery"
            failed = True
        if not complete and not truncated:
            status += " (counts unavailable; zero contribution to totals)"
        return dict(index=index, counts=counts, failures=failures, durations=durations, progress=progress,
                    status=status, failed=bool(failed), tail=sanitized_tail(output) if failed else [],
                    output_bytes=output_bytes)


def execute_shards(shards, dsns, modules, timeout, record, start="tests"):
    """Run shards. On interrupt, stop their processes before the pool waits."""
    executor = ThreadPoolExecutor(max_workers=len(shards))
    try:
        futures = [executor.submit(run_shard, i, shard, dsns[i], timeout, record,
                                   start=start,
                                   expected_count=sum(len(modules[m]) for m in shard))
                   for i, shard in enumerate(shards)]
        return [future.result() for future in futures]
    finally:
        try:
            stop_registered_processes()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


def docs_only(paths):
    return bool(paths) and all(
        PurePosixPath(path).name not in {"AGENTS.md", "CLAUDE.md"}
        and (path.startswith("docs/") or path.endswith(".md") or path == "README.html")
        for path in paths
    )


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=positive_int,
                        default=os.environ.get("DIRECTOR_TEST_SHARDS", str(max(1, min(8, (os.cpu_count() or 1) // 4)))))
    parser.add_argument("--timeout", type=positive_int, default=1800, help="seconds per shard")
    parser.add_argument("--serial", action="store_true")
    parser.add_argument("--record-durations", action="store_true")
    parser.add_argument("--docs-only", nargs=2, metavar=("BASE", "HEAD"))
    args = parser.parse_args(argv)
    if args.docs_only:
        diff = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z",
                               "...".join(args.docs_only), "--"], check=True, capture_output=True)
        paths = [p for p in diff.stdout.decode("utf-8").split("\0") if p]
        print("docs_only=" + str(docs_only(paths)).lower())
        return 0
    if args.serial:
        return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    duration_path = Path("tests/.durations.json")
    durations = json.loads(duration_path.read_text(encoding="utf-8")) if duration_path.exists() else {}
    if not isinstance(durations, dict) or any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0
        for v in durations.values()
    ):
        raise RunnerError("Invalid module duration file")
    # `python -m unittest` places cwd on sys.path; a script starts in scripts/.
    sys.path.insert(0, str(Path.cwd()))
    # Import-time app state also needs a disposable location, before discovery.
    with tempfile.TemporaryDirectory(prefix="director-discovery-") as directory:
        original = os.environ.get("SQLITE_PATH")
        os.environ["SQLITE_PATH"] = str(Path(directory) / "discovery.sqlite3")
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                modules = discover_modules()
        finally:
            if original is None:
                os.environ.pop("SQLITE_PATH", None)
            else:
                os.environ["SQLITE_PATH"] = original
        shards = make_shards(modules, args.shards, durations)
        print(f"Discovery parity: {sum(map(len, modules.values()))} tests; {len(modules)} modules; {len(shards)} shards", flush=True)
        with shard_databases(os.environ.get(DSN_VARIABLE), len(shards)) as dsns:
            results = execute_shards(shards, dsns, modules, args.timeout, args.record_durations)
            total = Counter()
            measured = Counter()
            for result in results:
                index = result["index"]
                counts = result["counts"]
                total.update(counts)
                for identity, outcome in result["progress"]:
                    print(f"[s{index}] {identity} ... {outcome}")
                print(f"[s{index}] {result['status']}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
                for kind, identity in result["failures"]:
                    print(f"[s{index}] {kind}: {identity}")
                if result["failed"] and result.get("tail"):
                    print(f"[s{index}] stdout/stderr tail (sanitized, last 60 lines):")
                    for line in result["tail"]:
                        print(f"[s{index}] {line}")
                owners = {identity: module for module in shards[index] for identity in modules[module]}
                for seconds, _, identity in result["durations"]:
                    if identity in owners:
                        measured[owners[identity]] += float(seconds)
            print("TOTAL: " + ", ".join(f"{k}={v}" for k, v in total.items()), flush=True)
            failed = any(result["failed"] for result in results)
    if args.record_durations and not failed:
        duration_path.write_text(json.dumps(dict(sorted(measured.items())), indent=2) + "\n", encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RunnerError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Exception messages may include libpq credentials or child output.
        print(f"Parallel runner failed ({type(error).__name__}); discovery, execution or cleanup incomplete", file=sys.stderr)
        sys.exit(1)
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:43 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 15:14 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:01 UTC | END]]
