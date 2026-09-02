# SharkRail

Native execution rails for AI agents.

SharkRail is a local execution runtime that helps AI agents and agent-driven tools run commands in a reliable way. The project started from an early Win32 pipe prototype and is now rebuilt as a cross-platform execution product with clear protocol contracts.

SharkRail is not a terminal UI. It is infrastructure for command execution.

## Why SharkRail

AI agents need more than `spawn()`:

- Structured `stdout`/`stderr` streams and stable exit codes
- A predictable lifecycle from accept to completion
- Reliable timeout and cancellation behaviour
- Process tree cleanup, not just killing the parent process
- Cross-platform consistency with explicit capability reporting

The current version focuses on correctness-first execution behavior that is reusable across windows, Linux, and macOS.

## Project status

This repository is in the vNext implementation phase. It currently contains the public specification + a first production-oriented code foundation, not a finished Windows-only tool.

For deep design details, see [docs/PRODUCT.md](docs/PRODUCT.md).

## Architecture overview

- Client-facing contract in a versioned protocol
- Session manager with explicit states
- Backend adapters for platform differences
- `sharkrail` CLI (entrypoint)
- Future adapter layers (MCP / ACP / IDE integrations)

## Core directions in this release

- `pipe` execution mode for non-interactive commands
- A consistent event model for running processes
- Timeout, cancel, and completion states with explicit reasons
- Cross-platform command specification model (direct argv vs shell)

## Build and run

See [BUILD.md](BUILD.md).

## Development guidelines

- English is the primary documentation language. Other language materials are provided for context where useful.
- All new features should have unit tests.
- The project follows standard open-source repository conventions (license, contribution rules, security policy, changelog).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

sharkrail run --help
```

Example:

```bash
sharkrail run echo Hello, SharkRail
```

## Documentation

- `docs/PRODUCT.md` (detailed design and roadmap)
- `CONTRIBUTING.md`
- `BUILD.md`

## License

This repository inherits the GPL-3.0 license currently present in `LICENSE`.

## 其他语言

- README 中文版：`README.zh-CN.md`
