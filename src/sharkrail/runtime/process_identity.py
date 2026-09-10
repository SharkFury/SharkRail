"""Stable process birth identities used to guard forced cleanup."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from .windows import process_creation_time


def _platform_name() -> str:
    """Return the runtime platform without inviting static branch folding."""

    return sys.platform


def process_birth_identity(pid: int) -> str | None:
    """Return an identity that changes when an operating-system PID is reused."""

    if pid <= 0:
        return None
    platform = _platform_name()
    if platform == "win32":  # pragma: no cover - exercised by Windows CI
        try:
            return f"windows:{process_creation_time(pid)}"
        except OSError:
            return None
    if platform.startswith("linux"):
        try:
            stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            # The command name is parenthesized and may itself contain spaces.
            fields_after_name = stat_line.rsplit(")", 1)[1].split()
            return f"linux:{fields_after_name[19]}"
        except (FileNotFoundError, IndexError, OSError, UnicodeError):
            return None
    if platform == "darwin":
        identity = _darwin_process_birth_identity(pid)
        if identity is not None:
            return identity
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started = completed.stdout.strip()
    return f"posix:{started}" if completed.returncode == 0 and started else None


def _darwin_process_birth_identity(pid: int) -> str | None:
    class ProcBsdInfo(ctypes.Structure):
        _fields_ = [
            ("fixed", ctypes.c_uint32 * 12),
            ("command", ctypes.c_char * 16),
            ("name", ctypes.c_char * 32),
            ("process", ctypes.c_uint32 * 6),
            ("start_seconds", ctypes.c_uint64),
            ("start_microseconds", ctypes.c_uint64),
        ]

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = (
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        )
        proc_pidinfo.restype = ctypes.c_int
        information = ProcBsdInfo()
        size = ctypes.sizeof(information)
        if proc_pidinfo(pid, 3, 0, ctypes.byref(information), size) != size:
            return None
    except (AttributeError, OSError):
        return None
    return f"darwin:{information.start_seconds}:{information.start_microseconds}"
