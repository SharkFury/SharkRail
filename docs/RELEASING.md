# Releasing SharkRail

SharkRail publishes Python distributions to PyPI through GitHub Actions trusted
publishing and attaches the same artifacts to a GitHub Release. No long-lived
PyPI API token is stored in GitHub.

## One-time repository setup

1. Create a GitHub environment named `pypi`. Add required reviewers if releases
   should require manual approval.
2. On PyPI, add a trusted publisher for owner `SharkFury`, repository
   `SharkRail`, workflow `release.yml`, and environment `pypi`. A pending trusted
   publisher can be configured before the first `sharkrail` release exists.

## Create a release

1. Update `version` in `pyproject.toml` and `__version__` in
   `src/sharkrail/__init__.py` to the same `MAJOR.MINOR.PATCH` value.
2. Move the release notes in `CHANGELOG.md` under that version and date.
3. Open a pull request and wait for the full CI matrix to pass on the release
   commit.
4. Create and push an annotated tag for that exact commit:

   ```bash
   git tag -a v0.1.0 -m "SharkRail 0.1.0"
   git push origin v0.1.0
   ```

The `release` workflow validates the tag, reruns Ruff and all tests, builds and
checks the wheel and source distribution, publishes them to PyPI, and then
creates a GitHub Release with generated notes and the same distributions.

## Validate without publishing

Run the release workflow manually from the GitHub Actions page. A manual run
executes the quality gates and uploads the built distributions as a workflow
artifact, but its tag guards prevent both PyPI and GitHub publication.

Locally, the equivalent checks are:

```bash
python .github/scripts/check_release.py v0.1.0
python -m ruff check src tests .github/scripts
python -m pytest
python -m build
python -m twine check dist/*
```

Before tagging, inspect the built wheel metadata and confirm the package version,
project URLs, Python requirement, and `License-Expression: MIT` are correct.

PyPI versions are immutable. If publishing succeeds but the GitHub Release step
fails, rerun only after confirming that the existing PyPI files match the
workflow artifacts. Never delete and recreate a release tag with different
contents. If PyPI publication fails before accepting any file, fix the release
commit and create a new patch version rather than moving a public tag.
