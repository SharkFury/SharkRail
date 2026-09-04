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
from .mcp import MCP_PROTOCOL_VERSION, McpRuntime
from .models import CommandMode, CommandSpec, ResourceLimits
from .policy import ExecutionPolicy, PolicyViolation
from .routing import Shell, Target, WslOptions, direct_command, shell_command
from .schema import protocol_schema
from .sessions import Session, SessionManager
from .telemetry import EventRecorder, configure_logging, configure_opentelemetry

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "CancellationPolicy",
    "CancellationStep",
    "Capability",
    "CommandMode",
    "CommandResult",
    "CommandRunner",
    "CommandSpec",
    "ErrorCode",
    "ErrorStage",
    "EventRecorder",
    "ExecutionError",
    "ExecutionPolicy",
    "InvalidTransition",
    "LifecycleEvent",
    "LifecycleEventType",
    "McpRuntime",
    "PolicyViolation",
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
    "configure_logging",
    "configure_opentelemetry",
    "direct_command",
    "protocol_schema",
    "shell_command",
]
