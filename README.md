# SharkRail

Native execution rails for AI agents.

SharkRail is a local, cross-platform command and terminal runtime for AI coding agents, IDE integrations, and automation tools. It turns OS-specific process behavior into a versioned contract with structured output, explicit lifecycle events, cancellation, timeouts, process-tree cleanup, and capability discovery.

It is infrastructure, not a terminal UI, remote shell, or security sandbox.

## What it solves

Calling `subprocess` is easy; making it reliable for an autonomous agent is not. Agents need to know whether a process started, distinguish output streams, retain output within a budget, cancel the entire process tree, and wait until output is drained after exit. Interactive tools additionally need a real PTY/ConPTY and resize support.

SharkRail provides:

- Direct argv execution without implicit shell parsing
- Explicit `cmd`, Windows PowerShell, PowerShell 7, Bash, and Zsh requests
- Independent stdout/stderr in pipe mode
- Native PTY on Linux/macOS and ConPTY on Windows
- Persistent sessions with stdin, EOF, resize, interrupt, cancel, wait, and dispose
- `interrupt → terminate → kill_tree` cancellation escalation
- Windows Job Objects and POSIX process groups for process-tree cleanup
- Ordered lifecycle/output events with resumable cursors
- Byte-accurate output budgets, truncation accounting, and resource limits
- CPU, memory, process-count, wall-time, idle-time, and cumulative-input policies
- Bounded event history, paginated subscriptions, session expiry, and RPC backpressure
- UTC/monotonic event timing, trace IDs, runtime health/stats, and session inspection
- Structured stderr logs, redacted JSONL audit files, and optional OpenTelemetry
- A newline-delimited JSON-RPC 2.0 stdio server
- Native and WSL target routing
- Runtime capability negotiation and `doctor` diagnostics

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e .

sharkrail run --json -- python -c "print('hello from SharkRail')"
sharkrail capabilities --json
sharkrail doctor
```

Shell execution is always explicit:

```bash
sharkrail shell bash "printf 'hello\n'"
sharkrail shell pwsh "Write-Output hello"
```

Run a direct command in WSL from Windows:

```powershell
sharkrail run --target wsl --wsl-distribution Ubuntu -- python3 -c "print('hello')"
```

## Agent protocol

Start the stdio service:

```bash
sharkrail serve
```

Each input and output line is one JSON-RPC 2.0 message. Requests may run concurrently.

```json
{"jsonrpc":"2.0","id":1,"method":"runtime.hello","params":{}}
{"jsonrpc":"2.0","id":2,"method":"session.start","params":{"spec":{"executable":"python","argv":["-c","print('hello')"]}}}
```

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for all methods and guarantees.

## Python API

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

## Platform contract

| Capability | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Pipe stdout/stderr | Separate | Separate | Separate |
| Interactive terminal | ConPTY via pywinpty | Native PTY | Native PTY |
| Resize | Yes | Yes | Yes |
| Process-tree ownership | Job Object | Process group | Process group |
| Native shells | cmd, PowerShell, pwsh | bash, zsh, pwsh | bash, zsh, pwsh |
| WSL target | Yes, best-effort Linux descendant cleanup | N/A | N/A |

Clients should call `runtime.capabilities` instead of guessing from the OS name. PTY output is a merged terminal stream; SharkRail does not invent separate stderr where the platform cannot provide it.

## Documentation

- [Product and architecture](docs/PRODUCT.md)
- [Protocol reference](docs/PROTOCOL.md)
- [Reliability contract](docs/RELIABILITY.md)
- [Observability](docs/OBSERVABILITY.md)
- [Build and test](BUILD.md)
- [Release process](docs/RELEASING.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [简体中文 README](README.zh-CN.md)

## Project status

v0.1 is an implementation release of the local execution runtime. The JSON-RPC protocol is versioned `1.0.0`; additive fields and capabilities may be introduced within that major version. See the product document for post-v0.1 integration work.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
