import asyncio
import os
import signal
import sys

import pytest

from sharkrail.backends import (
    CancellationPolicy,
    CancellationStep,
    PipeBackend,
    cancel_process,
    wait_for_exit,
)
from sharkrail.models import CommandSpec


def test_pipe_backend_supports_stdin_and_eof():
    async def _run() -> None:
        backend = PipeBackend()
        handle = await backend.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import sys; print(sys.stdin.read().upper())"),
            )
        )
        await backend.write(handle, b"hello")
        await backend.close_stdin(handle)
        assert handle.process.stdout is not None
        assert handle.process.stderr is not None
        stdout, stderr = await asyncio.gather(
            handle.process.stdout.read(),
            handle.process.stderr.read(),
        )
        await handle.process.wait()

        assert stdout.splitlines() == [b"HELLO"]
        assert stderr == b""

    asyncio.run(_run())


def test_wait_for_exit_reports_deadline():
    async def _run() -> None:
        backend = PipeBackend()
        handle = await backend.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        assert await wait_for_exit(handle, 0.01) is False
        await backend.kill_tree(handle)
        assert await wait_for_exit(handle, 2) is True

    asyncio.run(_run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group assertion")
def test_pipe_backend_creates_dedicated_process_group():
    async def _run() -> None:
        backend = PipeBackend()
        handle = await backend.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        assert os.getpgid(handle.pid) == handle.pid
        await backend.interrupt(handle)
        assert await wait_for_exit(handle, 2) is True
        assert handle.process.returncode == -signal.SIGINT

    asyncio.run(_run())


@pytest.mark.skipif(os.name == "nt", reason="fixture uses POSIX signal handlers")
def test_cancel_process_escalates_when_interrupt_is_ignored():
    async def _run() -> None:
        backend = PipeBackend()
        code = (
            "import signal,time; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(5)"
        )
        handle = await backend.start(CommandSpec(executable=sys.executable, argv=("-c", code)))
        assert handle.process.stdout is not None
        assert await handle.process.stdout.readline() == b"ready\n"

        steps = await cancel_process(
            backend,
            handle,
            CancellationPolicy(interrupt_grace_ms=10, terminate_grace_ms=1000),
        )

        assert steps == (CancellationStep.INTERRUPT, CancellationStep.TERMINATE)
        assert handle.process.returncode == -signal.SIGTERM

    asyncio.run(_run())


def test_cancel_process_can_skip_soft_interrupt():
    async def _run() -> None:
        backend = PipeBackend()
        handle = await backend.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        steps = await cancel_process(
            backend,
            handle,
            CancellationPolicy(skip_interrupt=True, terminate_grace_ms=1000),
        )
        assert steps[0] == CancellationStep.TERMINATE
        assert CancellationStep.INTERRUPT not in steps

    asyncio.run(_run())


def test_pipe_backend_environment_is_an_overlay():
    async def _run() -> None:
        backend = PipeBackend()
        handle = await backend.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import os; print(os.environ['SHARKRAIL_TEST']); print(bool(os.environ.get('PATH')))"),
                env={"SHARKRAIL_TEST": "present"},
            )
        )
        stdout, _ = await handle.process.communicate()
        assert stdout.splitlines() == [b"present", b"True"]

    asyncio.run(_run())
