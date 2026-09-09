"""Validate a SharkRail release tag against the package version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SEMVER_TAG = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$"
)
PACKAGE_VERSION = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)
CHANGELOG_RELEASE = re.compile(
    r"^## \[(?P<version>[^]]+)\] - (?P<date>\d{4}-\d{2}-\d{2})$", re.MULTILINE
)


def _read_version(path: Path, pattern: re.Pattern[str]) -> str:
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"Could not find a version in {path}")
    return match.group("version")


def validate_release(tag: str, package_version: str) -> None:
    """Raise ValueError unless the tag and package version agree."""
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"Release tag must use the vMAJOR.MINOR.PATCH form: {tag!r}")

    tag_version = match.group("version")
    if tag_version != package_version:
        raise ValueError(
            f"Tag {tag!r} does not match package version {package_version!r}"
        )


def validate_changelog(changelog: str, package_version: str) -> None:
    """Require the package version to have a dated, formal release heading."""

    releases = {
        match.group("version"): match.group("date")
        for match in CHANGELOG_RELEASE.finditer(changelog)
    }
    if package_version not in releases:
        raise ValueError(
            f"CHANGELOG.md has no dated [{package_version}] release heading"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Git tag to validate, for example v0.1.0")
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[2]
    package_version = _read_version(
        repository / "src" / "sharkrail" / "_version.py", PACKAGE_VERSION
    )

    try:
        validate_release(args.tag, package_version)
        validate_changelog(
            (repository / "CHANGELOG.md").read_text(encoding="utf-8"),
            package_version,
        )
    except ValueError as error:
        parser.error(str(error))

    print(f"Release tag {args.tag} matches SharkRail {package_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
