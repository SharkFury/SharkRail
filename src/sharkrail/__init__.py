"""SharkRail execution package."""

__version__ = "0.1.0"

from .backends import CancellationPolicy, CancellationStep
from .capabilities import Capability, collect
from .errors import ErrorCode, ErrorStage, ExecutionError, SharkRailError
from .executor import (
    CommandResult,
    CommandRunner,
    LifecycleEvent,
    LifecycleEventType,
)
from .lifecycle import InvalidTransition, SessionLifecycle, SessionState
from .models import CommandMode, CommandSpec, ResourceLimits
from .routing import Shell, Target, WslOptions, direct_command, shell_command
from .sessions import Session, SessionManager

__all__ = [
    "CancellationPolicy",
    "CancellationStep",
    "Capability",
    "CommandMode",
    "CommandResult",
    "CommandRunner",
    "CommandSpec",
    "ErrorCode",
    "ErrorStage",
    "ExecutionError",
    "InvalidTransition",
    "LifecycleEvent",
    "LifecycleEventType",
    "ResourceLimits",
    "Session",
    "SessionLifecycle",
    "SessionManager",
    "SessionState",
    "SharkRailError",
    "Shell",
    "Target",
    "WslOptions",
    "collect",
    "direct_command",
    "shell_command",
]
