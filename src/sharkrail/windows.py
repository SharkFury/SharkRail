"""Minimal Windows Job Object ownership for process-tree cleanup."""

from __future__ import annotations

import os
from typing import NoReturn


class WindowsJob:
    """A kill-on-close Job Object owned by one execution session."""

    def __init__(
        self,
        *,
        memory_bytes: int | None = None,
        cpu_time_seconds: int | None = None,
        process_count: int | None = None,
    ) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are only available on Windows")
        self._handle = _create_job(
            memory_bytes=memory_bytes,
            cpu_time_seconds=cpu_time_seconds,
            process_count=process_count,
        )
        self._closed = False

    def assign(self, pid: int) -> None:
        if self._closed:
            raise RuntimeError("Job Object is closed")
        _assign_process(self._handle, pid)

    def terminate(self, exit_code: int = 1) -> None:
        if not self._closed:
            _terminate_job(self._handle, exit_code)

    def wait_empty(self, timeout_ms: int) -> bool:
        """Wait until the Job has no active processes after termination."""

        if self._closed:
            return True
        return _wait_for_job(self._handle, timeout_ms)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_handle(self._handle)

    def __enter__(self) -> WindowsJob:  # noqa: PYI034 - Python 3.9 lacks typing.Self
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        if hasattr(self, "_closed"):
            try:
                self.close()
            except OSError:
                pass


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

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

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _raise_last_error(operation: str) -> NoReturn:
    error = ctypes.get_last_error()  # type: ignore[attr-defined]
    raise OSError(error, f"{operation} failed", None, error)


def _create_job(
    *,
    memory_bytes: int | None = None,
    cpu_time_seconds: int | None = None,
    process_count: int | None = None,
) -> int:
    handle = _kernel32.CreateJobObjectW(None, None)
    if not handle:
        _raise_last_error("CreateJobObjectW")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if memory_bytes is not None:
        info.JobMemoryLimit = memory_bytes
        flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
    if cpu_time_seconds is not None:
        info.BasicLimitInformation.PerJobUserTimeLimit = cpu_time_seconds * 10_000_000
        flags |= JOB_OBJECT_LIMIT_JOB_TIME
    if process_count is not None:
        info.BasicLimitInformation.ActiveProcessLimit = process_count
        flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    info.BasicLimitInformation.LimitFlags = flags
    if not _kernel32.SetInformationJobObject(
        handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        _kernel32.CloseHandle(handle)
        _raise_last_error("SetInformationJobObject")
    return handle


def _assign_process(job: int, pid: int) -> None:
    access = PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION
    process = _kernel32.OpenProcess(access, False, pid)
    if not process:
        _raise_last_error("OpenProcess")
    try:
        if not _kernel32.AssignProcessToJobObject(job, process):
            _raise_last_error("AssignProcessToJobObject")
    finally:
        _kernel32.CloseHandle(process)


def _terminate_job(job: int, exit_code: int) -> None:
    if not _kernel32.TerminateJobObject(job, exit_code):
        _raise_last_error("TerminateJobObject")


def _wait_for_job(job: int, timeout_ms: int) -> bool:
    result = _kernel32.WaitForSingleObject(job, timeout_ms)
    if result == 0:
        return True
    if result == 258:
        return False
    _raise_last_error("WaitForSingleObject")


def _close_handle(handle: int) -> None:
    if not _kernel32.CloseHandle(handle):
        _raise_last_error("CloseHandle")
