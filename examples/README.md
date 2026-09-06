# SharkRail examples

Every supported integration surface and core execution scenario has a runnable
example here. Run examples from the repository root after installing SharkRail:

```bash
python -m pip install -e .
python examples/basics/run_command.py
```

All Python examples use `sys.executable` where possible, so they work on
Windows, Linux, and macOS. Platform-specific examples detect support and exit
cleanly with an explanation when the capability is unavailable.

## Scenario index

### Basics

| Scenario | Example | What it demonstrates |
| --- | --- | --- |
| One-shot structured command | [`run_command.py`](basics/run_command.py) | Direct argv execution and separate stdout/stderr |
| Explicit shell script | [`shell_command.py`](basics/shell_command.py) | Selecting a shell without implicit parsing |

### Sessions and reliability

| Scenario | Example | What it demonstrates |
| --- | --- | --- |
| Persistent interactive process | [`persistent_session.py`](sessions/persistent_session.py) | Start, write, close stdin, wait, and dispose |
| Real terminal | [`pty_session.py`](sessions/pty_session.py) | PTY/ConPTY mode, input, merged output, and resize |
| Deadlines and cancellation | [`timeout_and_cancel.py`](sessions/timeout_and_cancel.py) | Total/idle timeout classification and cancellation escalation |
| Bounded output | [`bounded_output.py`](sessions/bounded_output.py) | Truncation and exact dropped-byte accounting |
| Concurrent agent work | [`concurrent_sessions.py`](sessions/concurrent_sessions.py) | Multiple sessions sharing one bounded manager |
| Events and runtime inspection | [`events_and_stats.py`](sessions/events_and_stats.py) | Cursor reads, lifecycle events, inspect, and stats |
| OS resource limits | [`resource_limits.py`](sessions/resource_limits.py) | CPU-time intent plus guidance for platform-specific limits |

### Security and observability

| Scenario | Example | What it demonstrates |
| --- | --- | --- |
| Host-owned execution policy | [`execution_policy.py`](security/execution_policy.py) | Allow lists, required deadlines, and fail-closed rejection |
| Redacted audit trail | [`event_audit.py`](security/event_audit.py) | Local bounded JSONL event recording without output content |
| Policy file | [`policy.json`](security/policy.json) | A strict policy shared by CLI and server deployments |

### Agent integrations

| Scenario | Example | What it demonstrates |
| --- | --- | --- |
| Native JSON-RPC host | [`json_rpc_client.py`](integrations/json_rpc_client.py) | Hello, session start, wait, and dispose over stdio |
| MCP host | [`mcp_stdio_client.py`](integrations/mcp_stdio_client.py) | MCP initialize, tool discovery, and capability call |
| ACP client terminal | [`acp_terminal_adapter.py`](integrations/acp_terminal_adapter.py) | ACP terminal create, output, wait, and release |

### Platform adapters

| Scenario | Example | What it demonstrates |
| --- | --- | --- |
| Windows Subsystem for Linux | [`wsl_command.py`](platforms/wsl_command.py) | Structured WSL target routing and optional execution |

## CLI scenarios

The CLI uses the same runtime as the Python, JSON-RPC, MCP, and ACP examples:

```bash
# Direct argv execution; no shell parses the arguments.
sharkrail run --json -- python -c "print('hello')"

# Shell syntax is accepted only after choosing a shell explicitly.
sharkrail shell bash "printf 'hello\\n'"

# Bound runtime and retained output.
sharkrail run --timeout-ms 30000 --max-output-bytes 1048576 -- python worker.py

# Apply a host-owned policy and remove the inherited environment.
sharkrail run --policy examples/security/policy.json --clean-env \
  --timeout-ms 30000 --max-output-bytes 1048576 -- python -V

# Inspect capabilities and actively probe the installation.
sharkrail capabilities --json
sharkrail doctor --json

# Record a bounded, redacted lifecycle audit.
sharkrail run --event-log sharkrail-events.jsonl -- python -V

# Start an integration server.
sharkrail serve
sharkrail mcp
```

On Windows, replace `python` with the appropriate interpreter command when it
is not on `PATH`. Shell and WSL availability must be discovered at runtime; do
not infer it from the operating-system name.
