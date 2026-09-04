# SharkRail

> Verifiable process execution for AI agents.

[![CI](https://github.com/SharkFury/SharkRail/actions/workflows/ci.yml/badge.svg)](https://github.com/SharkFury/SharkRail/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-0A7E8C.svg)](LICENSE)
[![Protocol: 1.0](https://img.shields.io/badge/protocol-1.0-5B47A5.svg)](docs/PROTOCOL.md)

[English](README.md) | [简体中文](README.zh-CN.md)

SharkRail defines and implements an open, vendor-neutral reliability contract
for processes run by coding agents, IDEs, and automation tools. Its Python
reference runtime presents one versioned session model over Windows pipes and
ConPTY, POSIX pipes and PTYs, process groups, Job Objects, and WSL.

SharkRail is execution infrastructure. It is not a terminal emulator, remote
shell, or security sandbox.

For a short, non-interactive command on one platform, use the standard process
API. SharkRail earns its additional dependency when a product needs verifiable
completion, bounded output, interactive terminal semantics, process-tree
cleanup, or one honest contract across operating systems.

## Why SharkRail?

Starting a subprocess is easy. Supervising one for an autonomous agent is not.
An agent needs deterministic answers to questions that interactive shells leave
implicit:

- Did the process start, exit, and finish draining its output?
- Which bytes came from stdout, stderr, or a merged terminal stream?
- Did a timeout, cancellation, or resource policy end the command?
- Were descendant processes cleaned up?
- Was output truncated, and exactly how much was dropped?
- Does this machine support PTY, resize, a requested shell, or WSL?

SharkRail turns those answers into structured results, ordered events, stable
errors, and discoverable capabilities.

The project's public goal is larger than one implementation: preserve the
contract, reference runtime, conformance tests, and real-world failure cases as
shared infrastructure. Read the [public value design](docs/VALUE.md).

## Highlights

- Direct argv execution with no implicit shell parsing
- Explicit cmd, PowerShell, pwsh, Bash, and Zsh execution
- Separate stdout/stderr in pipe mode; native PTY/ConPTY in terminal mode
- Persistent sessions with input, EOF, resize, interrupt, cancel, wait, and dispose
- Process-tree cleanup through Windows Job Objects or POSIX process groups
- Ordered lifecycle events, resumable cursors, and lossless Base64 output
- Bounded output, input, events, sessions, RPC concurrency, and execution time
- Memory, CPU-time, process-count, wall-time, and idle-time policies
- MCP and JSON-RPC 2.0 over stdio plus an asynchronous Python API
- Runtime health, statistics, trace IDs, redacted audit logs, and OpenTelemetry hooks
- Runtime-probed capability negotiation and active `doctor` diagnostics

## Quick start

SharkRail currently targets early adopters. Install from source:

```bash
git clone https://github.com/SharkFury/SharkRail.git
cd SharkRail
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e .
```

Run a direct command and inspect the local runtime:

```bash
sharkrail run --json -- python -c "print('hello from SharkRail')"
sharkrail capabilities --json
sharkrail doctor
```

Shell parsing is always explicit:

```bash
sharkrail shell bash "printf 'hello\n'"
sharkrail shell pwsh "Write-Output hello"
```

On Windows, target a WSL distribution without constructing a shell string:

```powershell
sharkrail run --target wsl --wsl-distribution Ubuntu -- python3 -c "print('hello')"
```

See [BUILD.md](BUILD.md) for a complete developer setup.

## Integrate with an agent

Start the MCP server for hosts that support tool discovery:

```bash
sharkrail mcp
```

Start the newline-delimited JSON-RPC service:

```bash
sharkrail serve
```

Each input and output line is one JSON-RPC 2.0 message. Requests may run
concurrently and responses are matched by `id`.

```json
{"jsonrpc":"2.0","id":1,"method":"runtime.hello","params":{}}
{"jsonrpc":"2.0","id":2,"method":"session.start","params":{"spec":{"executable":"python","argv":["-c","print('hello')"]}}}
```

For in-process integration:

```python
import asyncio
import sys

from sharkrail import CommandSpec, SessionManager


async def main() -> None:
    runtime = SessionManager()
    session = await runtime.start(
        CommandSpec(sys.executable, ("-c", "print(input().upper())"))
    )
    await runtime.write(session.id, b"hello\n")
    await runtime.close_stdin(session.id)
    result = await runtime.wait(session.id)
    print(result.stdout)


asyncio.run(main())
```

See [agent integrations](docs/INTEGRATIONS.md) for MCP configuration and
[the protocol reference](docs/PROTOCOL.md) for the complete native wire contract.

## Cross-platform contract

| Capability | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Pipe output | Separate stdout/stderr | Separate stdout/stderr | Separate stdout/stderr |
| Interactive terminal | ConPTY via pywinpty | Native PTY | Native PTY |
| Resize | Yes | Yes | Yes |
| Process-tree ownership | Job Object | Process group | Process group |
| Native shells | cmd, PowerShell, pwsh | bash, zsh, pwsh | bash, zsh, pwsh |
| WSL target | Best-effort descendant cleanup | Not applicable | Not applicable |

Clients should call `runtime.capabilities`; they should not infer behavior from
an OS name. PTY/ConPTY output is one merged terminal stream, so SharkRail never
invents a separate stderr channel where the platform does not provide one.

## Lifecycle and reliability

```text
created -> accepted -> starting -> running -> exiting -> draining -> completed
                                      |                         |
                                      +-> cancelling -----------+
created / accepted / starting / running / draining -----------> failed
completed / failed -------------------------------------------> disposed
```

Process exit and session completion are intentionally different. A session is
complete only after output has drained or a bounded drain failure has been
reported. Cancellation escalates through interrupt, terminate, and process-tree
kill. Output loss and capability degradation are explicit—not silent.

Read the [reliability contract](docs/RELIABILITY.md) before building a production
integration.

## Documentation

Start with the [documentation index](docs/README.md), or go directly to:

- [Product scope and principles](docs/PRODUCT.md)
- [Public value, stewardship, and evidence](docs/VALUE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Protocol reference](docs/PROTOCOL.md)
- [Configuration and limits](docs/CONFIGURATION.md)
- [Reliability contract](docs/RELIABILITY.md)
- [Observability](docs/OBSERVABILITY.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Versioning policy](docs/VERSIONING.md)
- [Roadmap](ROADMAP.md)
- [Build and test](BUILD.md)

## Project status

SharkRail is in alpha (`v0.1`). Its local execution core is implemented and
continuously tested on Windows, Ubuntu, and macOS with Python 3.9, 3.11, and
3.14. The JSON-RPC protocol is versioned `1.0.0`, but pre-1.0 package APIs may
still change with release notes and migration guidance.

See [CHANGELOG.md](CHANGELOG.md) for shipped changes and [ROADMAP.md](ROADMAP.md)
for non-binding future direction.

## Community and security

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
- Use [SUPPORT.md](SUPPORT.md) for help and diagnostic guidance.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

SharkRail is available under the [MIT License](LICENSE).
