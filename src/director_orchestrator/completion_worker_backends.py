# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-25 | 02:21 UTC | BEGIN]]
"""Concrete no-shell Completion Gate worker backends.

Host work is delegated to an audited low-privilege broker.  Repository-controlled
commands never execute directly under the orchestrator account.  Container work
uses a digest-pinned image, a read-only repository mount, no network, a read-only
root filesystem, no capabilities, no-new-privileges, and bounded resources.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .completion_gates import domain_digest
from .completion_workers import (
    AuditedHostProfile,
    CompletionWorkerError,
    CompletionWorkerRequest,
    DisposableContainerSpec,
    WorkerBackendCapabilities,
    WorkerBackendEvidence,
    WorkerSpec,
    WorkerTier,
)
from .process_isolation import ProcessTreeGuard


@dataclass(frozen=True, slots=True)
class SanitizedWorkerOutput:
    decisive_output: str
    output_digest: str
    redaction_passed: bool
    secret_scan_passed: bool


class WorkerOutputSanitizer(Protocol):
    def sanitize(self, stdout: bytes, stderr: bytes) -> SanitizedWorkerOutput: ...


@dataclass(frozen=True, slots=True)
class ExecutableBinding:
    path: str
    fingerprint: str

    @classmethod
    def create(cls, path: str | os.PathLike[str]) -> "ExecutableBinding":
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file():
            raise CompletionWorkerError("worker backend executable is not a file")
        return cls(str(resolved), _file_digest(resolved))

    def authenticate(self) -> None:
        if _file_digest(Path(self.path)) != self.fingerprint:
            raise CompletionWorkerError("worker backend executable fingerprint changed")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CompletionWorkerError("worker backend executable cannot be read") from exc
    return "sha256:" + digest.hexdigest()


class _BoundedProcessBackend:
    binding: ExecutableBinding
    sanitizer: WorkerOutputSanitizer

    def _execute_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        output_limit_bytes: int,
    ) -> tuple[int | None, bool, bool, SanitizedWorkerOutput, bool]:
        self.binding.authenticate()
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"COMSPEC", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
        }
        creationflags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            tuple(argv),
            cwd=cwd,
            env=environment,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
        tree_guard = ProcessTreeGuard(process)
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        lock = threading.Lock()
        overflow = threading.Event()

        def drain(name: str, stream: Any) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    with lock:
                        captured = len(buffers["stdout"]) + len(buffers["stderr"])
                        remaining = max(0, output_limit_bytes - captured)
                        buffers[name].extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            overflow.set()
                            self._terminate_tree(process)
                            return
            finally:
                stream.close()

        readers = [
            threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        tree_terminated = False
        try:
            exit_code: int | None = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            tree_guard.terminate()
            self._terminate_tree(process)
            tree_terminated = True
            process.wait(timeout=10)
            exit_code = None
        if overflow.is_set():
            tree_guard.terminate()
            tree_terminated = True
            process.wait(timeout=10)
        for reader in readers:
            reader.join(timeout=10)
        if any(reader.is_alive() for reader in readers):
            self._terminate_tree(process)
            raise CompletionWorkerError("worker output readers did not terminate")
        sanitized = self.sanitizer.sanitize(
            bytes(buffers["stdout"]), bytes(buffers["stderr"])
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", sanitized.output_digest):
            raise CompletionWorkerError("sanitizer returned a malformed output digest")
        tree_guard.close()
        return exit_code, timed_out, overflow.is_set(), sanitized, tree_terminated

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                shell=False,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class AuditedHostWorkerBackend(_BoundedProcessBackend):
    """Invoke only a separately installed, exact-fingerprint sandbox broker."""

    def __init__(
        self,
        launcher: ExecutableBinding,
        sanitizer: WorkerOutputSanitizer,
    ) -> None:
        self.binding = launcher
        self.sanitizer = sanitizer

    @property
    def capabilities(self) -> WorkerBackendCapabilities:
        return WorkerBackendCapabilities(
            tier=WorkerTier.AUDITED_HOST,
            low_privilege_identity=True,
            network_isolation=True,
            resource_limits=True,
            process_tree_termination=True,
            read_only_repository=True,
            disposable_filesystem=False,
        )

    def command(
        self, spec: AuditedHostProfile, request: CompletionWorkerRequest
    ) -> tuple[str, ...]:
        limits = spec.limits
        return (
            self.binding.path,
            "run",
            "--profile",
            spec.launcher_profile_id,
            "--network",
            "none",
            "--memory-bytes",
            str(limits.memory_limit_bytes),
            "--cpus",
            str(limits.cpu_limit),
            "--pids-limit",
            str(limits.pids_limit),
            "--read-only-repository",
            request.repository_root,
            "--expected-head",
            request.expected_head_sha,
            "--",
            *spec.fixed_argv,
        )

    def execute(
        self,
        spec: WorkerSpec,
        request: CompletionWorkerRequest,
        *,
        lease_token: str,
    ) -> WorkerBackendEvidence:
        if not isinstance(spec, AuditedHostProfile):
            raise CompletionWorkerError("host backend received the wrong worker tier")
        root = _repository_root(request)
        before_head, before_clean = _repository_state(root)
        result = self._execute_argv(
            self.command(spec, request),
            cwd=root,
            timeout_seconds=spec.limits.timeout_seconds,
            output_limit_bytes=spec.limits.output_limit_bytes,
        )
        after_head, after_clean = _repository_state(root)
        return _evidence(
            request, spec, lease_token, before_head, after_head,
            before_clean, after_clean, result,
            disposable=False,
        )


class DisposableContainerWorkerBackend(_BoundedProcessBackend):
    def __init__(
        self,
        runtime: ExecutableBinding,
        sanitizer: WorkerOutputSanitizer,
    ) -> None:
        self.binding = runtime
        self.sanitizer = sanitizer

    @property
    def capabilities(self) -> WorkerBackendCapabilities:
        return WorkerBackendCapabilities(
            tier=WorkerTier.DISPOSABLE_CONTAINER,
            low_privilege_identity=True,
            network_isolation=True,
            resource_limits=True,
            process_tree_termination=True,
            read_only_repository=True,
            disposable_filesystem=True,
        )

    def command(
        self, spec: DisposableContainerSpec, request: CompletionWorkerRequest
    ) -> tuple[str, ...]:
        if any(character in request.repository_root for character in (",", "\r", "\n")):
            raise CompletionWorkerError("repository path cannot be encoded safely for a mount")
        name = "director-gate-" + hashlib.sha256(
            request.job_id.encode("utf-8")
        ).hexdigest()[:20]
        limits = spec.limits
        return (
            self.binding.path,
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            f"director.completion-job={request.job_id}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(limits.pids_limit),
            "--memory",
            str(limits.memory_limit_bytes),
            "--cpus",
            str(limits.cpu_limit),
            "--user",
            spec.run_as_user,
            "--workdir",
            spec.working_directory,
            "--mount",
            f"type=bind,src={request.repository_root},dst=/workspace,readonly",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=67108864",
            spec.image,
            *spec.fixed_argv,
        )

    def execute(
        self,
        spec: WorkerSpec,
        request: CompletionWorkerRequest,
        *,
        lease_token: str,
    ) -> WorkerBackendEvidence:
        if not isinstance(spec, DisposableContainerSpec):
            raise CompletionWorkerError("container backend received the wrong worker tier")
        root = _repository_root(request)
        before_head, before_clean = _repository_state(root)
        result = self._execute_argv(
            self.command(spec, request),
            cwd=root,
            timeout_seconds=spec.limits.timeout_seconds,
            output_limit_bytes=spec.limits.output_limit_bytes,
        )
        after_head, after_clean = _repository_state(root)
        return _evidence(
            request, spec, lease_token, before_head, after_head,
            before_clean, after_clean, result,
            disposable=True,
        )


def _repository_root(request: CompletionWorkerRequest) -> Path:
    root = Path(request.repository_root).resolve(strict=True)
    if not root.is_dir():
        raise CompletionWorkerError("worker repository root is not a directory")
    return root


def _repository_state(root: Path) -> tuple[str, bool]:
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, shell=False, check=False,
        capture_output=True, text=True, timeout=15,
    )
    status = subprocess.run(
        ("git", "status", "--porcelain=v2", "--untracked-files=all"),
        cwd=root, shell=False, check=False, capture_output=True, text=True, timeout=15,
    )
    if (
        head.returncode != 0
        or status.returncode != 0
        or re.fullmatch(r"[0-9a-f]{40}\n?", head.stdout) is None
    ):
        raise CompletionWorkerError("worker cannot authenticate repository state")
    return head.stdout.strip(), status.stdout == ""


def _evidence(
    request: CompletionWorkerRequest,
    spec: WorkerSpec,
    lease_token: str,
    before_head: str,
    after_head: str,
    before_clean: bool,
    after_clean: bool,
    execution: tuple[int | None, bool, bool, SanitizedWorkerOutput, bool],
    *,
    disposable: bool,
) -> WorkerBackendEvidence:
    exit_code, timed_out, overflow, output, tree_terminated = execution
    return WorkerBackendEvidence(
        job_id=request.job_id,
        lease_digest=domain_digest("director-completion-worker-lease/v1", lease_token),
        tier=spec.tier,
        runner_registry_digest=request.runner_registry_digest,
        worker_registry_digest=request.worker_registry_digest,
        worker_spec_digest=request.worker_spec_digest,
        before_head_sha=before_head,
        after_head_sha=after_head,
        before_clean=before_clean,
        after_clean=after_clean,
        exit_code=exit_code,
        timed_out=timed_out,
        output_limit_exceeded=overflow,
        sanitized_output_digest=output.output_digest,
        decisive_output=output.decisive_output,
        redaction_passed=output.redaction_passed,
        secret_scan_passed=output.secret_scan_passed,
        network_isolated=True,
        resource_limits_applied=True,
        low_privilege_identity_applied=True,
        process_tree_terminated=(tree_terminated or (not timed_out and not overflow)),
        repository_read_only=True,
        disposable_filesystem=disposable,
    )
# [[Codex | GPT-5.6 | 2026-08-25 | 02:21 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
