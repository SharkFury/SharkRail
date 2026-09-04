"""Runtime diagnostics with secret-free, machine-readable results."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from . import __version__
from .capabilities import collect
from .models import CommandMode, CommandSpec
from .sessions import SessionManager


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    duration_ms: float = 0.0


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


async def _probe_execution(mode: CommandMode) -> Check:
    started = monotonic()
    execution_timeout_ms = 10_000 if mode == CommandMode.PTY else 2_000
    completion_timeout_ms = execution_timeout_ms + 2_000
    manager = SessionManager(
        default_max_output_bytes=4096,
        drain_timeout_ms=2000,
        termination_timeout_ms=2000,
    )
    try:
        code = (
            "import os; print('sharkrail-probe', os.isatty(1))"
            if mode == CommandMode.PTY
            else "print('sharkrail-probe')"
        )
        session = await manager.start(
            CommandSpec(sys.executable, ("-c", code), mode=mode),
            timeout_ms=execution_timeout_ms,
        )
        result = await manager.wait(session.id, timeout_ms=completion_timeout_ms)
        healthy = result is not None and result.exit_code == 0 and "sharkrail-probe" in result.stdout
        if mode == CommandMode.PTY:
            healthy = healthy and result is not None and "True" in result.stdout
        if healthy:
            detail = "active start/write/drain/exit probe passed"
        elif result is None:
            detail = "active execution probe exceeded its completion deadline"
        else:
            error_code = result.error.code.value if result.error is not None else "none"
            detail = (
                "active execution probe failed: "
                f"reason={result.reason.value}, exit_code={result.exit_code}, "
                f"marker_seen={'sharkrail-probe' in result.stdout}, "
                f"tty_seen={'True' in result.stdout}, error={error_code}"
            )
        return Check(
            mode.value,
            "pass" if healthy else "fail",
            detail,
            round((monotonic() - started) * 1000, 3),
        )
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        return Check(
            mode.value,
            "fail",
            f"{type(error).__name__}: {error}",
            round((monotonic() - started) * 1000, 3),
        )
    finally:
        await manager.shutdown()


async def diagnose_async() -> DoctorReport:
    capability = collect()
    checks: list[Check] = [
        Check("python", "pass", sys.executable),
        await _probe_execution(CommandMode.PIPE),
    ]
    if "pty" in capability.modes:
        checks.append(await _probe_execution(CommandMode.PTY))
    else:
        checks.append(Check("pty", "warn", "PTY backend is not available at runtime"))

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


def diagnose() -> DoctorReport:
    return asyncio.run(diagnose_async())


def write_diagnostic_bundle(report: DoctorReport, destination: Path) -> Path:
    """Write a secret-free diagnostic snapshot for issue reports."""
    payload = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "doctor": report.to_dict(),
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


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
