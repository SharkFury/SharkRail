"""Runtime diagnostics with secret-free, machine-readable results."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass

from . import __version__
from .capabilities import collect


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    runtime_version: str
    protocol_version: str
    platform: str
    platform_release: str
    architecture: str
    python_version: str
    process_tree: str
    modes: tuple[str, ...]
    targets: tuple[str, ...]
    max_output_bytes: int
    checks: tuple[Check, ...]

    @property
    def healthy(self) -> bool:
        return all(check.status != "fail" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["healthy"] = self.healthy
        return result


def diagnose() -> DoctorReport:
    capability = collect()
    checks: list[Check] = [
        Check("python", "pass", sys.executable),
        Check("pipe", "pass", "async subprocess pipes are available"),
    ]
    if "pty" in capability.modes:
        checks.append(Check("pty", "pass", "native POSIX PTY backend is available"))
    else:
        checks.append(Check("pty", "warn", "PTY backend is not available in this build"))

    for shell in capability.shells:
        executable = "cmd.exe" if shell == "cmd" else (
            "powershell.exe" if shell == "powershell" and os.name == "nt" else shell
        )
        path = shutil.which(executable)
        checks.append(
            Check(
                f"shell:{shell}",
                "pass" if path else "warn",
                path or "not found on PATH",
            )
        )
    if "wsl" in capability.targets:
        path = shutil.which("wsl.exe")
        checks.append(Check("wsl", "pass" if path else "warn", path or "wsl.exe not found"))

    return DoctorReport(
        runtime_version=__version__,
        protocol_version=capability.contract_version,
        platform=capability.platform_name,
        platform_release=platform.release(),
        architecture=platform.machine(),
        python_version=platform.python_version(),
        process_tree=capability.process_tree,
        modes=capability.modes,
        targets=capability.targets,
        max_output_bytes=capability.max_output_bytes,
        checks=tuple(checks),
    )


def format_report(report: DoctorReport) -> str:
    lines = [
        f"SharkRail {report.runtime_version} (protocol {report.protocol_version})",
        f"Platform: {report.platform} {report.platform_release} ({report.architecture})",
        f"Python: {report.python_version}",
        f"Modes: {', '.join(report.modes)}",
        f"Targets: {', '.join(report.targets)}",
        f"Process tree: {report.process_tree}",
        f"Output limit: {report.max_output_bytes} bytes",
        "Checks:",
    ]
    lines.extend(f"  [{check.status.upper()}] {check.name}: {check.detail}" for check in report.checks)
    return "\n".join(lines)
