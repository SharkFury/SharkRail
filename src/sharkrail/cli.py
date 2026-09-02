"""CLI for SharkRail."""

from __future__ import annotations

import argparse
import asyncio
import json

from . import __version__
from .executor import CommandRunner
from .models import CommandMode, CommandSpec
from .capabilities import collect


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
    run.add_argument("--events", action="store_true", help="Emit lifecycle events")
    run.add_argument("--max-output-bytes", type=int, default=None, help="Trim stdout/stderr to this byte budget")

    caps = subparsers.add_parser("capabilities", help="Print runtime capability contract")
    caps.add_argument("--json", action="store_true", help="Print machine-readable output")

    return parser


async def _run_cmd(ns: argparse.Namespace) -> int:
    spec = CommandSpec(
        executable=ns.executable,
        argv=tuple(ns.args),
        cwd=ns.cwd,
        mode=CommandMode(ns.mode),
    )
    runner = CommandRunner(dry_run=ns.dry_run, max_output_bytes=ns.max_output_bytes)

    events: list[dict[str, object]] = []
    if ns.events:
        result, raw_events = await runner.run_events(spec, timeout_ms=ns.timeout_ms)
        events = [
            {
                "seq": event.seq,
                "kind": event.kind.value,
                "payload": event.payload,
            }
            for event in raw_events
        ]
    else:
        result = await runner.run(spec, timeout_ms=ns.timeout_ms)

    if ns.json:
        print(
            json.dumps(
                {
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "reason": result.reason.value,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "output_truncated": result.output_truncated,
                    **({"events": events} if ns.events else {}),
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

    if ns.command == "capabilities":
        capability = collect()
        if ns.json:
            print(
                json.dumps(
                    {
                        "contract_version": capability.contract_version,
                        "platform": capability.platform_name,
                        "modes": capability.modes,
                        "process_tree": capability.process_tree,
                        "supports_timeout": capability.supports_timeout,
                        "max_output_bytes": capability.max_output_bytes,
                        "features": capability.features,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        print(
            "protocol v"
            + capability.contract_version
            + ", platform: "
            + capability.platform_name
            + ", modes: "
            + ", ".join(capability.modes)
            + ", process_tree: "
            + capability.process_tree
            + ", features: "
            + ", ".join(capability.features)
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
