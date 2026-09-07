from importlib import resources
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def test_package_and_repository_use_mit_license() -> None:
    project = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    license_text = (REPOSITORY / "LICENSE").read_text(encoding="utf-8")

    assert 'license = "MIT"' in project
    assert license_text.startswith("MIT License\n")
    assert "Permission is hereby granted, free of charge" in license_text
    assert "Copyright (c) 2026 Larry Chen and SharkRail contributors" in license_text


def test_public_documentation_has_no_stale_gpl_reference() -> None:
    public_files = [
        REPOSITORY / "README.md",
        REPOSITORY / "README.zh-CN.md",
        REPOSITORY / "pyproject.toml",
    ]

    for path in public_files:
        assert "GPL" not in path.read_text(encoding="utf-8"), path


def test_project_tagline_is_consistent() -> None:
    expected = "Verifiable process execution for AI agents"

    assert expected in (REPOSITORY / "README.md").read_text(encoding="utf-8")
    assert expected in (REPOSITORY / "docs" / "PRODUCT.md").read_text(encoding="utf-8")
    assert expected in (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")


def test_citation_metadata_describes_the_project() -> None:
    citation = (REPOSITORY / "CITATION.cff").read_text(encoding="utf-8")

    assert "cff-version: 1.2.0" in citation
    assert "license: MIT" in citation
    assert "https://github.com/SharkFury/SharkRail" in citation


def test_package_version_has_one_configuration_source() -> None:
    project = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    package_init = (REPOSITORY / "src" / "sharkrail" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert 'dynamic = ["version"]' in project
    assert 'version = {attr = "sharkrail._version.__version__"}' in project
    assert "from ._version import __version__" in package_init


def test_service_configuration_example_is_packaged() -> None:
    example = (
        resources.files("sharkrail.resources")
        .joinpath("sharkrail.toml.example")
        .read_text(encoding="utf-8")
    )
    assert "[job_store]" in example
    assert 'url = "sqlite:///:memory:"' in example
