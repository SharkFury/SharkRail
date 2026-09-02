from sharkrail.capabilities import collect


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
