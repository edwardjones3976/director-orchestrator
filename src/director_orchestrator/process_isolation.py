# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | BEGIN]]
"""Cross-platform process-tree ownership for bounded runner execution."""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes


class ProcessTreeGuard:
    """Own a Windows Job Object when available; remain a no-op on POSIX."""

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
            return
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
            return
        self._job = int(job)
        self._kernel32 = kernel32

    @property
    def active(self) -> bool:
        return self._job is not None

    def terminate(self) -> None:
        if self._job is None or os.name != "nt":
            return
        assert self._kernel32 is not None
        self._kernel32.TerminateJobObject(wintypes.HANDLE(self._job), 1)

    def close(self) -> None:
        if self._job is None or os.name != "nt":
            return
        handle, self._job = self._job, None
        assert self._kernel32 is not None
        self._kernel32.CloseHandle(wintypes.HANDLE(handle))

    def __enter__(self) -> "ProcessTreeGuard":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 07:25 UTC | END]]
