import asyncio
import os
import sys
from unittest.mock import patch

import pytest

from sharkrail.core.models import CommandMode, CommandSpec
from sharkrail.runtime.backends import PtyBackend, read_pty_output
from sharkrail.runtime.executor import CommandRunner

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX PTY backend")


def test_pty_command_observes_a_real_terminal():
    async def _run() -> None:
        result = await CommandRunner().run(
            CommandSpec(
                executable=sys.executable,
                argv=(
                    "-c",
                    "import os; print(os.isatty(0), os.isatty(1), os.isatty(2))",
                ),
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
            CommandSpec(
                executable=sys.executable, argv=("-c", code), mode=CommandMode.PTY
            )
        )
        output_task = asyncio.create_task(read_pty_output(backend, handle))
        await backend.resize(handle, cols=100, rows=40)
        await backend.write(handle, b"hello\n")
        await handle.process.wait()
        output = await output_task
        await backend.dispose(handle)

        assert b"hello 100 40" in output

    asyncio.run(_run())


def test_pty_close_stdin_delivers_unterminated_input_then_eof():
    async def _run() -> None:
        backend = PtyBackend()
        handle = await backend.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import sys; print(repr(sys.stdin.read()))"),
                mode=CommandMode.PTY,
            )
        )
        output_task = asyncio.create_task(read_pty_output(backend, handle))
        await backend.write(handle, b"unterminated")
        await backend.close_stdin(handle)
        await asyncio.wait_for(handle.process.wait(), timeout=2)
        output = await output_task
        await backend.dispose(handle)

        assert b"unterminated" in output

    asyncio.run(_run())


def test_pty_close_stdin_fails_closed_in_raw_mode():
    async def _run() -> None:
        backend = PtyBackend()
        handle = await backend.start(
            CommandSpec(
                executable=sys.executable,
                argv=(
                    "-c",
                    "import time,tty; tty.setraw(0); print('ready', flush=True); time.sleep(30)",
                ),
                mode=CommandMode.PTY,
            )
        )
        try:
            assert b"ready" in await asyncio.wait_for(backend.read(handle), timeout=1)
            with pytest.raises(RuntimeError, match="non-canonical mode"):
                await backend.close_stdin(handle)
            assert handle.stdin_closed is False
        finally:
            await backend.kill_tree(handle)
            await asyncio.wait_for(handle.process.wait(), timeout=2)
            await backend.dispose(handle)

    asyncio.run(_run())


def test_pty_backpressure_is_cancellable_without_blocking_worker_threads():
    async def _run() -> None:
        backend = PtyBackend()
        handle = await backend.start(
            CommandSpec(
                executable=sys.executable,
                argv=(
                    "-c",
                    "import sys,time,tty; tty.setraw(0); print('ready', flush=True); time.sleep(30)",
                ),
                mode=CommandMode.PTY,
            )
        )
        try:
            assert b"ready" in await asyncio.wait_for(backend.read(handle), timeout=1)
            with patch(
                "sharkrail.runtime.backends.asyncio.to_thread",
                side_effect=AssertionError("POSIX PTY I/O must not use worker threads"),
            ):
                writer = asyncio.create_task(backend.write(handle, b"x" * (2 << 20)))
                await asyncio.sleep(0.05)
                assert not writer.done()
                writer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(writer, timeout=1)
        finally:
            await backend.kill_tree(handle)
            await asyncio.wait_for(handle.process.wait(), timeout=2)
            await backend.dispose(handle)

    asyncio.run(_run())
