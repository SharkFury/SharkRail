import re

import sharkrail


def test_v01_public_api_is_exported():
    expected = {
        "CancellationPolicy",
        "CommandRunner",
        "CommandSpec",
        "ExecutionError",
        "ExecutionPolicy",
        "PolicyViolation",
        "protocol_schema",
        "SessionManager",
        "SharkRailError",
        "Shell",
        "Target",
        "direct_command",
        "shell_command",
    }
    assert expected.issubset(set(sharkrail.__all__))
    assert re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", sharkrail.__version__
    )
