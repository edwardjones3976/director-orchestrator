# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:25 UTC | BEGIN]]
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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Protocol, Sequence

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
from .process_isolation import (
    ProcessTreeGuard,
    launch_contained_process,
    trusted_neutral_cwd,
)


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


@dataclass(frozen=True, slots=True)
class TrackedRepositorySnapshot:
    root: Path
    manifest_digest: str
    file_count: int


_SNAPSHOT_MAX_FILES = 10_000
_SNAPSHOT_MAX_BYTES = 512 * 1024 * 1024
_SNAPSHOT_MAX_TREE_BYTES = 8 * 1024 * 1024
_GIT_OBJECT_ID = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _git_bytes(
    repository: Path,
    argv: Sequence[str],
    *,
    stdin: bytes | None = None,
    deadline: float,
) -> bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), *argv),
            cwd=trusted_neutral_cwd(),
            env=_git_environment(),
            shell=False,
            check=False,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_remaining_seconds(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise CompletionWorkerError("completion worker total deadline expired") from exc
    except OSError as exc:
        raise CompletionWorkerError("tracked snapshot Git read failed") from exc
    if completed.returncode != 0:
        raise CompletionWorkerError("tracked snapshot Git read failed")
    return completed.stdout


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CompletionWorkerError("completion worker total deadline expired")
    return remaining


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def _start_cat_file_batch(
    repository: Path,
) -> tuple[subprocess.Popen[bytes], ProcessTreeGuard]:
    try:
        return launch_contained_process(
            ("git", "-C", str(repository), "cat-file", "--batch"),
            cwd=trusted_neutral_cwd(),
            env=_git_environment(),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise CompletionWorkerError("tracked snapshot Git read failed") from exc


def _snapshot_path(path_bytes: bytes, seen: set[str]) -> tuple[str, PurePosixPath]:
    try:
        path = path_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CompletionWorkerError("tracked snapshot contains a non-UTF-8 path") from exc
    pure = PurePosixPath(path)
    if (
        not path
        or pure.is_absolute()
        or "\\" in path
        or any(
            not part
            or part in {".", ".."}
            or ":" in part
            or part.casefold() == ".git"
            for part in pure.parts
        )
    ):
        raise CompletionWorkerError("tracked snapshot path is unsafe")
    alias = "/".join(
        unicodedata.normalize("NFC", part).casefold() for part in pure.parts
    )
    if alias in seen:
        raise CompletionWorkerError("tracked snapshot contains an aliased path")
    seen.add(alias)
    return path, pure


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError as exc:
        raise CompletionWorkerError("tracked snapshot path cannot be authenticated") from exc
    return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _remove_snapshot(root: Path) -> None:
    def restore_permissions(path: Path) -> None:
        if not path.exists() or _is_link_or_reparse(path):
            return
        os.chmod(path, 0o700 if path.is_dir() else 0o600)

    def make_writable_and_retry(function: Any, path: str, _error: Any) -> None:
        candidate = Path(path)
        if candidate.parent == root or root in candidate.parent.parents:
            restore_permissions(candidate.parent)
        restore_permissions(candidate)
        function(path)

    def prepare_for_removal(path: Path) -> None:
        try:
            restore_permissions(path)
        except OSError:
            # The rmtree callback retries the exact failing operation and keeps
            # cleanup fail-closed if the permission repair remains impossible.
            pass

    if root.exists():
        # POSIX unlink/rmdir authority belongs to the parent directory, not the
        # read-only child. Restore every snapshot directory before traversal;
        # Windows also benefits from clearing file read-only attributes.
        for current, directories, files in os.walk(
            root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            prepare_for_removal(current_path)
            for name in directories:
                prepare_for_removal(current_path / name)
            for name in files:
                prepare_for_removal(current_path / name)
        if sys.version_info >= (3, 12):
            shutil.rmtree(root, onexc=make_writable_and_retry)
        else:  # pragma: no cover - supported compatibility path
            shutil.rmtree(root, onerror=make_writable_and_retry)
    if root.exists():
        raise CompletionWorkerError("tracked snapshot cleanup could not be verified")


@contextmanager
def _tracked_repository_snapshot(
    repository: Path, expected_head_sha: str, *, deadline: float
) -> Iterator[TrackedRepositorySnapshot]:
    _remaining_seconds(deadline)
    tree = _git_bytes(
        repository,
        ("ls-tree", "-rz", "--full-tree", expected_head_sha),
        deadline=deadline,
    )
    if len(tree) > _SNAPSHOT_MAX_TREE_BYTES:
        raise CompletionWorkerError("tracked snapshot tree exceeds its size limit")
    records = [record for record in tree.split(b"\0") if record]
    if len(records) > _SNAPSHOT_MAX_FILES:
        raise CompletionWorkerError("tracked snapshot exceeds its file-count limit")

    parsed: list[tuple[str, PurePosixPath, str, str]] = []
    seen: set[str] = set()
    for record in records:
        header, separator, path_bytes = record.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3:
            raise CompletionWorkerError("tracked snapshot tree record is malformed")
        mode_bytes, object_type, object_id = fields
        if mode_bytes == b"120000" or object_type != b"blob":
            raise CompletionWorkerError("tracked snapshot cannot contain links or submodules")
        if mode_bytes not in {b"100644", b"100755"} or not _GIT_OBJECT_ID.fullmatch(
            object_id
        ):
            raise CompletionWorkerError("tracked snapshot tree record is unsupported")
        path, pure = _snapshot_path(path_bytes, seen)
        parsed.append((path, pure, mode_bytes.decode("ascii"), object_id.decode("ascii")))

    root = Path(tempfile.mkdtemp(prefix="director-completion-snapshot-")).resolve(
        strict=True
    )
    repository = repository.resolve(strict=True)
    batch: subprocess.Popen[bytes] | None = None
    batch_guard: ProcessTreeGuard | None = None
    deadline_expired = threading.Event()
    deadline_termination_errors: list[BaseException] = []
    timer: threading.Timer | None = None
    try:
        if repository == root or repository in root.parents or _is_link_or_reparse(root):
            raise CompletionWorkerError("tracked snapshot directory is not isolated")
        manifest: list[dict[str, Any]] = []
        total_size = 0
        if parsed:
            batch, batch_guard = _start_cat_file_batch(repository)
            if batch.stdin is None or batch.stdout is None:
                raise CompletionWorkerError("tracked snapshot batch pipes are unavailable")

            def expire_batch() -> None:
                deadline_expired.set()
                if batch is not None:
                    try:
                        if batch_guard is not None:
                            batch_guard.terminate()
                        if os.name != "nt":
                            try:
                                os.killpg(batch.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        batch.kill()
                    except BaseException as exc:
                        deadline_termination_errors.append(exc)

            timer = threading.Timer(_remaining_seconds(deadline), expire_batch)
            timer.daemon = True
            timer.start()

        for path, pure, mode, object_id in parsed:
            _remaining_seconds(deadline)
            assert batch is not None and batch.stdin is not None and batch.stdout is not None
            try:
                batch.stdin.write(object_id.encode("ascii") + b"\n")
                batch.stdin.flush()
                header = batch.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                if deadline_expired.is_set():
                    raise CompletionWorkerError(
                        "completion worker total deadline expired"
                    ) from exc
                raise CompletionWorkerError("tracked snapshot Git batch failed") from exc
            fields = header.rstrip(b"\n").split(b" ")
            if (
                len(fields) != 3
                or fields[0].decode("ascii", errors="ignore") != object_id
                or fields[1] != b"blob"
                or not fields[2].isdigit()
            ):
                if deadline_expired.is_set():
                    raise CompletionWorkerError("completion worker total deadline expired")
                raise CompletionWorkerError("tracked snapshot object inventory is invalid")
            expected_size = int(fields[2])
            total_size += expected_size
            if total_size > _SNAPSHOT_MAX_BYTES:
                raise CompletionWorkerError("tracked snapshot exceeds its byte limit")
            target = root.joinpath(*pure.parts)
            if root not in target.parents:
                raise CompletionWorkerError("tracked snapshot path escaped its root")
            target.parent.mkdir(parents=True, exist_ok=True)
            if any(_is_link_or_reparse(parent) for parent in target.parents if parent != root):
                raise CompletionWorkerError("tracked snapshot path traversed a link")
            digest = hashlib.sha256()
            try:
                with target.open("xb") as handle:
                    remaining = expected_size
                    while remaining:
                        _remaining_seconds(deadline)
                        chunk = batch.stdout.read(min(1_048_576, remaining))
                        if not chunk:
                            if deadline_expired.is_set():
                                raise CompletionWorkerError(
                                    "completion worker total deadline expired"
                                )
                            raise CompletionWorkerError(
                                "tracked snapshot blob was truncated"
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if batch.stdout.read(1) != b"\n":
                        raise CompletionWorkerError(
                            "tracked snapshot blob delimiter is invalid"
                        )
            except OSError as exc:
                raise CompletionWorkerError("tracked snapshot file could not be created") from exc
            content_digest = "sha256:" + digest.hexdigest()
            manifest.append(
                {
                    "path": path,
                    "mode": mode,
                    "object_id": object_id,
                    "size": expected_size,
                    "content_digest": content_digest,
                }
            )
            os.chmod(target, 0o555 if mode == "100755" else 0o444)
        if batch is not None:
            assert batch.stdin is not None
            batch.stdin.close()
            try:
                return_code = batch.wait(timeout=_remaining_seconds(deadline))
            except subprocess.TimeoutExpired as exc:
                deadline_expired.set()
                batch.kill()
                batch.wait(timeout=5)
                raise CompletionWorkerError(
                    "completion worker total deadline expired"
                ) from exc
            if return_code != 0:
                if deadline_expired.is_set():
                    raise CompletionWorkerError("completion worker total deadline expired")
                raise CompletionWorkerError("tracked snapshot Git batch failed")
            if deadline_termination_errors:
                raise CompletionWorkerError(
                    "tracked snapshot deadline containment failed"
                ) from deadline_termination_errors[0]
        directories = {root}
        for _, pure, _, _ in parsed:
            candidate = root.joinpath(*pure.parts).parent
            while candidate != root:
                directories.add(candidate)
                candidate = candidate.parent
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            os.chmod(directory, 0o555)
        _remaining_seconds(deadline)
        yield TrackedRepositorySnapshot(
            root=root,
            manifest_digest=domain_digest(
                "director-completion-worker-snapshot-manifest/v1",
                {"head_sha": expected_head_sha, "entries": manifest},
            ),
            file_count=len(manifest),
        )
    finally:
        if timer is not None:
            timer.cancel()
        cleanup_errors: list[BaseException] = []

        def cleanup_step(operation: Any) -> None:
            try:
                operation()
            except BaseException as exc:
                cleanup_errors.append(exc)

        try:
            if batch is not None:
                if batch.stdin is not None and not batch.stdin.closed:
                    cleanup_step(batch.stdin.close)
                if batch_guard is not None:
                    cleanup_step(batch_guard.terminate)
                if os.name != "nt":
                    def terminate_batch_group() -> None:
                        try:
                            os.killpg(batch.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

                    cleanup_step(terminate_batch_group)
                if batch.poll() is None:
                    cleanup_step(batch.kill)
                    cleanup_step(lambda: batch.wait(timeout=5))
                if batch.stdout is not None and not batch.stdout.closed:
                    cleanup_step(batch.stdout.close)
                if batch_guard is not None:
                    cleanup_step(batch_guard.close)
        finally:
            cleanup_step(lambda: _remove_snapshot(root))
        if cleanup_errors:
            raise CompletionWorkerError(
                "tracked snapshot cleanup failed closed"
            ) from cleanup_errors[0]


class _BoundedProcessBackend:
    binding: ExecutableBinding
    sanitizer: WorkerOutputSanitizer

    def _execute_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
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
        self,
        spec: AuditedHostProfile,
        request: CompletionWorkerRequest,
        snapshot: TrackedRepositorySnapshot,
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
            str(snapshot.root),
            "--env",
            f"PYTHONPATH={snapshot.root / 'src'}",
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
        deadline = time.monotonic() + spec.limits.timeout_seconds
        with _tracked_repository_snapshot(
            root, request.expected_head_sha, deadline=deadline
        ) as snapshot:
            command = self.command(spec, request, snapshot)
            controls = _verified_host_controls(command, spec, request, snapshot)
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
                timeout_seconds=_remaining_seconds(deadline),
                output_limit_bytes=spec.limits.output_limit_bytes,
            )
            snapshot_manifest_digest = snapshot.manifest_digest
            snapshot_file_count = snapshot.file_count
        after_head, after_clean = _repository_state(root)
        return _evidence(
            request, spec, lease_token, before_head, after_head,
            before_clean, after_clean, result,
            disposable=False,
            controls=controls,
            snapshot_manifest_digest=snapshot_manifest_digest,
            snapshot_file_count=snapshot_file_count,
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
        self,
        spec: DisposableContainerSpec,
        request: CompletionWorkerRequest,
        snapshot: TrackedRepositorySnapshot,
    ) -> tuple[str, ...]:
        snapshot_root = str(snapshot.root)
        if any(character in snapshot_root for character in (",", "\r", "\n")):
            raise CompletionWorkerError("repository path cannot be encoded safely for a mount")
        name = _container_name(request.job_id)
        limits = spec.limits
        isolated_compileall = (
            "python",
            "-I",
            "-X",
            "pycache_prefix=/tmp/pycache",
            "-m",
            "compileall",
        )
        if (
            spec.runner_id == "python_compileall"
            and tuple(spec.fixed_argv[:6]) != isolated_compileall
        ):
            raise CompletionWorkerError(
                "container compileall must use isolated Python startup"
            )
        repository_environment: tuple[str, ...] = ()
        pycache_environment: tuple[str, ...] = ()
        if spec.runner_id != "python_compileall":
            repository_environment = ("--env", "PYTHONPATH=/workspace/src")
            pycache_environment = ("--env", "PYTHONPYCACHEPREFIX=/tmp/pycache")
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
            f"type=bind,src={snapshot_root},dst=/workspace,readonly",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=67108864",
            *repository_environment,
            *pycache_environment,
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
        deadline = time.monotonic() + spec.limits.timeout_seconds
        with _tracked_repository_snapshot(
            root, request.expected_head_sha, deadline=deadline
        ) as snapshot:
            command = self.command(spec, request, snapshot)
            controls = _verified_container_controls(command, spec, request, snapshot)
            try:
                result = self._execute_argv(
                    command,
                    cwd=self._trusted_cwd(),
                    timeout_seconds=_remaining_seconds(deadline),
                    output_limit_bytes=spec.limits.output_limit_bytes,
                )
            except BaseException:
                self._ensure_container_absent(name, request.job_id)
                raise
            self._ensure_container_absent(name, request.job_id)
            snapshot_manifest_digest = snapshot.manifest_digest
            snapshot_file_count = snapshot.file_count
        after_head, after_clean = _repository_state(root)
        return _evidence(
            request, spec, lease_token, before_head, after_head,
            before_clean, after_clean, result,
            disposable=True,
            controls=controls,
            snapshot_manifest_digest=snapshot_manifest_digest,
            snapshot_file_count=snapshot_file_count,
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
    snapshot: TrackedRepositorySnapshot,
) -> WorkerBackendCapabilities:
    required_pairs = (
        ("--profile", spec.launcher_profile_id),
        ("--network", "none"),
        ("--memory-bytes", str(spec.limits.memory_limit_bytes)),
        ("--cpus", str(spec.limits.cpu_limit)),
        ("--pids-limit", str(spec.limits.pids_limit)),
        ("--read-only-repository", str(snapshot.root)),
        ("--env", f"PYTHONPATH={snapshot.root / 'src'}"),
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
    snapshot: TrackedRepositorySnapshot,
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
        ("--mount", f"type=bind,src={snapshot.root},dst=/workspace,readonly"),
    )
    if not all(flag in command for flag in required_flags):
        raise CompletionWorkerError("container worker controls are incomplete")
    if not all(_command_pair(command, option, value) for option, value in required_pairs):
        raise CompletionWorkerError("container worker controls are incomplete")
    image_index = command.index(spec.image)
    if tuple(command[image_index + 1 :]) != spec.fixed_argv:
        raise CompletionWorkerError("container worker command binding changed")
    if spec.runner_id == "python_compileall":
        injected_environment = tuple(
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--env"
        )
        if any(
            value.startswith(("PYTHONPATH=", "PYTHONPYCACHEPREFIX="))
            for value in injected_environment
        ):
            raise CompletionWorkerError("container compileall import path is not isolated")
        if tuple(spec.fixed_argv[:6]) != (
            "python",
            "-I",
            "-X",
            "pycache_prefix=/tmp/pycache",
            "-m",
            "compileall",
        ):
            raise CompletionWorkerError("container compileall startup is not isolated")
    elif not all(
        _command_pair(command, "--env", value)
        for value in (
            "PYTHONPATH=/workspace/src",
            "PYTHONPYCACHEPREFIX=/tmp/pycache",
        )
    ):
        raise CompletionWorkerError("container worker controls are incomplete")
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
    snapshot_manifest_digest: str,
    snapshot_file_count: int,
) -> WorkerBackendEvidence:
    exit_code, timed_out, overflow, output, tree_terminated = execution
    return WorkerBackendEvidence(
        job_id=request.job_id,
        lease_digest=domain_digest("director-completion-worker-lease/v1", lease_token),
        tier=spec.tier,
        runner_registry_digest=request.runner_registry_digest,
        worker_registry_digest=request.worker_registry_digest,
        worker_spec_digest=request.worker_spec_digest,
        snapshot_manifest_digest=snapshot_manifest_digest,
        snapshot_file_count=snapshot_file_count,
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
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 15:25 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 16:32 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:07 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 20:22 UTC | END]]
