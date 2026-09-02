# SharkRail Product and Architecture

Status: v0.1 implementation

Product: SharkRail

Tagline: Native execution rails for AI agents.

## Product definition

SharkRail is a local execution runtime that gives AI agents a predictable way to run non-interactive commands and interactive terminal sessions across Windows, Linux, macOS, and WSL.

The product standardizes intent and observable results—not OS mechanisms. Windows uses pipes, ConPTY, and Job Objects. POSIX systems use pipes, PTYs, process groups, and signals. Clients use the same session methods while discovering differences through capabilities.

## Users and jobs

- Agent and IDE developers replace duplicated subprocess, PTY, timeout, buffering, and cleanup code.
- Coding agents execute builds, tests, linters, package managers, Git, REPLs, debuggers, and TUIs.
- Automation authors diagnose hangs, leaked descendants, missing output, encoding problems, and Windows-only failures.

## Product principles

1. Lifecycle comes from process state, EOF, and runtime events—not prompt matching.
2. Direct commands never pass through a shell.
3. Shell parsing is explicit and names the shell.
4. Process exit and session completion are different: completion follows output drain.
5. Cancellation is observable and escalates from interrupt to termination to tree kill.
6. Output loss is bounded and reported, never silent.
7. Capability negotiation describes real behavior; unsupported features do not silently degrade.
8. The runtime runs with the caller's authority and is not a security sandbox.

## v0.1 scope and implementation

### Execution

- Pipe mode with independent stdout and stderr
- POSIX PTY and Windows ConPTY via the platform-specific pywinpty dependency
- Persistent sessions, raw input, EOF, resize, interrupt, cancel, wait, and dispose
- Direct argv and explicit cmd/PowerShell/pwsh/bash/zsh execution
- Native and WSL target routing

### Reliability

- Windows kill-on-close Job Objects
- POSIX process groups
- Configurable timeout and cancellation grace periods
- Byte-based output budgets with retained/dropped byte accounting
- Concurrency, per-write, cumulative-input, output-event, event-history, completed-session, and RPC limits
- CPU, memory, process-count, wall-time, idle-time, drain, termination, and shutdown policies
- Stable error codes, error stages, and native diagnostics

### Interfaces

- Human and JSON CLI output
- Public asynchronous Python API
- Concurrent newline-delimited JSON-RPC 2.0 over stdio
- Capability negotiation and doctor diagnostics
- Runtime health/stats, session inspection, traceable timed events, structured logs, bounded audit files, and optional OpenTelemetry

### Quality

- Unit and integration tests for every implementation area
- Fault-injection, concurrency, protocol fuzz, and large-output reliability tests
- CI on Windows, Ubuntu, and macOS with Python 3.9, 3.11, and 3.14
- Lint, compatibility smoke, capability smoke, coverage/deadline gates, sdist, and wheel builds

## Runtime model

```text
Agent / IDE / CLI
        |
Python API or JSON-RPC adapter
        |
Session Manager -- events -- quotas -- cursors
        |
Target and shell router
        |
  +-----+------------------+
  |                        |
Windows                 POSIX
Pipe / ConPTY           Pipe / PTY
Job Object              Process group
  |
WSL adapter (wsl.exe)
```

Adapters translate requests; they do not own processes. Backends implement platform operations; they do not know JSON-RPC. The session manager owns lifecycle, output retention, event order, and quotas.

## Lifecycle contract

The complete state vocabulary is:

```text
created -> accepted -> starting -> running
running -> exiting -> draining -> completed
running -> cancelling -> draining -> completed
created/accepted/starting/running/draining -> failed
completed/failed -> disposed
```

The session event sequence is:

```text
accepted
process.started
stdout | stderr | pty.output | output.truncated | resource.limit_hit (0..n)
process.exited
session.drained
session.completed
```

Sequence numbers are monotonic per session. `process.exited` does not imply that all output is available. `session.completed` does.

Completion reasons remain separate from native exit codes: `success`, `failed`, `timeout`, `cancelled`, `killed`, and `resource_limited`.

## Cross-platform semantics

| Intent | Windows | Linux/macOS |
| --- | --- | --- |
| interrupt (pipe) | CTRL_BREAK_EVENT | SIGINT to process group |
| interrupt (PTY) | ConPTY control input | SIGINT to process group |
| terminate | Terminate process/job step | SIGTERM to process group |
| kill tree | Terminate Job Object | SIGKILL to process group |
| resize | pywinpty/ConPTY resize | TIOCSWINSZ |
| pipe output | Separate stdout/stderr | Separate stdout/stderr |
| PTY output | Merged UTF-8/VT stream | Merged terminal stream |

WSL commands are launched with structured `wsl.exe --exec` arguments. The Windows launcher is owned by a Job Object, but arbitrary Linux descendants may escape complete cleanup without an in-distribution supervisor. The capability is therefore best effort.

## Trust and security

v0.1 is local, single-user, and uses stdio; it opens no listening socket. It does not elevate privileges, store credentials, upload output, or modify shell profiles. Environment variables are accepted as an overlay and are never included in doctor output.

Direct argv avoids unintended shell interpretation, but executed programs have the same authority as SharkRail. Run untrusted code inside an appropriate sandbox, container, VM, AppContainer, or Windows Sandbox.

## Non-goals

- Remote shell or public network service
- Multi-tenant execution
- Credential storage or automatic UAC elevation
- Malware isolation
- Terminal emulator UI or full VT screen rendering
- Prompt-regex lifecycle detection
- Attaching to arbitrary existing consoles
- Session persistence across runtime restarts

## Roadmap after v0.1

- MCP and ACP adapters built on the stable session contract
- Optional Unix Domain Socket and current-user Windows Named Pipe transports
- WSL in-distribution supervisor for stronger Linux descendant cleanup
- Optional VT screen model and snapshots
- Explicit signing for GitHub Release artifacts

These are post-v0.1 integrations and enhancements, not missing parts of the v0.1 local execution contract.
