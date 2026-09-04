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


def test_citation_metadata_tracks_current_release() -> None:
    citation = (REPOSITORY / "CITATION.cff").read_text(encoding="utf-8")

    assert "cff-version: 1.2.0" in citation
    assert "license: MIT" in citation
    assert "version: 0.1.0" in citation
    assert "https://github.com/SharkFury/SharkRail" in citation
