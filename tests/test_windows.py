import os

import pytest

from sharkrail.backends import (
    PipeBackend,
    PtyBackend,
    WindowsPipeBackend,
    WindowsPtyBackend,
    pipe_backend,
    pty_backend,
)
from sharkrail.windows import WindowsJob


def test_platform_pipe_backend_selection():
    backend = pipe_backend()
    if os.name == "nt":
        assert isinstance(backend, WindowsPipeBackend)
    else:
        assert type(backend) is PipeBackend


@pytest.mark.skipif(os.name == "nt", reason="non-Windows guard assertion")
def test_windows_job_has_explicit_platform_guard():
    with pytest.raises(OSError, match="only available on Windows"):
        WindowsJob()


def test_platform_pty_backend_selection():
    backend = pty_backend()
    if os.name == "nt":
        assert isinstance(backend, WindowsPtyBackend)
    else:
        assert type(backend) is PtyBackend
