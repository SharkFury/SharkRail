"""Access to SharkRail's bundled machine-readable contracts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def protocol_schema() -> dict[str, Any]:
    """Return an independent copy of the JSON Schema for protocol 1.x."""
    resource = files("sharkrail.schemas").joinpath("protocol-1.0.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))
