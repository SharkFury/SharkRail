# BUILD

This repository uses a standard Python package layout.

## Prerequisites

- Python 3.9+
- No extra OS dependencies required for the current foundation layer

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Build

```bash
python -m pip install build
python -m build
```

## Test

```bash
pytest
```

## Lint / format (optional)

```bash
pip install ruff
ruff check .
ruff format .
```

## Local run

```bash
sharkrail run echo hi
```
