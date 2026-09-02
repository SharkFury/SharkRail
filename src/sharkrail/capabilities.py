"""Platform capability reporting."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    contract_version: str
    platform_name: str
    process_tree: str
    modes: tuple[str, ...]
    max_output_bytes: int
    supports_timeout: bool
    features: tuple[str, ...]


def collect() -> Capability:
    current = platform.system().lower()
    if current == "windows":
        return Capability(
            contract_version="1.0.0",
            platform_name="windows",
            process_tree="job_object",
            modes=("pipe", "pty"),
            max_output_bytes=16 * 1024 * 1024,
            supports_timeout=True,
            features=("session_lifecycle", "exit_reasons", "capabilities"),
        )

    if current == "darwin":
        return Capability(
            contract_version="1.0.0",
            platform_name="macos",
            process_tree="process_group",
            modes=("pipe", "pty"),
            max_output_bytes=16 * 1024 * 1024,
            supports_timeout=True,
            features=("session_lifecycle", "exit_reasons", "capabilities"),
        )

    return Capability(
        contract_version="1.0.0",
        platform_name="linux",
        process_tree="process_group",
        modes=("pipe", "pty"),
        max_output_bytes=16 * 1024 * 1024,
        supports_timeout=True,
        features=("session_lifecycle", "exit_reasons", "capabilities"),
    )
