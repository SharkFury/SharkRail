"""CLI for SharkRail."""

from __future__ import annotations

import argparse
import asyncio
import json

from . import __version__
from .executor import CommandRunner
from .models import CommandMode, CommandSpec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharkrail",
        description="SharkRail: Native execution rails for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"sharkrail {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run a command")
    run.add_argument("executable", help="Executable binary name")
    run.add_argument("args", nargs="*", help="Arguments passed to executable")
    run.add_argument("--mode", choices=["pipe", "pty"], default="pipe")
    run.add_argument("--timeout-ms", type=int, default=None)
    run.add_argument("--cwd", default=None)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true", help="Print machine-readable output")

    return parser


async def _run_cmd(ns: argparse.Namespace) -> int:
    spec = CommandSpec(
        executable=ns.executable,
        argv=tuple(ns.args),
        cwd=ns.cwd,
        mode=CommandMode(ns.mode),
    )
    result = await CommandRunner(dry_run=ns.dry_run).run(spec, timeout_ms=ns.timeout_ms)
    if ns.json:
        print(
            json.dumps(
                {
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "reason": result.reason.value,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                ensure_ascii=False,
            )
        )
        if result.reason.value == "timeout":
            return 124
        if result.exit_code != 0:
            return result.exit_code
        return 0

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    return 1 if result.timed_out else result.exit_code


def main() -> int:
    parser = build_parser()
    ns = parser.parse_args()

    if ns.command == "run":
        return asyncio.run(_run_cmd(ns))

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
