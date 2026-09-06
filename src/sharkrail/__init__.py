"""SharkRail execution package."""

__version__ = "0.1.0"

from .core.errors import ErrorCode, ErrorStage, ExecutionError, SharkRailError
from .core.lifecycle import InvalidTransition, SessionLifecycle, SessionState
from .core.models import CommandMode, CommandSpec, ResourceLimits
from .integrations.acp import AcpTerminalAdapter
from .integrations.mcp import MCP_PROTOCOL_VERSION, McpRuntime
from .integrations.schema import protocol_schema
from .observability.telemetry import (
    EventRecorder,
    configure_logging,
    configure_opentelemetry,
)
from .runtime.backends import CancellationPolicy, CancellationStep
from .runtime.capabilities import Capability, collect
from .runtime.executor import (
    CommandResult,
    CommandRunner,
    LifecycleEvent,
    LifecycleEventType,
)
from .runtime.policy import ExecutionPolicy, PolicyViolation
from .runtime.routing import Shell, Target, WslOptions, direct_command, shell_command
from .runtime.sessions import Session, SessionManager

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "AcpTerminalAdapter",
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
