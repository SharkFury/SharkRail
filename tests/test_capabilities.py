from sharkrail.capabilities import collect


def test_capabilities_contract_shape():
    c = collect()
    assert c.platform_name in {"windows", "linux", "macos"}
    assert isinstance(c.modes, tuple)
    assert "pipe" in c.modes
    assert c.max_output_bytes > 0

