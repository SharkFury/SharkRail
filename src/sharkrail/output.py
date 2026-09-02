"""Bounded byte output collection with explicit truncation accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CapturedOutput:
    stdout: str
    stderr: str
    retained_bytes: int
    truncated_bytes: int
    decoding_errors: bool

    @property
    def truncated(self) -> bool:
        return self.truncated_bytes > 0


def capture_output(
    stdout: bytes,
    stderr: bytes,
    max_bytes: Optional[int],
    *,
    encoding: str = "utf-8",
) -> CapturedOutput:
    """Retain output within a shared byte budget and decode it safely.

    stdout receives the first share of the budget to retain the historical CLI
    contract. Streaming backends use the same accounting in arrival order.
    """
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be greater than or equal to zero")

    budget = len(stdout) + len(stderr) if max_bytes is None else max_bytes
    kept_stdout = stdout[:budget]
    remaining = max(0, budget - len(kept_stdout))
    kept_stderr = stderr[:remaining]
    retained = len(kept_stdout) + len(kept_stderr)
    total = len(stdout) + len(stderr)

    stdout_text = kept_stdout.decode(encoding, errors="replace")
    stderr_text = kept_stderr.decode(encoding, errors="replace")
    decoding_errors = "\ufffd" in stdout_text or "\ufffd" in stderr_text
    return CapturedOutput(
        stdout=stdout_text,
        stderr=stderr_text,
        retained_bytes=retained,
        truncated_bytes=total - retained,
        decoding_errors=decoding_errors,
    )
