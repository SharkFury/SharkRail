from contextlib import ExitStack
from unittest.mock import patch

from sharkrail.runtime.capabilities import collect


def test_capabilities_contract_shape():
    c = collect()
    assert c.contract_version == "1.0.0"
    assert c.platform_name in {"windows", "linux", "macos"}
    assert isinstance(c.modes, tuple)
    assert "session_lifecycle" in c.features
    assert "pipe" in c.modes
    assert c.max_output_bytes > 0
    assert "native" in c.targets
    assert c.shells
    assert c.resource_limits
    assert c.verification["report"] == "discovery_only"
    assert c.verification["pipe"].endswith("not_probed")


def test_windows_capabilities_report_missing_optional_runtimes():
    def executable(name: str):
        return "C:/Windows/System32/cmd.exe" if name == "cmd.exe" else None

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "sharkrail.runtime.capabilities.platform.system", return_value="Windows"
            )
        )
        stack.enter_context(
            patch("sharkrail.runtime.capabilities.find_spec", return_value=None)
        )
        stack.enter_context(
            patch("sharkrail.runtime.capabilities.shutil.which", side_effect=executable)
        )
        capability = collect()

    assert capability.modes == ("pipe",)
    assert capability.targets == ("native",)
    assert capability.shells == ("cmd",)
    assert "pty" not in capability.features
    assert capability.degraded_reasons
    assert capability.process_tree == "job_object"
    assert capability.process_tree_fallbacks == ()
    assert capability.verification["pty"] == "unavailable"


def test_windows_capabilities_disclose_conpty_assignment_window():
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "sharkrail.runtime.capabilities.platform.system", return_value="Windows"
            )
        )
        stack.enter_context(
            patch("sharkrail.runtime.capabilities.find_spec", return_value=object())
        )
        stack.enter_context(
            patch(
                "sharkrail.runtime.capabilities.shutil.which",
                return_value="C:/Windows/System32/tool.exe",
            )
        )
        capability = collect()

    assert capability.modes == ("pipe", "pty")
    assert any("suspended-create" in reason for reason in capability.degraded_reasons)
