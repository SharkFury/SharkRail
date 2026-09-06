"""Platform capability reporting."""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass, field
from importlib.util import find_spec


@dataclass(frozen=True)
class Capability:
    contract_version: str
    platform_name: str
    process_tree: str
    modes: tuple[str, ...]
    max_output_bytes: int
    supports_timeout: bool
    features: tuple[str, ...]
    targets: tuple[str, ...]
    shells: tuple[str, ...]
    degraded_reasons: tuple[str, ...] = ()
    process_tree_fallbacks: tuple[str, ...] = ()
    resource_limits: tuple[str, ...] = ()
    verification: dict[str, str] = field(default_factory=dict)


def collect() -> Capability:
    current = platform.system().lower()
    if current == "windows":
        pty_available = find_spec("winpty") is not None
        wsl_available = shutil.which("wsl.exe") is not None
        shells = tuple(
            shell
            for shell, executable in (
                ("cmd", "cmd.exe"),
                ("powershell", "powershell.exe"),
                ("pwsh", "pwsh.exe"),
            )
            if shutil.which(executable) is not None
        )
        modes = ("pipe", "pty") if pty_available else ("pipe",)
        features = [
            "session_lifecycle",
            "exit_reasons",
            "capabilities",
            "process_tree_kill",
            "resource_limits",
        ]
        degraded: list[str] = []
        if pty_available:
            features.extend(("pty", "resize"))
        else:
            degraded.append("pywinpty is unavailable; ConPTY is disabled")
        if not wsl_available:
            degraded.append("wsl.exe is unavailable; WSL target is disabled")
        return Capability(
            contract_version="1.0.0",
            platform_name="windows",
            process_tree="job_object_or_taskkill",
            modes=modes,
            max_output_bytes=16 * 1024 * 1024,
            supports_timeout=True,
            features=tuple(features),
            targets=("native", "wsl") if wsl_available else ("native",),
            shells=shells,
            degraded_reasons=tuple(degraded),
            process_tree_fallbacks=("taskkill_fallback",),
            resource_limits=("memory_bytes", "cpu_time_seconds", "process_count"),
            verification={
                "report": "discovery_only",
                "pipe": "built_in_not_probed",
                "pty": (
                    "dependency_present_not_probed" if pty_available else "unavailable"
                ),
                "process_tree": "selected_at_session_start",
                "wsl": (
                    "executable_present_not_probed" if wsl_available else "unavailable"
                ),
            },
        )

    if current == "darwin":
        shells = tuple(
            shell for shell in ("bash", "zsh", "pwsh") if shutil.which(shell)
        )
        return Capability(
            contract_version="1.0.0",
            platform_name="macos",
            process_tree="process_group",
            modes=("pipe", "pty"),
            max_output_bytes=16 * 1024 * 1024,
            supports_timeout=True,
            features=(
                "session_lifecycle",
                "exit_reasons",
                "capabilities",
                "process_tree_kill",
                "pty",
                "resize",
                "resource_limits",
            ),
            targets=("native",),
            shells=shells,
            resource_limits=("memory_bytes", "cpu_time_seconds", "process_count"),
            verification={
                "report": "discovery_only",
                "pipe": "built_in_not_probed",
                "pty": "platform_api_present_not_probed",
                "process_tree": "configured_not_probed",
            },
        )

    shells = tuple(shell for shell in ("bash", "zsh", "pwsh") if shutil.which(shell))
    return Capability(
        contract_version="1.0.0",
        platform_name="linux",
        process_tree="process_group",
        modes=("pipe", "pty"),
        max_output_bytes=16 * 1024 * 1024,
        supports_timeout=True,
        features=(
            "session_lifecycle",
            "exit_reasons",
            "capabilities",
            "process_tree_kill",
            "pty",
            "resize",
            "resource_limits",
        ),
        targets=("native",),
        shells=shells,
        resource_limits=("memory_bytes", "cpu_time_seconds", "process_count"),
        verification={
            "report": "discovery_only",
            "pipe": "built_in_not_probed",
            "pty": "platform_api_present_not_probed",
            "process_tree": "configured_not_probed",
        },
    )
