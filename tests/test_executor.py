import asyncio

from sharkrail.executor import CommandRunner
from sharkrail.models import CommandSpec


def test_command_runner_executes_command():
    async def _run() -> None:
        runner = CommandRunner()
        result = await runner.run(CommandSpec(executable="python", argv=("-c", "print('ok')")))
        assert result.exit_code == 0
        assert "ok" in result.stdout

    asyncio.run(_run())


def test_command_runner_timeout():
    async def _run() -> None:
        runner = CommandRunner()
        result = await runner.run(
            CommandSpec(
                executable="python",
                argv=("-c", "import time; time.sleep(10)"),
            ),
            timeout_ms=200,
        )
        assert result.timed_out is True
        assert result.exit_code == 124

    asyncio.run(_run())

