# Agent integrations

SharkRail can be embedded through its Python API, its native JSON-RPC protocol,
or the Model Context Protocol (MCP). All entry points use the same
`SessionManager`, capability probes, execution policy, lifecycle, and cleanup
rules.

## MCP server

Start the local stdio server:

```bash
sharkrail mcp
```

An MCP host can register it with a configuration shaped like this:

```json
{
  "mcpServers": {
    "sharkrail": {
      "command": "sharkrail",
      "args": ["mcp", "--policy", "/absolute/path/to/policy.json"]
    }
  }
}
```

The server implements the stable MCP `2025-11-25` lifecycle and tools
contract. It exposes capability discovery, bounded one-shot execution, and
persistent session start/read/write/wait/cancel/dispose tools. Tool output is
available both as JSON text and `structuredContent`.

The process hosting SharkRail owns the policy. Model-provided tool arguments
cannot weaken it. Use an absolute policy path and a minimal environment in
production; see [Configuration](CONFIGURATION.md).

[`examples/mcp_stdio_client.py`](../examples/mcp_stdio_client.py) is a runnable,
dependency-free host example that performs initialization, discovers tools,
and calls capability discovery.

## Native JSON-RPC

Use `sharkrail serve` when the embedding host needs the complete SharkRail
session contract, including health, statistics, resize, EOF, inspection, and
schema discovery. See [Protocol](PROTOCOL.md).

## ACP client terminal provider

ACP sends terminal requests from an agent to its client. An ACP client can
delegate those methods to `AcpTerminalAdapter`:

```python
from sharkrail import AcpTerminalAdapter

terminals = AcpTerminalAdapter()

# In the ACP client's request router:
response = await terminals.handle(method, params)
```

The adapter implements ACP v1 `terminal/create`, `terminal/output`,
`terminal/wait_for_exit`, `terminal/kill`, and `terminal/release`. Each terminal
is bound to its ACP `sessionId`. `outputByteLimit` retains the newest output,
truncates only at a valid UTF-8 boundary, and reports `truncated` as required by
ACP. The embedding client remains responsible for the rest of ACP and for
calling `terminal/release`.

## Python

The asynchronous Python API is best for an agent implemented in the same
process. Import `SessionManager` for persistent commands or `CommandRunner` for
one-shot execution. The installed package includes `py.typed` for type-aware
clients.

## Choosing an execution mode

- Prefer `pipe` for builds, tests, linters, and scripts. It preserves separate
  stdout and stderr and is easiest to parse.
- Use `pty` only when a program changes behavior without a terminal or needs
  interactive input and resize.
- Use direct executable plus argument arrays by default. Select an explicit
  shell only when shell language is part of the requested operation.

MCP is a convenient discovery surface, not a sandbox. Place untrusted commands
inside an OS sandbox, container, or VM in addition to a SharkRail policy.
