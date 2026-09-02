# BUILD

This repository uses a standard Python package layout.

## Prerequisites

- Python 3.9+
- No extra OS dependencies required for the current foundation layer

## Setup (Linux/macOS)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Windows installs `pywinpty` automatically for ConPTY support. Windows 10 version 1809 / Windows Server 2019 or newer is recommended.

## Build

```bash
python -m pip install build
python -m build
```

## Test

```bash
python -m pip install pytest ruff
pytest
ruff check src tests .github/scripts
```

## Format

```bash
ruff format .
```

## Local run

```bash
sharkrail run echo hi
sharkrail capabilities --json
sharkrail doctor
sharkrail serve
```

## Supported CI matrix

Every push and pull request is verified on Ubuntu, macOS, and Windows with Python 3.9 and 3.11. CI runs unit/integration tests, Ruff, compatibility smoke checks, capability checks, and distribution builds.

## Clean generated artifacts

Build outputs are written to `build/`, `dist/`, and `src/sharkrail.egg-info/`; all are ignored by Git and may be removed safely when no build is running.
