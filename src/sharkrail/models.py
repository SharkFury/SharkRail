"""Data models for command execution requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CommandMode(str, Enum):
    PIPE = "pipe"
    PTY = "pty"


@dataclass(frozen=True)
class CommandSpec:
    executable: str
    argv: tuple[str, ...]
    cwd: Optional[str] = None
    env: Optional[Mapping[str, str]] = None
    mode: CommandMode = CommandMode.PIPE

    def validate(self) -> None:
        if not self.executable or not self.executable.strip():
            raise ValueError("executable must be a non-empty string")
        if any(not arg for arg in self.argv):
            raise ValueError("argv contains empty argument")
        if self.env is not None and any(not key or "=" in key for key in self.env):
            raise ValueError("environment contains an invalid variable name")
        if self.mode == CommandMode.PTY and not self.argv:
            raise ValueError("PTY mode requires at least one argument")

    @property
    def argv_list(self) -> list[str]:
        return [self.executable, *self.argv]
