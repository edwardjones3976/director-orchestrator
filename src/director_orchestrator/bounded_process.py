# [[OpenAI Codex | GPT-6 | 2026-09-08 | 03:32 UTC | BEGIN]]
"""Shared contained subprocess primitive extracted from completion-gate runners."""
from __future__ import annotations

import errno
import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

from .process_isolation import ProcessContainmentError, launch_contained_process


def run_bounded_process(
    argv: Sequence[str], *, cwd: Path, environment: Mapping[str, str],
    timeout_seconds: float, output_limit_bytes: int, input_bytes: bytes = b"",
    input_limit_bytes: int = 1_048_576, cleanup_timeout_seconds: float = 5,
    deadline: float | None = None,
) -> tuple[int | None, bool, bool, bytes, bytes]:
    """Execute once; bound stdin and combined retained output.

    ``deadline`` is absolute monotonic time shared with caller preflight. Execution
    ends at the earlier deadline/timeout. OS termination and pipe draining have a
    separate cleanup allowance; incomplete cleanup raises, never yields success.
    Windows uses the existing suspended-launch Job Object; POSIX uses its session.
    """
    if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0
            or not math.isfinite(cleanup_timeout_seconds) or cleanup_timeout_seconds <= 0
            or output_limit_bytes < 1 or input_limit_bytes < 0
            or not isinstance(input_bytes, bytes) or len(input_bytes) > input_limit_bytes
            or (deadline is not None and not math.isfinite(deadline))):
        raise ValueError("invalid bounded process limits")
    end = time.monotonic() + timeout_seconds
    if deadline is not None:
        end = min(end, deadline)
    if time.monotonic() >= end:
        return None, True, False, b"", b""
    process, guard = launch_contained_process(
        tuple(argv), cwd=cwd, env=dict(environment), shell=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    lock = threading.Lock()
    overflow = threading.Event()
    cleanup_started = threading.Event()
    io_errors: list[BaseException] = []

    def drain(name, stream):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with lock:
                    remaining = max(0, output_limit_bytes - sum(map(len, buffers.values())))
                    buffers[name].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
                        return
        except BaseException as exc:
            io_errors.append(exc)
        finally:
            stream.close()

    def feed():
        try:
            view = memoryview(input_bytes)
            while view:
                written = process.stdin.write(view[:4096])
                if not written:
                    raise OSError("stdin write made no progress")
                view = view[written:]
        except BrokenPipeError:
            pass  # Programs may deliberately finish before consuming all input.
        except OSError as exc:
            # Windows reports EINVAL when termination closes a blocked pipe.
            if not (os.name == "nt" and exc.errno == errno.EINVAL
                    and (cleanup_started.is_set() or process.poll() is not None)):
                io_errors.append(exc)
        except BaseException as exc:
            io_errors.append(exc)
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=drain, args=(name, stream), daemon=True)
               for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    threads.append(threading.Thread(target=feed, daemon=True))
    timed_out = False
    exit_code = None
    cleanup_errors: list[BaseException] = []
    try:
        for thread in threads:
            thread.start()
        while True:
            if time.monotonic() >= end:
                timed_out = True
                break
            if overflow.is_set() or io_errors:
                break
            exit_code = process.poll()
            if exit_code is not None:
                break
            overflow.wait(min(0.01, max(0, end - time.monotonic())))
    finally:
        cleanup_end = time.monotonic() + cleanup_timeout_seconds
        cleanup_started.set()
        # Terminate descendants even when their parent exited normally.
        try:
            if os.name == "nt":
                guard.terminate()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=max(0, cleanup_end - time.monotonic()))
            guard.wait_empty(deadline=cleanup_end)
        except BaseException as exc:
            cleanup_errors.append(exc)
        finally:
            try:
                guard.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, cleanup_end - time.monotonic()))
        # Do not close streams from this thread: a blocked reader can hold a lock.
        if any(thread.is_alive() for thread in threads):
            cleanup_errors.append(ProcessContainmentError("process pipe cleanup incomplete"))
        if cleanup_errors:
            raise ProcessContainmentError("bounded process containment incomplete") from cleanup_errors[0]
    if io_errors:
        raise ProcessContainmentError("bounded process pipe operation failed") from io_errors[0]
    return exit_code, timed_out, overflow.is_set(), bytes(buffers["stdout"]), bytes(buffers["stderr"])
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 03:32 UTC | END]]
