"""Safe routing for direct, shell, and WSL execution requests."""

from __future__ import annotations

import ntpath
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..core.models import CommandMode, CommandSpec, ResourceLimits


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


@dataclass(frozen=True)
class WslInvocation:
    """The Linux-side execution context encoded in a WSL command line."""

    executable: str
    cwd: Optional[str]


def is_wsl_launcher(executable: str) -> bool:
    """Return whether an executable name targets the Windows WSL launcher."""

    return ntpath.basename(executable).casefold() in {"wsl", "wsl.exe"}


def parse_wsl_invocation(spec: CommandSpec) -> Optional[WslInvocation]:
    """Return the effective WSL command, rejecting non-canonical launch syntax.

    SharkRail emits long-form WSL options followed by an explicit ``--exec``
    delimiter. Policy enforcement uses this parser too, so a caller cannot evade
    checks by constructing a ``CommandSpec`` for ``wsl.exe`` directly.
    """

    if not is_wsl_launcher(spec.executable):
        return None

    value_options = {"--distribution", "--user", "--cd"}
    seen: set[str] = set()
    cwd: Optional[str] = None
    index = 0
    while index < len(spec.argv):
        option = spec.argv[index]
        if not isinstance(option, str):
            raise TypeError("WSL launcher options must be strings")
        if option == "--exec":
            if index + 1 >= len(spec.argv):
                raise ValueError("WSL --exec requires an executable")
            executable = spec.argv[index + 1]
            if not isinstance(executable, str):
                raise TypeError("WSL --exec executable must be a string")
            if not executable:
                raise ValueError("WSL --exec requires an executable")
            return WslInvocation(executable=executable, cwd=cwd)
        if option not in value_options:
            raise ValueError(f"unsupported WSL launcher option: {option}")
        if option in seen:
            raise ValueError(f"duplicate WSL launcher option: {option}")
        if index + 1 >= len(spec.argv):
            raise ValueError(f"WSL launcher option requires a value: {option}")
        value = spec.argv[index + 1]
        if not isinstance(value, str):
            raise TypeError(f"WSL launcher option value must be a string: {option}")
        if not value:
            raise ValueError(f"WSL launcher option requires a value: {option}")
        seen.add(option)
        if option == "--cd":
            cwd = value
        index += 2

    raise ValueError("WSL command requires an explicit --exec delimiter")


def shell_command(
    shell: Shell,
    script: str,
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    inherit_env: bool = True,
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
            inherit_env=inherit_env,
            options=wsl,
            resources=resources,
        )

    if shell == Shell.CMD:
        argv: tuple[str, ...] = ("/d", "/s", "/c", script)
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
        inherit_env=inherit_env,
        mode=mode,
        resources=resources or ResourceLimits(),
    )


def direct_command(
    executable: str,
    argv: tuple[str, ...] = (),
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    inherit_env: bool = True,
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
            inherit_env=inherit_env,
            options=wsl,
            resources=resources,
        )
    return CommandSpec(
        executable=executable,
        argv=argv,
        cwd=cwd,
        env=env,
        inherit_env=inherit_env,
        mode=mode,
        resources=resources or ResourceLimits(),
    )


def _wsl_spec(
    command: tuple[str, ...],
    *,
    mode: CommandMode,
    env: Optional[Mapping[str, str]],
    inherit_env: bool,
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
        inherit_env=inherit_env,
        mode=mode,
        resources=resources or ResourceLimits(),
    )
