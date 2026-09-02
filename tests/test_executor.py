import asyncio
import sys

from sharkrail.executor import CommandRunner, LifecycleEventType
from sharkrail.models import CommandSpec


def test_command_runner_executes_command():
    async def _run() -> None:
        runner = CommandRunner()
        result = await runner.run(
            CommandSpec(executable=sys.executable, argv=("-c", "print('ok')"))
        )
        assert result.exit_code == 0
        assert "ok" in result.stdout
        assert result.reason.value == "success"

    asyncio.run(_run())


def test_command_runner_timeout():
    async def _run() -> None:
        runner = CommandRunner()
        result = await runner.run(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import time; time.sleep(10)"),
            ),
            timeout_ms=200,
        )
        assert result.timed_out is True
        assert result.reason.value == "timeout"
        assert result.exit_code == 124

    asyncio.run(_run())


def test_command_runner_missing_executable_is_failed():
    async def _run() -> None:
        runner = CommandRunner()
        result, events = await runner.run_events(
            CommandSpec(executable="__missing_executable__", argv=())
        )
        assert result.exit_code == 127
        assert result.reason.value == "failed"
        assert result.stdout == ""
        assert result.stderr
        assert any(event.kind == LifecycleEventType.COMPLETED for event in events)
        assert events[-1].kind == LifecycleEventType.COMPLETED
        assert events[-1].payload["reason"] == "failed"

    asyncio.run(_run())
