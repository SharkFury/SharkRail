import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^]]*\]\((?P<target>[^)]+)\)")


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in REPOSITORY.rglob("*.md")
        if not any(part.startswith(".") for part in path.relative_to(REPOSITORY).parts)
    )


def test_required_project_documentation_exists() -> None:
    required = {
        "BUILD.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "LICENSE",
        "README.md",
        "README.zh-CN.md",
        "ROADMAP.md",
        "SECURITY.md",
        "SUPPORT.md",
        "docs/ARCHITECTURE.md",
        "docs/CONFIGURATION.md",
        "docs/OBSERVABILITY.md",
        "docs/PRODUCT.md",
        "docs/PROTOCOL.md",
        "docs/RELEASING.md",
        "docs/RELIABILITY.md",
        "docs/TROUBLESHOOTING.md",
        "docs/VERSIONING.md",
    }

    missing = sorted(path for path in required if not (REPOSITORY / path).is_file())
    assert not missing, f"missing project documentation: {missing}"


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []

    for source in markdown_files():
        content = source.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(content):
            target = match.group("target").split()[0].strip("<>")
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative_path = target.split("#", 1)[0]
            if relative_path and not (source.parent / relative_path).exists():
                broken.append(f"{source.relative_to(REPOSITORY)} -> {target}")

    assert not broken, "broken relative Markdown links:\n" + "\n".join(broken)


def test_readme_exposes_primary_entry_points() -> None:
    readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")

    for heading in (
        "## Why SharkRail?",
        "## Quick start",
        "## Cross-platform contract",
        "## Lifecycle and reliability",
        "## Documentation",
        "## Project status",
        "## Community and security",
        "## License",
    ):
        assert heading in readme

    for link in (
        "docs/README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "LICENSE",
    ):
        assert f"]({link})" in readme
