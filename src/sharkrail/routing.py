"""Safe routing for direct, shell, and WSL execution requests."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import CommandMode, CommandSpec, ResourceLimits


class Shell(str, Enum):
    CMD = "cmd"
    POWERSHELL = "powershell"
    PWSH = "pwsh"
    BASH = "bash"
    ZSH = "zsh"


class Target(str, Enum):
    NATIVE = "native"
    WSL = "wsl"


@dataclass(frozen=True)
class WslOptions:
    distribution: Optional[str] = None
    user: Optional[str] = None
    cwd: Optional[str] = None


def shell_command(
    shell: Shell,
    script: str,
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    mode: CommandMode = CommandMode.PIPE,
    target: Target = Target.NATIVE,
    wsl: Optional[WslOptions] = None,
    resources: Optional[ResourceLimits] = None,
) -> CommandSpec:
    if not script:
        raise ValueError("shell script must be non-empty")
    if target == Target.WSL:
        if shell not in {Shell.BASH, Shell.ZSH}:
            raise ValueError("WSL target supports bash and zsh shell requests")
        return _wsl_spec(
            (shell.value, "-lc", script),
            mode=mode,
            env=env,
            options=wsl,
            resources=resources,
        )

    if shell == Shell.CMD:
        argv = ("/d", "/s", "/c", script)
        executable = "cmd.exe"
    elif shell == Shell.POWERSHELL:
        argv = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script)
        executable = "powershell.exe" if os.name == "nt" else "powershell"
    elif shell == Shell.PWSH:
        argv = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script)
        executable = "pwsh"
    else:
        executable = shell.value
        argv = ("-lc", script)
    return CommandSpec(
        executable=executable,
        argv=argv,
        cwd=cwd,
        env=env,
        mode=mode,
        resources=resources or ResourceLimits(),
    )


def direct_command(
    executable: str,
    argv: tuple[str, ...] = (),
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    mode: CommandMode = CommandMode.PIPE,
    target: Target = Target.NATIVE,
    wsl: Optional[WslOptions] = None,
    resources: Optional[ResourceLimits] = None,
) -> CommandSpec:
    if target == Target.WSL:
        return _wsl_spec(
            (executable, *argv),
            mode=mode,
            env=env,
            options=wsl,
            resources=resources,
        )
    return CommandSpec(
        executable=executable,
        argv=argv,
        cwd=cwd,
        env=env,
        mode=mode,
        resources=resources or ResourceLimits(),
    )


def _wsl_spec(
    command: tuple[str, ...],
    *,
    mode: CommandMode,
    env: Optional[Mapping[str, str]],
    options: Optional[WslOptions],
    resources: Optional[ResourceLimits],
) -> CommandSpec:
    options = options or WslOptions()
    prefix: list[str] = []
    if options.distribution:
        prefix.extend(("--distribution", options.distribution))
    if options.user:
        prefix.extend(("--user", options.user))
    if options.cwd:
        prefix.extend(("--cd", options.cwd))
    prefix.append("--exec")
    return CommandSpec(
        executable="wsl.exe",
        argv=tuple(prefix) + command,
        env=env,
        mode=mode,
        resources=resources or ResourceLimits(),
    )
