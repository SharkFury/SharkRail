# SharkRail Protocol 1.0

SharkRail uses newline-delimited JSON-RPC 2.0 over stdin/stdout. Start it with `sharkrail serve`. One line is one complete request or response; the maximum request size is 1 MiB. Requests can execute concurrently, so responses may arrive in a different order and must be matched by `id`.

## Runtime methods

### `runtime.hello`

Returns the runtime name, package version, and protocol version.

### `runtime.capabilities`

Returns runtime-probed platform, modes, targets, available shells, process-tree mechanisms and fallbacks, granular resource limits, output limit, feature flags, and degradation reasons. Clients must use this response rather than infer support from the OS name. A started session reports the mechanism it actually acquired; for example, Windows pipe execution can report `taskkill_fallback` when Job Object assignment is rejected by a parent job.

### `runtime.health`

Returns lightweight `live`, `ready`, status, degradation reasons, and active-session count. This does not spawn a probe process; use `sharkrail doctor` for active verification.

### `runtime.stats`

Returns bounded runtime telemetry: session states and completion reasons, error counts, input/output/retained/dropped bytes, cancellations, RPC count/error/average latency, uptime, and event-recorder health.

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
    "idle_timeout_ms": 10000,
    "trace_id": "agent-tool-call-42",
    "max_output_bytes": 1048576
  }
}
```

Optional resource policy inside `spec`:

```json
{"resources":{"memory_bytes":536870912,"cpu_time_seconds":60,"process_count":32}}
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

### `session.list` and `session.inspect`

`session.list` returns retained session summaries. `session.inspect` returns one session's trace/request IDs, state, PID, timestamps, execution/drain/output age, byte counters, cancellation steps, and retained cursor range. Command arguments and environment values are not returned.

### `session.subscribe`

Returns retained events starting at the absolute `cursor`, plus `next_cursor` and `has_more`. `limit` defaults to 100 and `wait_ms` optionally performs a bounded predicate-based long poll. `session.events` is an alias. A cursor older than the retained ring fails with `EVENT_CURSOR_EXPIRED` and supplies `first_cursor`.

All events contain UTC and monotonic timestamps and a trace ID. Output events contain stream, byte offset, retained byte count, incremental UTF-8 text, lossless `data_base64`, and a decoding-error flag. PTY sessions emit `pty.output`, not synthetic stdout/stderr events. `process.started` identifies the actual process-tree mechanism, and `capability.degraded` reports any per-session fallback.

### `session.write`

Writes either UTF-8 `text` or `data_base64`. The default per-request input limit is 1 MiB.
The default cumulative input limit is 16 MiB per session.

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

Cancels an active process if necessary, releases native resources, and removes the session. Cancellation and disposal are idempotent. Completed sessions also expire by time and capacity.

## Result timing and binary output

Completed results include `duration_ms`, `drain_duration_ms`, UTF-8 `stdout`/`stderr`, and lossless `stdout_base64`/`stderr_base64`. `idle_timeout` is distinct from the total-runtime `timeout` reason; both set `timed_out` and use CLI exit code 124.

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

Stable application codes include `EXECUTABLE_NOT_FOUND`, `START_FAILED`, `INVALID_REQUEST`, `CAPABILITY_NOT_SUPPORTED`, `SESSION_NOT_FOUND`, `SESSION_EXPIRED`, `INVALID_SESSION_STATE`, `EVENT_CURSOR_EXPIRED`, `RESOURCE_LIMITED`, `DRAIN_TIMEOUT`, `TERMINATION_FAILED`, `IDLE_TIMEOUT`, and `INTERNAL_ERROR`.

## Compatibility

The protocol major version is `1`. Within a major version, new methods, fields, events, and capabilities may be added. Clients must ignore unknown response fields and event kinds. Unsupported methods return `-32601`; unsupported capabilities return a structured `CAPABILITY_NOT_SUPPORTED` error.
