from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "sharkrail"


def test_source_modules_follow_architecture_layers() -> None:
    expected_packages = {
        "core": {"errors.py", "lifecycle.py", "models.py", "output.py"},
        "runtime": {
            "backends.py",
            "capabilities.py",
            "doctor.py",
            "executor.py",
            "policy.py",
            "routing.py",
            "sessions.py",
            "windows.py",
        },
        "integrations": {"acp.py", "mcp.py", "protocol.py", "schema.py"},
        "observability": {"telemetry.py"},
    }

    for package, modules in expected_packages.items():
        package_root = PACKAGE_ROOT / package
        assert package_root.is_dir()
        assert (package_root / "__init__.py").is_file()
        assert modules <= {path.name for path in package_root.glob("*.py")}

    assert {path.name for path in PACKAGE_ROOT.glob("*.py")} == {
        "__init__.py",
        "__main__.py",
        "_version.py",
        "cli.py",
    }
