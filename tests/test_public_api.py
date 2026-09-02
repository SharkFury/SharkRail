import sharkrail


def test_v01_public_api_is_exported():
    expected = {
        "CancellationPolicy",
        "CommandRunner",
        "CommandSpec",
        "ExecutionError",
        "SessionManager",
        "SharkRailError",
        "Shell",
        "Target",
        "direct_command",
        "shell_command",
    }
    assert expected.issubset(set(sharkrail.__all__))
    assert sharkrail.__version__ == "0.1.0"
