"""Retain bounded output and report exactly how many bytes were dropped."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandRunner, CommandSpec


async def main() -> None:
    result = await CommandRunner(max_output_bytes=32).run(
        CommandSpec(sys.executable, ("-c", "print('x' * 100, end='')")),
        timeout_ms=5_000,
    )
    print(
        json.dumps(
            {
                "retained_bytes": result.retained_output_bytes,
                "dropped_bytes": result.truncated_output_bytes,
                "truncated": result.output_truncated,
                "output": result.stdout,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
