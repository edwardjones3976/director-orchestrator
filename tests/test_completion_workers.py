# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 02:27 UTC | BEGIN]]
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from director_orchestrator.completion_gates import domain_digest
from director_orchestrator.completion_worker_backends import (
    AuditedHostWorkerBackend,
    DisposableContainerWorkerBackend,
    ExecutableBinding,
    SanitizedWorkerOutput,
    _git_environment,
    _repository_state,
    _remove_snapshot,
    _start_cat_file_batch,
    _tracked_repository_snapshot,
)
from director_orchestrator.process_isolation import trusted_neutral_cwd
from director_orchestrator.completion_workers import (
    AuditedHostProfile,
    CompletionWorkerError,
    CompletionWorkerExecutor,
    CompletionWorkerRegistry,
    CompletionWorkerRequest,
    CompletionWorkerStore,
    DisposableContainerSpec,
    WorkerBackendCapabilities,
    WorkerBackendEvidence,
    WorkerIdentityConflict,
    WorkerJobStatus,
    WorkerTier,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
class StaticSanitizer:
    def sanitize(self, stdout: bytes, stderr: bytes) -> SanitizedWorkerOutput:
        payload = stdout + b"\0" + stderr
        return SanitizedWorkerOutput(
            decisive_output="sanitized",
            output_digest=domain_digest(
                "director-completion-worker-sanitized-output/v1",
                payload.hex(),
            ),
            redaction_passed=True,
            secret_scan_passed=True,
        )


class RecordingBackend:
    def __init__(
        self,
        tier: WorkerTier,
        *,
        raise_error: bool = False,
        mismatched_sha: bool = False,
        process_tree_terminated: bool = True,
        capabilities: WorkerBackendCapabilities | None = None,
    ) -> None:
        self.calls = 0
        self.raise_error = raise_error
        self.mismatched_sha = mismatched_sha
        self.process_tree_terminated = process_tree_terminated
        self._capabilities = capabilities or WorkerBackendCapabilities(
            tier=tier,
            low_privilege_identity=True,
            network_isolation=True,
            resource_limits=True,
            process_tree_termination=True,
            read_only_repository=True,
            disposable_filesystem=(tier == WorkerTier.DISPOSABLE_CONTAINER),
        )

    @property
    def capabilities(self) -> WorkerBackendCapabilities:
        return self._capabilities

    def execute(self, spec, request, *, lease_token):
        self.calls += 1
        if self.raise_error:
            raise TimeoutError("backend outcome unknown")
        return WorkerBackendEvidence(
            job_id=request.job_id,
            lease_digest=domain_digest(
                "director-completion-worker-lease/v1", lease_token
            ),
            tier=spec.tier,
            runner_registry_digest=request.runner_registry_digest,
            worker_registry_digest=request.worker_registry_digest,
            worker_spec_digest=request.worker_spec_digest,
            snapshot_manifest_digest=domain_digest(
                "director-completion-worker-snapshot-manifest/v1",
                {"head_sha": request.expected_head_sha, "entries": []},
            ),
            snapshot_file_count=0,
            before_head_sha=("2" * 40 if self.mismatched_sha else request.expected_head_sha),
            after_head_sha=request.expected_head_sha,
            before_clean=True,
            after_clean=True,
            exit_code=spec.expected_exit_code,
            timed_out=False,
            output_limit_exceeded=False,
            sanitized_output_digest=DIGEST_C,
            decisive_output="OK",
            redaction_passed=True,
            secret_scan_passed=True,
            network_isolated=True,
            resource_limits_applied=True,
            low_privilege_identity_applied=True,
            process_tree_terminated=self.process_tree_terminated,
            repository_read_only=True,
            disposable_filesystem=(spec.tier == WorkerTier.DISPOSABLE_CONTAINER),
        )


class CompletionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.repository, check=True)
        subprocess.run(
            ("git", "config", "user.email", "completion@example.invalid"),
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ("git", "config", "user.name", "Completion Worker Test"),
            cwd=self.repository,
            check=True,
        )
        (self.repository / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(("git", "add", "fixture.txt"), cwd=self.repository, check=True)
        subprocess.run(
            ("git", "commit", "-q", "-m", "fixture"),
            cwd=self.repository,
            check=True,
        )
        self.head = subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=self.repository, text=True
        ).strip()
        self.host = AuditedHostProfile.build(
            spec_id="host.compile",
            runner_id="python_compileall",
            registry_digest=DIGEST_A,
            runner_definition_digest=DIGEST_B,
            parameter_profile_id="compile-src",
            parameter_profile_digest=DIGEST_C,
            launcher_profile_id="director-low-privilege-python",
            fixed_argv=("python", "-m", "compileall", "-q", "src"),
        )
        self.container = DisposableContainerSpec.build(
            spec_id="container.tests",
            runner_id="python_unittest",
            registry_digest=DIGEST_A,
            runner_definition_digest=DIGEST_B,
            parameter_profile_id="unit-tests",
            parameter_profile_digest=DIGEST_C,
            image="registry.example/director-worker@sha256:" + "d" * 64,
            fixed_argv=("python", "-m", "unittest", "discover", "-s", "tests"),
        )
        self.registry = CompletionWorkerRegistry((self.host, self.container))
        self.store = CompletionWorkerStore(str(self.root / "workers.sqlite3"))

    def request(self, **changes) -> CompletionWorkerRequest:
        values = {
            "job_id": "worker-job-1",
            "idempotency_key": "task:assignment:gate:attempt-1",
            "task_id": "DIR-TEST",
            "assignment_id": "DIR-TEST-1",
            "gate_id": "director.tests",
            "ledger_digest": DIGEST_B,
            "expected_head_sha": self.head,
            "runner_registry_digest": self.registry.runner_registry_digest,
            "worker_registry_digest": self.registry.registry_digest,
            "worker_spec_id": self.host.spec_id,
            "worker_spec_digest": self.host.spec_digest,
            "repository_root": str(self.repository.resolve()),
        }
        values.update(changes)
        return CompletionWorkerRequest(**values)

    def executor(self, host=None, container=None, **kwargs) -> CompletionWorkerExecutor:
        return CompletionWorkerExecutor(
            self.registry,
            self.store,
            {
                WorkerTier.AUDITED_HOST: host or RecordingBackend(WorkerTier.AUDITED_HOST),
                WorkerTier.DISPOSABLE_CONTAINER: container
                or RecordingBackend(WorkerTier.DISPOSABLE_CONTAINER),
            },
            enabled=True,
            **kwargs,
        )

    def test_registry_rejects_unpinned_container_and_mixed_runner_registries(self):
        with self.assertRaisesRegex(CompletionWorkerError, "pinned"):
            DisposableContainerSpec.build(
                spec_id="bad",
                runner_id="python_unittest",
                registry_digest=DIGEST_A,
                runner_definition_digest=DIGEST_B,
                parameter_profile_id="tests",
                parameter_profile_digest=DIGEST_C,
                image="python:3.12",
                fixed_argv=("python", "-V"),
            )
        other = replace(self.host, spec_id="other", registry_digest=DIGEST_B)
        with self.assertRaisesRegex(CompletionWorkerError, "one runner registry"):
            CompletionWorkerRegistry((self.host, other))

    def test_readiness_requires_both_tiers_and_an_isolated_store(self):
        disabled = CompletionWorkerExecutor(
            self.registry,
            self.store,
            {
                WorkerTier.AUDITED_HOST: RecordingBackend(WorkerTier.AUDITED_HOST),
                WorkerTier.DISPOSABLE_CONTAINER: RecordingBackend(
                    WorkerTier.DISPOSABLE_CONTAINER
                ),
            },
        ).readiness()
        self.assertFalse(disabled["ready"])
        self.assertIn("worker_execution_disabled", disabled["reasons"])

        incomplete = CompletionWorkerExecutor(
            self.registry,
            self.store,
            {WorkerTier.AUDITED_HOST: RecordingBackend(WorkerTier.AUDITED_HOST)},
            enabled=True,
        ).readiness()
        self.assertFalse(incomplete["ready"])
        self.assertIn("disposable_container_backend_not_ready", incomplete["reasons"])
        self.assertFalse(incomplete["completion_authority"])

        colliding = self.executor(
            reserved_database_paths=(self.store.path,)
        ).readiness()
        self.assertFalse(colliding["ready"])
        self.assertIn("worker_store_not_isolated", colliding["reasons"])

    def test_success_is_durable_and_redelivery_does_not_execute_again(self):
        backend = RecordingBackend(WorkerTier.AUDITED_HOST)
        executor = self.executor(host=backend)

        first = executor.execute(self.request())
        replay = executor.execute(self.request())

        self.assertEqual(WorkerJobStatus.SUCCEEDED, first.status)
        self.assertEqual(WorkerJobStatus.SUCCEEDED, replay.status)
        self.assertTrue(replay.replayed)
        self.assertEqual(1, backend.calls)

    def test_idempotency_reuse_with_different_work_fails_closed(self):
        executor = self.executor()
        executor.execute(self.request())
        with self.assertRaises(WorkerIdentityConflict):
            executor.execute(self.request(assignment_id="DIR-TEST-2"))

    def test_job_id_reuse_with_a_new_idempotency_key_is_a_typed_conflict(self):
        executor = self.executor()
        executor.execute(self.request())
        with self.assertRaisesRegex(WorkerIdentityConflict, "job id"):
            executor.execute(self.request(idempotency_key="different-key"))

    def test_backend_exception_requires_reconciliation_and_never_redispatches(self):
        backend = RecordingBackend(WorkerTier.AUDITED_HOST, raise_error=True)
        executor = self.executor(host=backend)

        first = executor.execute(self.request())
        replay = executor.execute(self.request())

        self.assertEqual(WorkerJobStatus.RECONCILIATION_REQUIRED, first.status)
        self.assertEqual(WorkerJobStatus.RECONCILIATION_REQUIRED, replay.status)
        self.assertIn("will not be dispatched again", replay.reason)
        self.assertEqual(1, backend.calls)

    def test_stranded_running_job_is_recovered_as_readback_only(self):
        request = self.request()
        self.store.claim(request)
        self.assertIsNotNone(self.store.acquire(request))
        backend = RecordingBackend(WorkerTier.AUDITED_HOST)

        outcome = self.executor(host=backend).execute(request)

        self.assertEqual(WorkerJobStatus.RECONCILIATION_REQUIRED, outcome.status)
        self.assertEqual(0, backend.calls)

    def test_exact_sha_or_isolation_evidence_cannot_pass(self):
        backend = RecordingBackend(WorkerTier.AUDITED_HOST, mismatched_sha=True)
        outcome = self.executor(host=backend).execute(self.request())
        self.assertEqual(WorkerJobStatus.FAILED, outcome.status)
        self.assertIn("exact binding", outcome.reason)

        missing_termination = RecordingBackend(
            WorkerTier.AUDITED_HOST,
            process_tree_terminated=False,
        )
        outcome = self.executor(host=missing_termination).execute(
            self.request(
                job_id="worker-job-2",
                idempotency_key="task:assignment:gate:attempt-2",
            )
        )
        self.assertEqual(WorkerJobStatus.FAILED, outcome.status)
        self.assertIn("isolation", outcome.reason)

    def test_container_command_is_pinned_fixed_and_deny_by_default(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        request = self.request(
            worker_spec_id=self.container.spec_id,
            worker_spec_digest=self.container.spec_digest,
        )
        with _tracked_repository_snapshot(
            self.repository, self.head, deadline=time.monotonic() + 30
        ) as snapshot:
            command = backend.command(self.container, request, snapshot)
            mount = next(
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "--mount"
            )
            self.assertIn(str(snapshot.root), mount)
            self.assertNotIn(str(self.repository.resolve()), mount)
        self.assertEqual(Path(sys.executable).resolve(), Path(command[0]).resolve())
        for required in (
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "no-new-privileges", "--pids-limit", "--memory", "--cpus",
            "--user", "--mount", self.container.image,
        ):
            self.assertIn(required, command)
        self.assertEqual(self.container.fixed_argv, command[-len(self.container.fixed_argv):])
        self.assertNotIn("-c", command[: command.index(self.container.image)])
        self.assertIn("PYTHONPATH=/workspace/src", command)
        self.assertIn("PYTHONPYCACHEPREFIX=/tmp/pycache", command)
        expected_cwd = (
            Path(os.environ["SYSTEMROOT"], "System32") if os.name == "nt" else Path("/")
        )
        self.assertEqual(expected_cwd, backend._trusted_cwd())

    def test_container_compileall_cannot_load_repository_startup_modules(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        isolated_compileall = DisposableContainerSpec.build(
            spec_id="container.compile",
            runner_id="python_compileall",
            registry_digest=DIGEST_A,
            runner_definition_digest=DIGEST_B,
            parameter_profile_id="compile-src",
            parameter_profile_digest=DIGEST_C,
            image="registry.example/director-worker@sha256:" + "e" * 64,
            fixed_argv=(
                "python", "-I", "-X", "pycache_prefix=/tmp/pycache",
                "-m", "compileall", "-q", "src", "tests",
            ),
        )
        request = self.request(
            worker_spec_id=isolated_compileall.spec_id,
            worker_spec_digest=isolated_compileall.spec_digest,
        )
        with _tracked_repository_snapshot(
            self.repository, self.head, deadline=time.monotonic() + 30
        ) as snapshot:
            command = backend.command(isolated_compileall, request, snapshot)
        self.assertNotIn("PYTHONPATH=/workspace/src", command)
        self.assertNotIn("PYTHONPYCACHEPREFIX=/tmp/pycache", command)
        self.assertEqual(
            isolated_compileall.fixed_argv,
            command[-len(isolated_compileall.fixed_argv):],
        )

        unsafe_compileall = replace(
            isolated_compileall,
            fixed_argv=("python", "-m", "compileall", "-q", "src", "tests"),
        )
        with _tracked_repository_snapshot(
            self.repository, self.head, deadline=time.monotonic() + 30
        ) as snapshot:
            with self.assertRaisesRegex(CompletionWorkerError, "isolated Python"):
                backend.command(unsafe_compileall, request, snapshot)

    def test_isolated_compileall_uses_external_pycache_without_loading_shadow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "src"
            pycache = root / "cache"
            marker = root / "shadow-executed.txt"
            source.mkdir()
            (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            shadow = f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n"
            (source / "compileall.py").write_text(shadow, encoding="utf-8")
            (source / "sitecustomize.py").write_text(shadow, encoding="utf-8")

            result = subprocess.run(
                (
                    sys.executable,
                    "-I",
                    "-X",
                    f"pycache_prefix={pycache}",
                    "-m",
                    "compileall",
                    "-q",
                    "src",
                ),
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(any(source.rglob("__pycache__")))
            self.assertTrue(any(pycache.rglob("*.pyc")))

    def test_host_contract_supplies_pythonpath_and_uses_neutral_cwd(self):
        backend = AuditedHostWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        with _tracked_repository_snapshot(
            self.repository, self.head, deadline=time.monotonic() + 30
        ) as snapshot:
            command = backend.command(self.host, self.request(), snapshot)
            self.assertIn("--env", command)
            pythonpath = next(
                item.removeprefix("PYTHONPATH=")
                for item in command
                if item.startswith("PYTHONPATH=")
            )
            self.assertEqual((snapshot.root / "src").resolve(), Path(pythonpath).resolve())
            self.assertNotEqual(
                (self.repository / "src").resolve(), Path(pythonpath).resolve()
            )
        expected_cwd = (
            Path(os.environ["SYSTEMROOT"], "System32") if os.name == "nt" else Path("/")
        )
        self.assertEqual(expected_cwd, backend._trusted_cwd())
        self.assertNotEqual(self.repository.resolve(), backend._trusted_cwd())

    def test_tracked_snapshot_excludes_ignored_secret_and_binds_manifest(self):
        (self.repository / ".gitignore").write_text(".env.secret\n", encoding="utf-8")
        nested = self.repository / "src" / "nested"
        nested.mkdir(parents=True)
        (nested / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(
            ("git", "add", ".gitignore", "src/nested/module.py"),
            cwd=self.repository,
            check=True,
        )
        subprocess.run(("git", "commit", "-q", "-m", "ignore secret"), cwd=self.repository, check=True)
        head = subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=self.repository, text=True
        ).strip()
        (self.repository / ".env.secret").write_text(
            "PERPLEXITY_API_KEY=never-copy-this\n", encoding="utf-8"
        )

        snapshot_path: Path | None = None
        with patch(
            "director_orchestrator.completion_worker_backends._start_cat_file_batch",
            wraps=_start_cat_file_batch,
        ) as batch_start:
            with _tracked_repository_snapshot(
                self.repository, head, deadline=time.monotonic() + 30
            ) as snapshot:
                snapshot_path = snapshot.root
                self.assertFalse((snapshot.root / ".env.secret").exists())
                self.assertEqual("fixture\n", (snapshot.root / "fixture.txt").read_text())
                self.assertEqual(
                    "VALUE = 1\n",
                    (snapshot.root / "src" / "nested" / "module.py").read_text(),
                )
                self.assertEqual(3, snapshot.file_count)
                self.assertRegex(snapshot.manifest_digest, r"^sha256:[0-9a-f]{64}$")
                self.assertNotEqual(self.repository.resolve(), snapshot.root)
                self.assertNotIn(self.repository.resolve(), snapshot.root.parents)
        self.assertEqual(1, batch_start.call_count)
        self.assertIsNotNone(snapshot_path)
        self.assertFalse(snapshot_path.exists())

    def test_cleanup_removes_readonly_snapshot_tree(self):
        snapshot = self.root / "readonly-posix-snapshot"
        nested = snapshot / "one" / "two"
        nested.mkdir(parents=True)
        payload = nested / "fixture.txt"
        payload.write_text("immutable fixture\n", encoding="utf-8")
        os.chmod(payload, 0o444)
        os.chmod(nested, 0o555)
        os.chmod(nested.parent, 0o555)
        os.chmod(snapshot, 0o555)

        _remove_snapshot(snapshot)

        self.assertFalse(snapshot.exists())

    def test_live_mutate_read_restore_cannot_change_worker_snapshot(self):
        live = self.repository / "fixture.txt"
        original = live.read_text(encoding="utf-8")

        with _tracked_repository_snapshot(
            self.repository, self.head, deadline=time.monotonic() + 30
        ) as snapshot:
            live.write_text("mutated during worker execution\n", encoding="utf-8")
            try:
                self.assertEqual(
                    original,
                    (snapshot.root / "fixture.txt").read_text(encoding="utf-8"),
                )
            finally:
                live.write_text(original, encoding="utf-8")

        status = subprocess.check_output(
            ("git", "status", "--porcelain=v2", "--untracked-files=all"),
            cwd=self.repository,
            text=True,
        )
        self.assertEqual("", status)

    def test_snapshot_and_worker_share_one_total_deadline(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        backend._ensure_container_absent = Mock()
        execution = (
            0,
            False,
            False,
            StaticSanitizer().sanitize(b"OK", b""),
            True,
        )
        backend._execute_argv = Mock(return_value=execution)
        short_spec = replace(
            self.container,
            limits=replace(self.container.limits, timeout_seconds=1),
        )
        request = self.request(
            worker_spec_id=self.container.spec_id,
            worker_spec_digest=self.container.spec_digest,
        )

        backend.execute(short_spec, request, lease_token="lease")

        remaining = backend._execute_argv.call_args.kwargs["timeout_seconds"]
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, short_spec.limits.timeout_seconds)

    def test_expired_total_deadline_prevents_snapshot_construction(self):
        with self.assertRaisesRegex(CompletionWorkerError, "total deadline"):
            with _tracked_repository_snapshot(
                self.repository,
                self.head,
                deadline=time.monotonic() - 1,
            ):
                self.fail("expired construction must not yield a snapshot")

    def test_snapshot_git_batch_is_contained_and_cannot_lazy_fetch(self):
        environment = _git_environment()
        self.assertEqual("1", environment["GIT_NO_LAZY_FETCH"])
        self.assertEqual("0", environment["GIT_TERMINAL_PROMPT"])
        self.assertEqual("Never", environment["GCM_INTERACTIVE"])
        process = Mock()
        guard = Mock()
        with patch(
            "director_orchestrator.completion_worker_backends.launch_contained_process",
            return_value=(process, guard),
        ) as launch:
            returned = _start_cat_file_batch(self.repository)

        self.assertEqual((process, guard), returned)
        command = launch.call_args.args[0]
        self.assertEqual(("cat-file", "--batch"), command[-2:])
        self.assertFalse(launch.call_args.kwargs["shell"])
        self.assertEqual("1", launch.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"])

    def test_cleanup_errors_never_skip_snapshot_removal_or_handle_closure(self):
        class BrokenInput:
            def __init__(self) -> None:
                self.closed = False

            def write(self, _value: bytes) -> None:
                raise BrokenPipeError("fixture write failure")

            def flush(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True
                raise OSError("fixture stdin close failure")

        class BrokenOutput:
            def __init__(self) -> None:
                self.closed = False

            def readline(self) -> bytes:
                return b""

            def close(self) -> None:
                self.closed = True
                raise OSError("fixture stdout close failure")

        process = Mock()
        process.stdin = BrokenInput()
        process.stdout = BrokenOutput()
        process.pid = 424242
        process.poll.return_value = None
        process.kill.side_effect = OSError("fixture kill failure")
        process.wait.return_value = 1
        guard = Mock()
        guard.terminate.side_effect = OSError("fixture guard failure")
        snapshot_root = self.root / "forced-snapshot"
        snapshot_root.mkdir()

        with patch(
            "director_orchestrator.completion_worker_backends.tempfile.mkdtemp",
            return_value=str(snapshot_root),
        ), patch(
            "director_orchestrator.completion_worker_backends._start_cat_file_batch",
            return_value=(process, guard),
        ), patch(
            "director_orchestrator.completion_worker_backends.os.killpg",
            create=True,
        ):
            with self.assertRaisesRegex(CompletionWorkerError, "cleanup failed closed"):
                with _tracked_repository_snapshot(
                    self.repository,
                    self.head,
                    deadline=time.monotonic() + 30,
                ):
                    self.fail("broken batch must not yield")

        self.assertFalse(snapshot_root.exists())
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        process.kill.assert_called()
        process.wait.assert_called()
        guard.terminate.assert_called()
        guard.close.assert_called()

    def test_host_backend_fails_closed_without_orchestrator_verified_controls(self):
        backend = AuditedHostWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        self.assertFalse(backend.capabilities.low_privilege_identity)
        self.assertFalse(backend.capabilities.network_isolation)
        self.assertFalse(backend.capabilities.resource_limits)
        self.assertFalse(backend.capabilities.read_only_repository)
        backend._execute_argv = Mock()

        with self.assertRaisesRegex(
            CompletionWorkerError, "orchestrator-verifiable OS evidence"
        ):
            backend.execute(self.host, self.request(), lease_token="lease")

        backend._execute_argv.assert_not_called()

    def test_worker_git_probes_use_explicit_repository_from_neutral_cwd(self):
        head_result = Mock(returncode=0, stdout=self.head + "\n")
        status_result = Mock(returncode=0, stdout="")
        with patch(
            "director_orchestrator.completion_worker_backends.subprocess.run",
            side_effect=[head_result, status_result],
        ) as backend_run:
            self.assertEqual((self.head, True), _repository_state(self.repository))
        with patch(
            "director_orchestrator.completion_workers.subprocess.run",
            side_effect=[head_result, status_result],
        ) as executor_run:
            CompletionWorkerExecutor._authenticate_repository(self.request())

        for run in (backend_run, executor_run):
            for call in run.call_args_list:
                command = call.args[0]
                self.assertEqual(("git", "-C"), command[:2])
                self.assertTrue(
                    os.path.samefile(command[2], self.repository),
                    f"git -C targeted a different repository: {command[2]}",
                )
                self.assertEqual(trusted_neutral_cwd(), call.kwargs["cwd"])

    def test_container_cleanup_is_label_bound_forced_and_verified(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        backend._ABSENCE_POLL_SECONDS = 0
        backend._runtime_control = Mock(
            side_effect=[
                (0, b"director-gate-fixture|worker-job-1\n"),
                (0, b""),
                (0, b""),
                (0, b""),
            ]
        )
        backend._ensure_container_absent("director-gate-fixture", "worker-job-1")
        self.assertEqual(4, backend._runtime_control.call_count)
        self.assertEqual(
            ("rm", "--force", "director-gate-fixture"),
            backend._runtime_control.call_args_list[1].args[0],
        )

    def test_container_cleanup_catches_late_creation_before_stable_absence(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        backend._ABSENCE_POLL_SECONDS = 0
        backend._runtime_control = Mock(
            side_effect=[
                (0, b""),
                (0, b"director-gate-fixture|worker-job-1\n"),
                (0, b""),
                (0, b""),
                (0, b""),
            ]
        )

        backend._ensure_container_absent("director-gate-fixture", "worker-job-1")

        self.assertEqual(
            ("rm", "--force", "director-gate-fixture"),
            backend._runtime_control.call_args_list[2].args[0],
        )

    def test_container_cleanup_fails_when_absence_never_stabilizes(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        backend._ABSENCE_POLL_SECONDS = 0
        backend._runtime_control = Mock(
            side_effect=[
                (0, b""),
                (0, b"director-gate-fixture|worker-job-1\n"),
                (0, b""),
                (0, b""),
                (0, b"director-gate-fixture|worker-job-1\n"),
                (0, b""),
                (0, b""),
            ]
        )

        with self.assertRaisesRegex(CompletionWorkerError, "stable.*absence"):
            backend._ensure_container_absent("director-gate-fixture", "worker-job-1")

    def test_container_cleanup_refuses_a_foreign_label(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        backend._runtime_control = Mock(
            return_value=(0, b"director-gate-fixture|another-job\n")
        )
        with self.assertRaisesRegex(CompletionWorkerError, "another job"):
            backend._ensure_container_absent("director-gate-fixture", "worker-job-1")
        self.assertEqual(1, backend._runtime_control.call_count)

    def test_container_cli_exception_still_requires_verified_daemon_cleanup(self):
        backend = DisposableContainerWorkerBackend(
            ExecutableBinding.create(sys.executable), StaticSanitizer()
        )
        backend._execute_argv = Mock(side_effect=CompletionWorkerError("CLI failed"))
        backend._ensure_container_absent = Mock()
        request = self.request(
            worker_spec_id=self.container.spec_id,
            worker_spec_digest=self.container.spec_digest,
        )
        with self.assertRaisesRegex(CompletionWorkerError, "CLI failed"):
            backend.execute(self.container, request, lease_token="lease")
        backend._ensure_container_absent.assert_called_once()

    def test_append_only_audit_and_guarded_job_transitions(self):
        request = self.request()
        self.store.claim(request)
        connection = sqlite3.connect(self.store.path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("UPDATE completion_worker_events SET event_type='x'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "invalid"):
                connection.execute(
                    "UPDATE completion_worker_jobs SET status='SUCCEEDED' WHERE job_id=?",
                    (request.job_id,),
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-25 | 02:27 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 12:35 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:12 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 18:48 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:22 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 21:01 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 07:12 UTC | END]]
