"""Execution primitives for SharkRail."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import CommandMode, CommandSpec


class CompletionReason(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    reason: CompletionReason = CompletionReason.SUCCESS
    timed_out: bool = False


class CommandRunner:
    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    async def run(
        self,
        spec: CommandSpec,
        timeout_ms: Optional[int] = None,
    ) -> CommandResult:
        spec.validate()

        if self._dry_run:
            return CommandResult(exit_code=0, stdout="", stderr="")

        # PTY support is part of the roadmap. Foundation layer keeps a single,
        # portable execution path while exposing explicit mode for callers.
        if spec.mode == CommandMode.PTY:
            # Keep PTY explicit for compatibility. At this stage we run with
            # normal pipes and clearly expose the mode in command handling.
            pass

        proc = await asyncio.create_subprocess_exec(
            *spec.argv_list,
            cwd=spec.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            if timeout_ms is None:
                out, err = await proc.communicate()
            else:
                out, err = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_ms / 1000,
                )
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            return CommandResult(
                exit_code=124,
                stdout=out.decode(errors="ignore"),
                stderr=err.decode(errors="ignore"),
                reason=CompletionReason.TIMEOUT,
                timed_out=True,
            )

        if proc.returncode == 0:
            reason = CompletionReason.SUCCESS
        elif proc.returncode is None:
            reason = CompletionReason.FAILED
        else:
            reason = CompletionReason.FAILED

        return CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else 1,
            stdout=out.decode(errors="ignore"),
            stderr=err.decode(errors="ignore"),
            reason=reason,
            timed_out=False,
        )
