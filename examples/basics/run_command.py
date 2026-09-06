"""Run one command with structured output and completion information."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandRunner, CommandSpec


async def main() -> None:
    result = await CommandRunner().run(
        CommandSpec(
            sys.executable,
            ("-c", "import sys; print('hello'); print('warning', file=sys.stderr)"),
        ),
        timeout_ms=5_000,
    )
    print(
        json.dumps(
            {
                "exit_code": result.exit_code,
                "reason": result.reason.value,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
