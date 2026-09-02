"""Stable, machine-readable execution errors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    EXECUTABLE_NOT_FOUND = "EXECUTABLE_NOT_FOUND"
    START_FAILED = "START_FAILED"
    INVALID_REQUEST = "INVALID_REQUEST"
    CAPABILITY_NOT_SUPPORTED = "CAPABILITY_NOT_SUPPORTED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    INVALID_SESSION_STATE = "INVALID_SESSION_STATE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    RESOURCE_LIMITED = "RESOURCE_LIMITED"
    DRAIN_TIMEOUT = "DRAIN_TIMEOUT"
    TERMINATION_FAILED = "TERMINATION_FAILED"
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"


class ErrorStage(str, Enum):
    VALIDATE = "validate"
    START = "start"
    RUN = "run"
    DRAIN = "drain"
    DISPOSE = "dispose"


@dataclass(frozen=True)
class ExecutionError:
    code: ErrorCode
    stage: ErrorStage
    message: str
    retryable: bool = False
    native: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "stage": self.stage.value,
            "message": self.message,
            "retryable": self.retryable,
            "native": dict(self.native),
        }


class SharkRailError(Exception):
    """Exception carrying the stable protocol error representation."""

    def __init__(self, error: ExecutionError, cause: Optional[BaseException] = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.__cause__ = cause
