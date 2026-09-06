"""Apply a CPU-time limit while keeping a wall-clock deadline."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandRunner, ResourceLimits, direct_command


async def main() -> None:
    # Memory and process-count limits use the same ResourceLimits fields, but
    # their availability and semantics are more platform-specific. Discover
    # capabilities and validate those limits on the deployment host first.
    limits = ResourceLimits(cpu_time_seconds=2)
    result = await CommandRunner().run(
        direct_command(
            sys.executable,
            ("-c", "print('within-limits')"),
            resources=limits,
        ),
        timeout_ms=5_000,
    )
    print(
        json.dumps(
            {
                "reason": result.reason.value,
                "output": result.stdout.strip(),
                "requested_limits": {
                    "cpu_time_seconds": limits.cpu_time_seconds,
                },
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
