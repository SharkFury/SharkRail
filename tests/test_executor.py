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
        assert any(event.kind == LifecycleEventType.SESSION_ERROR for event in events)
        assert events[-1].kind == LifecycleEventType.SESSION_COMPLETED
        assert events[-1].payload["reason"] == "failed"

    asyncio.run(_run())


def test_command_runner_output_is_truncated():
    async def _run() -> None:
        runner = CommandRunner(max_output_bytes=10)
        result = await runner.run(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "print('aaaaaaaaaaaaaaaaaaaaaaaaaaaa')"),
            )
        )
        assert result.stdout
        assert len(result.stdout) <= 10
        assert result.output_truncated is True
        assert result.retained_output_bytes == 10
        assert result.truncated_output_bytes > 0

    asyncio.run(_run())


def test_command_runner_reports_structured_start_error():
    async def _run() -> None:
        runner = CommandRunner()
        result = await runner.run(CommandSpec(executable="__missing_executable__", argv=()))

        assert result.error is not None
        assert result.error.to_dict()["code"] == "EXECUTABLE_NOT_FOUND"
        assert result.error.to_dict()["stage"] == "start"

    asyncio.run(_run())
