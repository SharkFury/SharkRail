"""Validate a SharkRail release tag against the package version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SEMVER_TAG = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$"
)
PROJECT_VERSION = re.compile(r'^version = "(?P<version>[^"]+)"$', re.MULTILINE)
PACKAGE_VERSION = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)


def _read_version(path: Path, pattern: re.Pattern[str]) -> str:
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"Could not find a version in {path}")
    return match.group("version")


def validate_release(tag: str, project_version: str, package_version: str) -> None:
    """Raise ValueError unless the tag and both package versions agree."""
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"Release tag must use the vMAJOR.MINOR.PATCH form: {tag!r}")

    tag_version = match.group("version")
    if project_version != package_version:
        raise ValueError(
            "Version mismatch: "
            f"pyproject.toml has {project_version!r}, "
            f"src/sharkrail/__init__.py has {package_version!r}"
        )
    if tag_version != project_version:
        raise ValueError(
            f"Tag {tag!r} does not match package version {project_version!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Git tag to validate, for example v0.1.0")
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[2]
    project_version = _read_version(repository / "pyproject.toml", PROJECT_VERSION)
    package_version = _read_version(
        repository / "src" / "sharkrail" / "__init__.py", PACKAGE_VERSION
    )

    try:
        validate_release(args.tag, project_version, package_version)
    except ValueError as error:
        parser.error(str(error))

    print(f"Release tag {args.tag} matches SharkRail {project_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
