import asyncio
import os
import sys

import pytest

from sharkrail.backends import PtyBackend, read_pty_output
from sharkrail.executor import CommandRunner
from sharkrail.models import CommandMode, CommandSpec

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX PTY backend")


def test_pty_command_observes_a_real_terminal():
    async def _run() -> None:
        result = await CommandRunner().run(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import os; print(os.isatty(0), os.isatty(1), os.isatty(2))"),
                mode=CommandMode.PTY,
            )
        )
        assert result.exit_code == 0
        assert "True True True" in result.stdout
        assert result.stderr == ""

    asyncio.run(_run())


def test_pty_backend_writes_and_resizes_terminal():
    async def _run() -> None:
        backend = PtyBackend()
        code = (
            "import os,struct,sys,termios; "
            "data=sys.stdin.readline(); "
            "size=struct.unpack('HHHH', termios.tcgetwinsize(0) if False else "
            "__import__('fcntl').ioctl(0, termios.TIOCGWINSZ, bytes(8))); "
            "print(data.strip(), size[1], size[0])"
        )
        handle = await backend.start(
            CommandSpec(executable=sys.executable, argv=("-c", code), mode=CommandMode.PTY)
        )
        output_task = asyncio.create_task(read_pty_output(backend, handle))
        await backend.resize(handle, cols=100, rows=40)
        await backend.write(handle, b"hello\n")
        await handle.process.wait()
        output = await output_task
        await backend.dispose(handle)

        assert b"hello 100 40" in output

    asyncio.run(_run())
