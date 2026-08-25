# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
"""Cross-platform process-tree ownership for bounded runner execution."""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from typing import Any, Sequence


class ProcessContainmentError(RuntimeError):
    """The child could not be launched inside an enforceable process boundary."""


class ProcessTreeGuard:
    """Own a mandatory Windows kill-on-close Job Object; remain a no-op on POSIX."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._job: int | None = None
        self._kernel32: object | None = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ProcessContainmentError("Windows Job Object creation failed")
        information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(information), ctypes.sizeof(information)
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            job, wintypes.HANDLE(int(process._handle))
        )
        if not assigned:
            kernel32.CloseHandle(job)
            raise ProcessContainmentError(
                "Windows process could not be assigned to its Job Object"
            )
        self._job = int(job)
        self._kernel32 = kernel32

    @property
    def active(self) -> bool:
        return self._job is not None

    def terminate(self) -> None:
        if self._job is None or os.name != "nt":
            return
        assert self._kernel32 is not None
        if not self._kernel32.TerminateJobObject(wintypes.HANDLE(self._job), 1):
            raise ProcessContainmentError("Windows Job Object termination failed")

    def close(self) -> None:
        if self._job is None or os.name != "nt":
            return
        handle, self._job = self._job, None
        assert self._kernel32 is not None
        if not self._kernel32.CloseHandle(wintypes.HANDLE(handle)):
            raise ProcessContainmentError("Windows Job Object close failed")

    def __enter__(self) -> "ProcessTreeGuard":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        # Last-resort cleanup for an exception that escapes a runner before its
        # explicit fail-closed cleanup path. Evidence is already rejected.
        if self._job is None:
            return
        try:
            self.terminate()
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]


def launch_contained_process(
    argv: Sequence[str],
    **kwargs: Any,
) -> tuple[subprocess.Popen[bytes], ProcessTreeGuard]:
    """Launch before user code can run, attach containment, then resume on Windows."""

    if os.name != "nt":
        kwargs["start_new_session"] = True
        process = subprocess.Popen(tuple(argv), **kwargs)
        return process, ProcessTreeGuard(process)

    creationflags = int(kwargs.pop("creationflags", 0))
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    creationflags |= 0x00000004  # CREATE_SUSPENDED
    process = subprocess.Popen(tuple(argv), creationflags=creationflags, **kwargs)
    guard: ProcessTreeGuard | None = None
    try:
        guard = ProcessTreeGuard(process)
        _resume_only_thread(process.pid)
        return process, guard
    except Exception:
        if guard is not None:
            try:
                guard.terminate()
            finally:
                guard.close()
        else:
            process.kill()
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise


def _resume_only_thread(process_id: int) -> None:
    """Resume the sole primary thread created by CREATE_SUSPENDED."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid:
        raise ProcessContainmentError("Windows thread snapshot failed")
    thread_ids: list[int] = []
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while has_entry:
            if int(entry.th32OwnerProcessID) == process_id:
                thread_ids.append(int(entry.th32ThreadID))
            has_entry = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    if len(thread_ids) != 1:
        raise ProcessContainmentError(
            "suspended Windows child did not have exactly one primary thread"
        )
    thread = kernel32.OpenThread(0x0002, False, thread_ids[0])
    if not thread:
        raise ProcessContainmentError("Windows primary thread could not be opened")
    try:
        previous_count = int(kernel32.ResumeThread(thread))
    finally:
        kernel32.CloseHandle(thread)
    if previous_count != 1:
        raise ProcessContainmentError("Windows primary thread resume was ambiguous")
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:03 UTC | END]]
