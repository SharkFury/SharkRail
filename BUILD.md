# Build and test

This repository uses a standard `src`-layout Python package. The commands below
are the local equivalents of the main CI quality gates.

## Prerequisites

- Python 3.9 or newer
- Git
- A supported Windows, Linux, or macOS host

Windows installs `pywinpty` automatically for ConPTY support. Windows 10
version 1809 / Windows Server 2019 or newer is recommended.

## Create a development environment

Linux and macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

## Run quality gates

```bash
python -m ruff check src tests .github/scripts
python -m pytest --timeout=20 --cov=sharkrail --cov-report=term-missing --cov-fail-under=70
python -m compileall src tests
python .github/scripts/compat_smoke.py
```

Run one test while iterating:

```bash
python -m pytest tests/test_sessions.py -k cancellation
```

Format Python sources with:

```bash
python -m ruff format .
```

Formatting does not replace `ruff check`; run both before opening a pull request.

## Build distributions

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

The build must produce one source distribution and one platform-independent
wheel. The wheel includes the MIT license and PEP 639 `License-Expression`
metadata.

## Smoke test the CLI

```bash
sharkrail --version
sharkrail run --json -- python -c "print('hello')"
sharkrail capabilities --json
sharkrail doctor
sharkrail serve
```

## CI matrix

Every push to `main` and every pull request is checked on Ubuntu, macOS, and
Windows with Python 3.9, 3.11, and 3.14. CI runs tests, reliability stress
coverage, Ruff, dependency and compatibility checks, capability smoke tests,
distribution builds, per-job deadlines, and a 70% coverage gate.

Tagged releases use PyPI trusted publishing and attach the verified artifacts
to a GitHub Release. See [docs/RELEASING.md](docs/RELEASING.md).

## Generated files

Local builds may create `build/`, `dist/`, `*.egg-info/`, `.coverage`, and cache
directories. They are ignored by Git and can be removed after no build or test
process is using them.
