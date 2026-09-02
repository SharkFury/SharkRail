import asyncio

from sharkrail.executor import LifecycleEventType, CommandRunner
from sharkrail.models import CommandSpec


def test_lifecycle_events_order():
    async def _run() -> None:
        runner = CommandRunner(dry_run=True)
        _, events = await runner.run_events(CommandSpec(executable="echo", argv=("hello",)))

        assert [e.kind for e in events] == [
            LifecycleEventType.ACCEPTED,
            LifecycleEventType.RUNNING,
            LifecycleEventType.COMPLETED,
        ]
        assert events[0].seq == 0

    asyncio.run(_run())


def test_lifecycle_events_for_timeout_reason():
    async def _run() -> None:
        runner = CommandRunner()
        _, events = await runner.run_events(
            CommandSpec(executable="python3", argv=("-c", "import time; time.sleep(10)")),
            timeout_ms=200,
        )

        assert any(e.kind == LifecycleEventType.OUTPUT for e in events)
        assert any(e.kind == LifecycleEventType.EXITED for e in events)
        assert any(
            e.kind == LifecycleEventType.COMPLETED and e.payload.get("reason") == "timeout"
            for e in events
        )

    asyncio.run(_run())
