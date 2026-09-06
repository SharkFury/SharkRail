"""Enforce a host-owned policy before an agent command starts."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandRunner, CommandSpec, ExecutionPolicy


async def main() -> None:
    policy = ExecutionPolicy(
        allowed_executables=frozenset({sys.executable}),
        require_timeout=True,
        max_timeout_ms=5_000,
        max_output_bytes=1_024,
    )
    runner = CommandRunner(policy=policy, max_output_bytes=1_024)
    allowed = await runner.run(
        CommandSpec(sys.executable, ("-c", "print('allowed')")), timeout_ms=1_000
    )
    denied = await runner.run(CommandSpec("not-allowed", ()), timeout_ms=1_000)
    print(
        json.dumps(
            {
                "allowed": allowed.reason.value,
                "denied_code": denied.error.code.value if denied.error else None,
                "denied_rule": denied.error.native.get("rule")
                if denied.error
                else None,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
