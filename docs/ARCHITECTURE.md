# Architecture

SharkRail separates the stable intent an agent expresses from the platform
mechanism used to execute it.

```text
Agent / IDE / automation
          |
 CLI | Python API | MCP | JSON-RPC stdio | ACP client adapter
          |
   host execution policy
          |
   SessionManager
   lifecycle · quotas · events · cursors · telemetry
          |
   command / shell / target routing
          |
    +-----+------------------------+
    |                              |
 Windows backend                POSIX backend
 pipes · ConPTY                 pipes · PTY
 Job Objects                    process groups
    |
 WSL adapter (wsl.exe --exec)
```

## Layer responsibilities

### Interfaces

The CLI, Python API, MCP tools, JSON-RPC adapter, and ACP terminal provider
translate caller input into the same session operations. They do not implement
separate process lifecycles. Protocol stdout is reserved for protocol messages;
diagnostics go to stderr.

### Policy

The host-owned policy is enforced immediately before session admission and
process creation. Protocol arguments can narrow a request but cannot weaken the
host policy. Policy is a guardrail, not an OS isolation boundary.

### Session manager

`SessionManager` owns session state, deadlines, cancellation serialization,
output retention, event ordering, cursor validity, completed-session retention,
and runtime telemetry. It is the semantic center of the project.

### Routing

Routing converts direct commands, explicitly selected shells, and WSL targets
into structured argv. Direct execution never invokes a shell implicitly.
Adapters choose a target; they do not own the resulting process.

### Backends

Backends own OS handles and translate platform operations such as start, wait,
write, resize, interrupt, terminate, and kill-tree. They do not know about
JSON-RPC or client request IDs.

## Data flow

1. The interface and its published schema validate a request.
2. Host policy validates authority and resource ceilings.
3. Routing resolves the target, shell, and backend without flattening argv.
4. Atomic admission reserves capacity before the backend starts a process.
5. The backend creates the process and its ownership boundary.
6. Reader tasks emit byte-preserving output events into a bounded history.
7. The session monitor applies runtime and idle deadlines, waits for process
   exit, drains output, and produces exactly one terminal result.
8. Interfaces render that same result for humans or protocol clients.

## Ownership and invariants

- A successfully started session reaches `completed` or `failed`.
- Session sequence numbers are monotonic and never reused.
- `process.exited` precedes `session.completed`; output drain occurs between them.
- Cancellation and disposal are idempotent and serialized with other mutations.
- Every bounded loss—output, events, retention, or audit storage—is observable.
- Capability discovery labels unprobed evidence; `doctor` actively probes, and
  each session reports the mechanism it actually acquired.

## Extension points

New transports should wrap the session API rather than duplicate supervision.
New platform backends must preserve the lifecycle and error contracts. New
events and response fields must follow [VERSIONING.md](VERSIONING.md), and all
new behavior requires platform-appropriate tests.

See [PRODUCT.md](PRODUCT.md) for scope and [RELIABILITY.md](RELIABILITY.md) for
the normative lifecycle guarantees.
