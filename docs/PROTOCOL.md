# SharkRail Protocol 1.0

SharkRail uses newline-delimited JSON-RPC 2.0 over stdin/stdout. Start it with `sharkrail serve`. One line is one complete request or response; the maximum request size is 1 MiB. Requests can execute concurrently, so responses may arrive in a different order and must be matched by `id`.

## Runtime methods

### `runtime.hello`

Returns the runtime name, package version, and protocol version.

### `runtime.capabilities`

Returns platform, modes, targets, shells, process-tree mechanism, output limit, and feature flags. Clients must use this response rather than infer support from the OS name.

## Session methods

### `session.start`

Starts a process and immediately returns `session_id`, state, pid, mode, and event cursor.

Direct request:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "session.start",
  "params": {
    "spec": {
      "executable": "python",
      "argv": ["-c", "print(input())"],
      "mode": "pipe",
      "cwd": null,
      "env": {"EXAMPLE": "value"},
      "target": "native"
    },
    "timeout_ms": 30000,
    "max_output_bytes": 1048576
  }
}
```

Shell request:

```json
{"spec":{"shell":"pwsh","script":"Write-Output hello","mode":"pipe"}}
```

WSL request:

```json
{
  "spec": {
    "executable": "python3",
    "argv": ["-V"],
    "target": "wsl",
    "wsl": {"distribution": "Ubuntu", "user": "agent", "cwd": "/work"}
  }
}
```

### `session.get`

Returns the current state and metadata for `session_id`.

### `session.subscribe`

Returns retained events starting at `cursor` plus `next_cursor`. `wait_ms` optionally performs a bounded long poll. `session.events` is an alias.

Output events contain stream, byte offset, retained byte count, UTF-8 text, and a decoding-error flag. PTY sessions emit `pty.output`, not synthetic stdout/stderr events.

### `session.write`

Writes either UTF-8 `text` or `data_base64`. The default per-request input limit is 1 MiB.

### `session.close_stdin`

Closes pipe stdin or sends terminal EOF.

### `session.resize`

Takes positive integer `cols` and `rows`; only valid for PTY sessions.

### `session.interrupt`

Sends the platform's best matching soft interrupt.

### `session.cancel`

Runs cancellation escalation. Optional fields are `interrupt_grace_ms`, `terminate_grace_ms`, and `force` (skip interrupt). The result lists the steps actually attempted.

### `session.wait`

Waits for session completion. With `wait_timeout_ms`, returns `null` when the wait deadline expires without modifying the process. A completed result includes output, exit code, completion reason, timeout flag, truncation accounting, and structured error when applicable.

### `session.dispose`

Cancels an active process if necessary, releases native resources, and removes the session.

## Errors

JSON-RPC framing errors use standard codes. Runtime errors use `-32000` with stable data:

```json
{
  "code": "EXECUTABLE_NOT_FOUND",
  "stage": "start",
  "message": "...",
  "retryable": false,
  "native": {"errno": 2}
}
```

Stable application codes include `EXECUTABLE_NOT_FOUND`, `START_FAILED`, `INVALID_REQUEST`, `CAPABILITY_NOT_SUPPORTED`, `SESSION_NOT_FOUND`, `INVALID_SESSION_STATE`, `RESOURCE_LIMITED`, and `INTERNAL_ERROR`.

## Compatibility

The protocol major version is `1`. Within a major version, new methods, fields, events, and capabilities may be added. Clients must ignore unknown response fields and event kinds. Unsupported methods return `-32601`; unsupported capabilities return a structured `CAPABILITY_NOT_SUPPORTED` error.
