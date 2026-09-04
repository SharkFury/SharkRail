# Releasing SharkRail

SharkRail currently publishes signed Python distributions and a CycloneDX SBOM
as assets on a GitHub Release. PyPI publication is intentionally disabled while
the `sharkrail` project application is pending.

## Current release destination

The release workflow does not contain a PyPI publishing job and does not require
a PyPI token or GitHub `pypi` environment. A version tag creates only a GitHub
Release. Do not upload the generated distributions to PyPI manually.

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

The `release` workflow validates the tag, reruns the complete test suite on
Windows, Ubuntu, and macOS, runs source quality gates, builds and checks the
wheel and source distribution, generates a CycloneDX SBOM, and creates GitHub
artifact attestations. It attaches the distributions and SBOM to a GitHub
Release without publishing to PyPI.

## Validate without publishing

Run the release workflow manually from the GitHub Actions page. A manual run
executes the quality gates and uploads the built distributions as a workflow
artifact, but its tag guards prevent both PyPI and GitHub publication.

Locally, the equivalent checks are:

```bash
python .github/scripts/check_release.py v0.1.0
python -m ruff check .
python -m ruff format --check .
python -m mypy src/sharkrail
python -m pytest --timeout=20
python -m build
python -m twine check dist/*
```

Before tagging, inspect the built wheel metadata and confirm the package version,
project URLs, Python requirement, and `License-Expression: MIT` are correct.

After the GitHub Release is created, download an artifact and verify that GitHub
attested it from this repository's release workflow:

```bash
gh attestation verify sharkrail-0.1.0-py3-none-any.whl --repo SharkFury/SharkRail
```

Inspect the attached `*.sbom.cdx.json` and verify its root component hash against
the wheel before using it for dependency inventory.

Never delete and recreate a public release tag with different contents. If the
GitHub Release step fails, preserve the tag and rerun the failed job only after
confirming that the workflow is still building the tagged commit.

## Enable PyPI later

PyPI publishing must be introduced by a reviewed pull request after the project
application is approved. That change must:

1. Create a protected GitHub environment named `pypi`.
2. Configure a PyPI trusted publisher for owner `SharkFury`, repository
   `SharkRail`, workflow `release.yml`, and environment `pypi`.
3. Add a tag-only publish job using GitHub OIDC trusted publishing, with no
   long-lived PyPI API token.
4. Make GitHub Release creation depend on successful PyPI publication so the
   published artifacts cannot diverge.
5. Validate the complete workflow with a new patch version; PyPI versions are
   immutable and public tags must never be moved.
