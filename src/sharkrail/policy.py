"""Host-enforced execution policy for agent-controlled commands."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import CommandSpec


class PolicyViolation(ValueError):
    """A request violates a named execution-policy rule."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"execution denied by policy rule: {rule}")
        self.rule = rule


@dataclass(frozen=True)
class ExecutionPolicy:
    """Optional deny-by-rule guardrails applied before process creation."""

    allowed_executables: frozenset[str] | None = None
    denied_executables: frozenset[str] = frozenset()
    allowed_cwd_roots: tuple[Path, ...] = ()
    allowed_env_keys: frozenset[str] | None = None
    allow_parent_environment: bool = True
    require_absolute_executable: bool = False
    require_timeout: bool = False
    max_timeout_ms: int | None = None
    max_output_bytes: int | None = None
    max_memory_bytes: int | None = None
    max_cpu_time_seconds: int | None = None
    max_process_count: int | None = None

    def enforce(
        self,
        spec: CommandSpec,
        *,
        timeout_ms: int | None,
        max_output_bytes: int | None,
    ) -> None:
        requested = _command_names(spec.executable)
        if self.denied_executables and requested & _normalized(self.denied_executables):
            raise PolicyViolation("denied_executables")
        if (
            self.allowed_executables is not None
            and not requested & _normalized(self.allowed_executables)
        ):
            raise PolicyViolation("allowed_executables")
        if self.require_absolute_executable and not Path(spec.executable).is_absolute():
            raise PolicyViolation("require_absolute_executable")
        if self.allowed_cwd_roots:
            cwd = Path(spec.cwd or os.getcwd()).resolve()
            if not any(_is_within(cwd, root.resolve()) for root in self.allowed_cwd_roots):
                raise PolicyViolation("allowed_cwd_roots")
        if not self.allow_parent_environment and spec.inherit_env:
            raise PolicyViolation("allow_parent_environment")
        if self.allowed_env_keys is not None and spec.env is not None:
            unexpected = set(spec.env) - set(self.allowed_env_keys)
            if unexpected:
                raise PolicyViolation("allowed_env_keys")
        if self.require_timeout and timeout_ms is None:
            raise PolicyViolation("require_timeout")
        _enforce_ceiling("max_timeout_ms", timeout_ms, self.max_timeout_ms)
        _enforce_ceiling("max_output_bytes", max_output_bytes, self.max_output_bytes)
        _enforce_ceiling(
            "max_memory_bytes",
            spec.resources.memory_bytes,
            self.max_memory_bytes,
        )
        _enforce_ceiling(
            "max_cpu_time_seconds",
            spec.resources.cpu_time_seconds,
            self.max_cpu_time_seconds,
        )
        _enforce_ceiling(
            "max_process_count",
            spec.resources.process_count,
            self.max_process_count,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutionPolicy:
        known = {
            "allowed_executables",
            "denied_executables",
            "allowed_cwd_roots",
            "allowed_env_keys",
            "allow_parent_environment",
            "require_absolute_executable",
            "require_timeout",
            "max_timeout_ms",
            "max_output_bytes",
            "max_memory_bytes",
            "max_cpu_time_seconds",
            "max_process_count",
        }
        unknown = set(value) - known
        if unknown:
            raise ValueError(f"unknown execution policy fields: {', '.join(sorted(unknown))}")
        return cls(
            allowed_executables=_optional_strings(value, "allowed_executables"),
            denied_executables=_optional_strings(value, "denied_executables") or frozenset(),
            allowed_cwd_roots=tuple(
                Path(item) for item in _strings(value, "allowed_cwd_roots", default=())
            ),
            allowed_env_keys=_optional_strings(value, "allowed_env_keys"),
            allow_parent_environment=_boolean(value, "allow_parent_environment", True),
            require_absolute_executable=_boolean(
                value, "require_absolute_executable", False
            ),
            require_timeout=_boolean(value, "require_timeout", False),
            max_timeout_ms=_optional_positive_int(value, "max_timeout_ms"),
            max_output_bytes=_optional_positive_int(value, "max_output_bytes"),
            max_memory_bytes=_optional_positive_int(value, "max_memory_bytes"),
            max_cpu_time_seconds=_optional_positive_int(
                value, "max_cpu_time_seconds"
            ),
            max_process_count=_optional_positive_int(value, "max_process_count"),
        )

    @classmethod
    def from_json(cls, path: Path) -> ExecutionPolicy:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("execution policy must be a JSON object")
        return cls.from_dict(value)


def _command_names(executable: str) -> set[str]:
    return _normalized((executable, Path(executable).name))


def _normalized(values: Iterable[object]) -> set[str]:
    return {os.path.normcase(str(value)) for value in values}


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((path, root)) == str(root)
    except ValueError:
        return False


def _enforce_ceiling(rule: str, value: int | None, ceiling: int | None) -> None:
    if ceiling is not None and value is not None and value > ceiling:
        raise PolicyViolation(rule)


def _strings(
    value: dict[str, Any], key: str, *, default: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    raw = value.get(key, default)
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(raw)


def _optional_strings(value: dict[str, Any], key: str) -> frozenset[str] | None:
    if key not in value:
        return None
    return frozenset(_strings(value, key))


def _boolean(value: dict[str, Any], key: str, default: bool) -> bool:
    raw = value.get(key, default)
    if not isinstance(raw, bool):
        raise TypeError(f"{key} must be a boolean")
    return raw


def _optional_positive_int(value: dict[str, Any], key: str) -> int | None:
    raw = value.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return raw
