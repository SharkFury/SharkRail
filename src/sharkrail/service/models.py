"""Durable asynchronous Job resource models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

DEFAULT_JOB_TIMEOUT_SECONDS = 60 * 60
MAX_JOB_TIMEOUT_SECONDS = 24 * 60 * 60
DEFAULT_JOB_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_JOB_OUTPUT_BYTES = DEFAULT_JOB_MAX_OUTPUT_BYTES

_JOB_SPEC_FIELDS = {
    "command",
    "cwd",
    "env",
    "timeout_seconds",
    "idle_timeout_seconds",
    "max_output_bytes",
    "callback_endpoint_id",
    "callback",
    "desired_state",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobPhase(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    EXECUTOR_LOST = "executor_lost"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in {
            JobPhase.SUCCEEDED,
            JobPhase.FAILED,
            JobPhase.TIMED_OUT,
            JobPhase.CANCELED,
            JobPhase.EXECUTOR_LOST,
            JobPhase.EXPIRED,
        }


@dataclass(frozen=True)
class JobSpec:
    command: tuple[str, ...]
    cwd: Optional[str] = None
    env: Optional[Mapping[str, str]] = None
    timeout_seconds: Optional[float] = DEFAULT_JOB_TIMEOUT_SECONDS
    idle_timeout_seconds: Optional[float] = None
    max_output_bytes: int = DEFAULT_JOB_MAX_OUTPUT_BYTES
    callback_endpoint_id: Optional[str] = None
    desired_state: str = "active"

    def validate(self) -> None:
        if not self.command or any(
            not isinstance(item, str) or not item for item in self.command
        ):
            raise ValueError("command must be a non-empty array of non-empty strings")
        if self.cwd is not None and not isinstance(self.cwd, str):
            raise ValueError("cwd must be a string")
        if self.env is not None and any(
            not isinstance(key, str)
            or not key
            or "=" in key
            or not isinstance(value, str)
            for key, value in self.env.items()
        ):
            raise ValueError("env must contain valid string keys and values")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > MAX_JOB_TIMEOUT_SECONDS
            or not math.isfinite(self.timeout_seconds)
        ):
            raise ValueError(
                "timeout_seconds must be positive and no greater than "
                f"{MAX_JOB_TIMEOUT_SECONDS}"
            )
        if self.idle_timeout_seconds is not None and (
            not isinstance(self.idle_timeout_seconds, (int, float))
            or isinstance(self.idle_timeout_seconds, bool)
            or self.idle_timeout_seconds <= 0
            or self.idle_timeout_seconds > MAX_JOB_TIMEOUT_SECONDS
            or not math.isfinite(self.idle_timeout_seconds)
        ):
            raise ValueError(
                "idle_timeout_seconds must be positive and no greater than "
                f"{MAX_JOB_TIMEOUT_SECONDS}"
            )
        if (
            not isinstance(self.max_output_bytes, int)
            or isinstance(self.max_output_bytes, bool)
            or self.max_output_bytes < 0
            or self.max_output_bytes > MAX_JOB_OUTPUT_BYTES
        ):
            raise ValueError(
                "max_output_bytes must be a non-negative integer no greater than "
                f"{MAX_JOB_OUTPUT_BYTES}"
            )
        if self.callback_endpoint_id is not None and (
            not isinstance(self.callback_endpoint_id, str)
            or not self.callback_endpoint_id
        ):
            raise ValueError("callback endpoint_id must be a non-empty string")
        if self.desired_state not in {"active", "cancelled"}:
            raise ValueError("desired_state must be active or cancelled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "env": dict(self.env) if self.env is not None else None,
            "timeout_seconds": self.timeout_seconds,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "callback_endpoint_id": self.callback_endpoint_id,
            "desired_state": self.desired_state,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JobSpec:
        unknown = set(value) - _JOB_SPEC_FIELDS
        if unknown:
            raise ValueError(f"unknown Job field: {min(unknown)}")
        command = value.get("command")
        if not isinstance(command, (list, tuple)):
            raise TypeError("command must be an array")
        env = value.get("env")
        if env is not None and not isinstance(env, dict):
            raise ValueError("env must be an object")
        callback = value.get("callback")
        if callback is not None:
            if not isinstance(callback, dict):
                raise ValueError("callback must be an object")
            unknown_callback = set(callback) - {"endpoint_id"}
            if unknown_callback:
                raise ValueError(f"unknown callback field: {min(unknown_callback)}")
        if value.get("callback_endpoint_id") is not None and callback is not None:
            raise ValueError("callback_endpoint_id and callback are mutually exclusive")
        callback_endpoint_id = value.get("callback_endpoint_id")
        if callback is not None:
            callback_endpoint_id = callback.get("endpoint_id")
        timeout_seconds = value.get("timeout_seconds")
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_JOB_TIMEOUT_SECONDS
        spec = cls(
            command=tuple(command),
            cwd=value.get("cwd"),
            env=env,
            timeout_seconds=timeout_seconds,
            idle_timeout_seconds=value.get("idle_timeout_seconds"),
            max_output_bytes=value.get(
                "max_output_bytes", DEFAULT_JOB_MAX_OUTPUT_BYTES
            ),
            callback_endpoint_id=callback_endpoint_id,
            desired_state=value.get("desired_state", "active"),
        )
        spec.validate()
        return spec


@dataclass(frozen=True)
class JobRecord:
    id: str
    tenant_id: str
    idempotency_key: str
    spec: JobSpec
    request_hash: str
    phase: JobPhase
    generation: int
    observed_generation: int
    revision: int
    durability: str
    store_epoch: str
    created_at: str
    updated_at: str
    attempt_id: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_epoch: int = 0
    lease_expires_at: Optional[float] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    exit_code: Optional[int] = None
    reason: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    output_truncated: bool = False
    conditions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self, *, include_spec: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": self.id,
            "tenant_id": self.tenant_id,
            "phase": self.phase.value,
            "status": self.phase.value,
            "generation": self.generation,
            "observed_generation": self.observed_generation,
            "revision": self.revision,
            "durability": self.durability,
            "store_epoch": self.store_epoch,
            "attempt_id": self.attempt_id,
            "lease_epoch": self.lease_epoch,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "error": self.error,
            "output": {
                "stdout_bytes": self.stdout_bytes,
                "stderr_bytes": self.stderr_bytes,
                "truncated": self.output_truncated,
            },
            "conditions": list(self.conditions),
            "status_url": f"/v1/jobs/{self.id}",
            "result_url": f"/v1/jobs/{self.id}/result",
        }
        if include_spec:
            result["spec"] = self.spec.to_dict()
        return result


@dataclass(frozen=True)
class OutboxRecord:
    event_id: str
    job_id: str
    tenant_id: str
    endpoint_id: str
    payload: dict[str, Any]
    attempts: int
    next_attempt_at: float
    delivery_attempt_id: Optional[str] = None
