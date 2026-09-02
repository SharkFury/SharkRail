"""SharkRail execution package."""

__version__ = "0.0.1"

from .models import CommandMode, CommandSpec
from .executor import (
    CommandResult,
    CommandRunner,
    LifecycleEvent,
    LifecycleEventType,
)
from .lifecycle import InvalidTransition, SessionLifecycle, SessionState

__all__ = [
    "CommandMode",
    "CommandSpec",
    "CommandResult",
    "CommandRunner",
    "LifecycleEvent",
    "LifecycleEventType",
    "InvalidTransition",
    "SessionLifecycle",
    "SessionState",
]
