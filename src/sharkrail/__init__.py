"""SharkRail execution package."""

__version__ = "0.0.1"

from .capabilities import Capability, collect
from .executor import (
    CommandResult,
    CommandRunner,
    LifecycleEvent,
    LifecycleEventType,
)
from .lifecycle import InvalidTransition, SessionLifecycle, SessionState
from .models import CommandMode, CommandSpec

__all__ = [
    "CommandMode",
    "CommandSpec",
    "CommandResult",
    "CommandRunner",
    "LifecycleEvent",
    "LifecycleEventType",
    "Capability",
    "collect",
    "InvalidTransition",
    "SessionLifecycle",
    "SessionState",
]
