# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:12 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
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
import time
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
from .process_isolation import launch_contained_process, trusted_neutral_cwd


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
        environment = _safe_environment()
        process, tree_guard = launch_contained_process(
            tuple(argv),
            cwd=cwd,
            env=environment,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        lock = threading.Lock()
        overflow = threading.Event()
        termination_started = threading.Event()
        termination_errors: list[BaseException] = []
        termination_lock = threading.Lock()

        def terminate_tree_once() -> None:
            with termination_lock:
                if termination_started.is_set():
                    return
                termination_started.set()
            try:
                tree_guard.terminate()
                self._terminate_posix_group(process)
            except BaseException as exc:  # evidence must fail closed
                termination_errors.append(exc)

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
                            terminate_tree_once()
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
        try:
            exit_code: int | None = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_tree_once()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise CompletionWorkerError(
                    "worker process tree did not terminate after timeout"
                ) from exc
            exit_code = None
        if overflow.is_set():
            terminate_tree_once()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise CompletionWorkerError(
                    "worker process tree did not terminate after output overflow"
                ) from exc
        # A normally exiting parent may leave descendants behind. Close the
        # complete Job/session group before accepting any worker evidence.
        terminate_tree_once()
        for reader in readers:
            reader.join(timeout=10)
        if any(reader.is_alive() for reader in readers):
            raise CompletionWorkerError("worker output readers did not terminate")
        if termination_errors:
            raise CompletionWorkerError("worker process containment failed") from termination_errors[0]
        sanitized = self.sanitizer.sanitize(
            bytes(buffers["stdout"]), bytes(buffers["stderr"])
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", sanitized.output_digest):
            raise CompletionWorkerError("sanitizer returned a malformed output digest")
        tree_guard.close()
        return exit_code, timed_out, overflow.is_set(), sanitized, True

    @staticmethod
    def _terminate_posix_group(process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _trusted_cwd(self) -> Path:
        try:
            return trusted_neutral_cwd()
        except (OSError, RuntimeError) as exc:
            raise CompletionWorkerError(
                "trusted neutral worker directory is unavailable"
            ) from exc


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
        # The orchestrator can authenticate this broker executable and bind its
        # argv, but it cannot observe the broker's child token, network policy,
        # resource limits, or repository handle.  Until an OS-backed verifier is
        # wired, do not advertise controls that only the broker could assert.
        return WorkerBackendCapabilities(
            tier=WorkerTier.AUDITED_HOST,
            low_privilege_identity=False,
            network_isolation=False,
            resource_limits=False,
            process_tree_termination=True,
            read_only_repository=False,
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
            "--env",
            f"PYTHONPATH={Path(request.repository_root, 'src')}",
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
        command = self.command(spec, request)
        controls = _verified_host_controls(command, spec, request)
        if not all(
            (
                controls.low_privilege_identity,
                controls.network_isolation,
                controls.resource_limits,
                controls.read_only_repository,
            )
        ):
            raise CompletionWorkerError(
                "audited host isolation lacks orchestrator-verifiable OS evidence"
            )
        result = self._execute_argv(
            command,
            cwd=self._trusted_cwd(),
            timeout_seconds=spec.limits.timeout_seconds,
            output_limit_bytes=spec.limits.output_limit_bytes,
        )
        after_head, after_clean = _repository_state(root)
        return _evidence(
            request, spec, lease_token, before_head, after_head,
            before_clean, after_clean, result,
            disposable=False,
            controls=controls,
        )


class DisposableContainerWorkerBackend(_BoundedProcessBackend):
    _ABSENCE_READS_REQUIRED = 2
    _ABSENCE_POLL_ATTEMPTS = 5
    _ABSENCE_POLL_SECONDS = 0.2

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
        name = _container_name(request.job_id)
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
            "--env",
            "PYTHONPATH=/workspace/src",
            "--env",
            "PYTHONPYCACHEPREFIX=/tmp/pycache",
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
        name = _container_name(request.job_id)
        command = self.command(spec, request)
        controls = _verified_container_controls(command, spec, request)
        try:
            result = self._execute_argv(
                command,
                cwd=self._trusted_cwd(),
                timeout_seconds=spec.limits.timeout_seconds,
                output_limit_bytes=spec.limits.output_limit_bytes,
            )
        except BaseException:
            self._ensure_container_absent(name, request.job_id)
            raise
        self._ensure_container_absent(name, request.job_id)
        after_head, after_clean = _repository_state(root)
        return _evidence(
            request, spec, lease_token, before_head, after_head,
            before_clean, after_clean, result,
            disposable=True,
            controls=controls,
        )

    def _ensure_container_absent(self, name: str, job_id: str) -> None:
        consecutive_absence_reads = 0
        for attempt in range(self._ABSENCE_POLL_ATTEMPTS):
            label = self._container_label(name)
            if label is None:
                consecutive_absence_reads += 1
                if consecutive_absence_reads >= self._ABSENCE_READS_REQUIRED:
                    return
            else:
                consecutive_absence_reads = 0
                if label != job_id:
                    raise CompletionWorkerError(
                        "deterministic worker container name is owned by another job"
                    )
                return_code, _ = self._runtime_control(("rm", "--force", name))
                if return_code != 0:
                    raise CompletionWorkerError("worker container force-removal failed")
            if attempt + 1 < self._ABSENCE_POLL_ATTEMPTS:
                time.sleep(self._ABSENCE_POLL_SECONDS)
        raise CompletionWorkerError(
            "stable worker container absence could not be verified"
        )

    def _container_label(self, name: str) -> str | None:
        return_code, stdout = self._runtime_control(
            (
                "container",
                "ls",
                "--all",
                "--filter",
                f"name=^/{name}$",
                "--format",
                '{{.Names}}|{{.Label "director.completion-job"}}',
            )
        )
        if return_code != 0:
            raise CompletionWorkerError("worker container state could not be verified")
        lines = [line for line in stdout.decode("utf-8", errors="strict").splitlines() if line]
        if not lines:
            return None
        if len(lines) != 1:
            raise CompletionWorkerError("worker container state was ambiguous")
        container_name, separator, label = lines[0].partition("|")
        if separator != "|" or container_name != name or not label:
            raise CompletionWorkerError("worker container identity was ambiguous")
        return label

    def _runtime_control(self, argv: Sequence[str]) -> tuple[int, bytes]:
        self.binding.authenticate()
        process, tree_guard = launch_contained_process(
            (self.binding.path, *argv),
            cwd=self._trusted_cwd(),
            env=_safe_environment(),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            try:
                stdout, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired as exc:
                tree_guard.terminate()
                self._terminate_posix_group(process)
                process.wait(timeout=5)
                raise CompletionWorkerError("worker container control timed out") from exc
            tree_guard.terminate()
            self._terminate_posix_group(process)
            if len(stdout) > 4096:
                raise CompletionWorkerError("worker container control output was oversized")
            return int(process.returncode), stdout
        finally:
            tree_guard.close()


def _repository_root(request: CompletionWorkerRequest) -> Path:
    root = Path(request.repository_root).resolve(strict=True)
    if not root.is_dir():
        raise CompletionWorkerError("worker repository root is not a directory")
    return root


def _container_name(job_id: str) -> str:
    return "director-gate-" + hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:20]


def _safe_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"COMSPEC", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    }


def _command_pair(command: Sequence[str], option: str, expected: str) -> bool:
    return any(
        command[index] == option and command[index + 1] == expected
        for index in range(len(command) - 1)
    )


def _verified_host_controls(
    command: Sequence[str],
    spec: AuditedHostProfile,
    request: CompletionWorkerRequest,
) -> WorkerBackendCapabilities:
    required_pairs = (
        ("--profile", spec.launcher_profile_id),
        ("--network", "none"),
        ("--memory-bytes", str(spec.limits.memory_limit_bytes)),
        ("--cpus", str(spec.limits.cpu_limit)),
        ("--pids-limit", str(spec.limits.pids_limit)),
        ("--read-only-repository", request.repository_root),
        ("--env", f"PYTHONPATH={Path(request.repository_root, 'src')}"),
        ("--expected-head", request.expected_head_sha),
    )
    if not all(_command_pair(command, option, value) for option, value in required_pairs):
        raise CompletionWorkerError("audited host worker controls are incomplete")
    if "--" not in command or tuple(command[command.index("--") + 1 :]) != spec.fixed_argv:
        raise CompletionWorkerError("audited host worker command binding changed")
    return WorkerBackendCapabilities(
        tier=WorkerTier.AUDITED_HOST,
        low_privilege_identity=False,
        network_isolation=False,
        resource_limits=False,
        process_tree_termination=True,
        read_only_repository=False,
        disposable_filesystem=False,
    )


def _verified_container_controls(
    command: Sequence[str],
    spec: DisposableContainerSpec,
    request: CompletionWorkerRequest,
) -> WorkerBackendCapabilities:
    required_flags = ("--rm", "--read-only")
    required_pairs = (
        ("--name", _container_name(request.job_id)),
        ("--label", f"director.completion-job={request.job_id}"),
        ("--network", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--pids-limit", str(spec.limits.pids_limit)),
        ("--memory", str(spec.limits.memory_limit_bytes)),
        ("--cpus", str(spec.limits.cpu_limit)),
        ("--user", spec.run_as_user),
        ("--workdir", spec.working_directory),
        ("--mount", f"type=bind,src={request.repository_root},dst=/workspace,readonly"),
        ("--env", "PYTHONPATH=/workspace/src"),
        ("--env", "PYTHONPYCACHEPREFIX=/tmp/pycache"),
    )
    if not all(flag in command for flag in required_flags):
        raise CompletionWorkerError("container worker controls are incomplete")
    if not all(_command_pair(command, option, value) for option, value in required_pairs):
        raise CompletionWorkerError("container worker controls are incomplete")
    image_index = command.index(spec.image)
    if tuple(command[image_index + 1 :]) != spec.fixed_argv:
        raise CompletionWorkerError("container worker command binding changed")
    return WorkerBackendCapabilities(
        tier=WorkerTier.DISPOSABLE_CONTAINER,
        low_privilege_identity=spec.run_as_user not in {"0", "0:0", "root"},
        network_isolation=True,
        resource_limits=True,
        process_tree_termination=True,
        read_only_repository=True,
        disposable_filesystem=True,
    )


def _repository_state(root: Path) -> tuple[str, bool]:
    neutral_cwd = trusted_neutral_cwd()
    head = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        cwd=neutral_cwd, shell=False, check=False,
        capture_output=True, text=True, timeout=15,
    )
    status = subprocess.run(
        (
            "git", "-C", str(root), "status", "--porcelain=v2",
            "--untracked-files=all",
        ),
        cwd=neutral_cwd, shell=False, check=False,
        capture_output=True, text=True, timeout=15,
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
    controls: WorkerBackendCapabilities,
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
        network_isolated=controls.network_isolation,
        resource_limits_applied=controls.resource_limits,
        low_privilege_identity_applied=controls.low_privilege_identity,
        process_tree_terminated=(tree_terminated and controls.process_tree_termination),
        repository_read_only=controls.read_only_repository,
        disposable_filesystem=(disposable and controls.disposable_filesystem),
    )
# [[Codex | GPT-5.6 | 2026-08-25 | 02:21 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:45 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 13:12 UTC | END]]
