"""CLI for SharkRail."""

from __future__ import annotations

import argparse
import asyncio

from .executor import CommandRunner
from .models import CommandMode, CommandSpec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharkrail",
        description="SharkRail: Native execution rails for AI agents.",
    )

    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run a command")
    run.add_argument("executable", help="Executable binary name")
    run.add_argument("args", nargs="*", help="Arguments passed to executable")
    run.add_argument("--mode", choices=["pipe", "pty"], default="pipe")
    run.add_argument("--timeout-ms", type=int, default=None)
    run.add_argument("--cwd", default=None)
    run.add_argument("--dry-run", action="store_true")

    return parser


async def _run_cmd(ns: argparse.Namespace) -> int:
    spec = CommandSpec(
        executable=ns.executable,
        argv=tuple(ns.args),
        cwd=ns.cwd,
        mode=CommandMode(ns.mode),
    )
    result = await CommandRunner(dry_run=ns.dry_run).run(spec, timeout_ms=ns.timeout_ms)
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

