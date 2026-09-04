"""Data models for command execution requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CommandMode(str, Enum):
    PIPE = "pipe"
    PTY = "pty"


@dataclass(frozen=True)
class ResourceLimits:
    memory_bytes: Optional[int] = None
    cpu_time_seconds: Optional[int] = None
    process_count: Optional[int] = None

    def validate(self) -> None:
        for name, value in {
            "memory_bytes": self.memory_bytes,
            "cpu_time_seconds": self.cpu_time_seconds,
            "process_count": self.process_count,
        }.items():
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class CommandSpec:
    executable: str
    argv: tuple[str, ...]
    cwd: Optional[str] = None
    env: Optional[Mapping[str, str]] = None
    mode: CommandMode = CommandMode.PIPE
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    inherit_env: bool = True

    def validate(self) -> None:
        if not self.executable or not self.executable.strip():
            raise ValueError("executable must be a non-empty string")
        if any(not arg for arg in self.argv):
            raise ValueError("argv contains empty argument")
        if self.env is not None and any(not key or "=" in key for key in self.env):
            raise ValueError("environment contains an invalid variable name")
        if not isinstance(self.inherit_env, bool):
            raise ValueError("inherit_env must be a boolean")
        if self.mode == CommandMode.PTY and not self.argv:
            raise ValueError("PTY mode requires at least one argument")
        self.resources.validate()

    @property
    def argv_list(self) -> list[str]:
        return [self.executable, *self.argv]
