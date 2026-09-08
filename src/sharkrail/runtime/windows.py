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

    def resume(self, pid: int) -> None:
        """Resume the primary thread of a process created with CREATE_SUSPENDED."""

        if self._closed:
            raise RuntimeError("Job Object is closed")
        _resume_process(pid)

    def terminate(self, exit_code: int = 1) -> None:
        if not self._closed:
            _terminate_job(self._handle, exit_code)

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
    THREAD_SUSPEND_RESUME = 0x0002
    TH32CS_SNAPTHREAD = 0x00000004
    ERROR_NO_MORE_FILES = 18
    INVALID_DWORD = 0xFFFFFFFF

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
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(THREADENTRY32),
    )
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(THREADENTRY32),
    )
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.ResumeThread.restype = wintypes.DWORD


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


def _resume_process(pid: int) -> None:
    """Find and resume the sole primary thread of a suspended new process."""

    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        _raise_last_error("CreateToolhelp32Snapshot")
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        if not _kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            _raise_last_error("Thread32First")
        while True:
            if entry.th32OwnerProcessID == pid:
                thread = _kernel32.OpenThread(
                    THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
                )
                if not thread:
                    _raise_last_error("OpenThread")
                try:
                    if _kernel32.ResumeThread(thread) == INVALID_DWORD:
                        _raise_last_error("ResumeThread")
                finally:
                    _kernel32.CloseHandle(thread)
                return
            if not _kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != ERROR_NO_MORE_FILES:  # type: ignore[attr-defined]
                    _raise_last_error("Thread32Next")
                break
        raise OSError(f"suspended process {pid} has no primary thread")
    finally:
        _kernel32.CloseHandle(snapshot)


def _terminate_job(job: int, exit_code: int) -> None:
    if not _kernel32.TerminateJobObject(job, exit_code):
        _raise_last_error("TerminateJobObject")


def _close_handle(handle: int) -> None:
    if not _kernel32.CloseHandle(handle):
        _raise_last_error("CloseHandle")
