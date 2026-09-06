"""Build and optionally run a structured Windows-to-WSL command."""

from __future__ import annotations

import argparse
import asyncio
import json

from sharkrail import CommandRunner, Target, WslOptions, collect, direct_command


async def run(distribution: str | None) -> None:
    spec = direct_command(
        "python3",
        ("-c", "print('wsl-ok')"),
        target=Target.WSL,
        wsl=WslOptions(distribution=distribution),
    )
    if "wsl" not in collect().targets or distribution is None:
        print(
            json.dumps(
                {
                    "skipped": "pass --distribution on a Windows host with WSL",
                    "structured_argv": spec.argv_list,
                }
            )
        )
        return
    result = await CommandRunner().run(spec, timeout_ms=10_000)
    print(json.dumps({"reason": result.reason.value, "output": result.stdout.strip()}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", help="installed WSL distribution name")
    args = parser.parse_args()
    asyncio.run(run(args.distribution))


if __name__ == "__main__":
    main()
