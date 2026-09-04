# Configuration and limits

SharkRail favors explicit request options and constructor arguments over hidden
global configuration. Limits are enforced by the shared session runtime, so the
CLI, Python API, and JSON-RPC service observe the same core behavior.

## Command controls

| Control | CLI | JSON-RPC `session.start` | Meaning |
| --- | --- | --- | --- |
| Execution mode | `--mode pipe\|pty` | `spec.mode` | Separate pipes or merged terminal stream |
| Working directory | `--cwd PATH` | `spec.cwd` | Child working directory |
| Total timeout | `--timeout-ms N` | `timeout_ms` | Wall-clock execution deadline |
| Idle timeout | `--idle-timeout-ms N` | `idle_timeout_ms` | Deadline with no output activity |
| Output budget | `--max-output-bytes N` | `max_output_bytes` | Combined retained output budget |
| Memory | `--memory-bytes N` | `spec.resources.memory_bytes` | OS-enforced memory policy where supported |
| CPU time | `--cpu-time-seconds N` | `spec.resources.cpu_time_seconds` | OS-enforced CPU policy where supported |
| Process count | `--process-count N` | `spec.resources.process_count` | Descendant/process policy where supported |
| Target | `--target native\|wsl` | `spec.target` | Native OS or WSL routing |

Use `sharkrail run --help`, `sharkrail shell --help`, and
`runtime.capabilities` as the executable source of truth. Resource policies
follow OS semantics and may expose degradation reasons.

## SessionManager defaults

The Python `SessionManager` constructor exposes host-level limits. Current v0.1
defaults are:

| Limit | Default |
| --- | ---: |
| Retained output | 16 MiB per session |
| Active sessions | 64 |
| One input write | 1 MiB |
| Cumulative session input | 16 MiB |
| Output events | 10,000 per session |
| Retained events | 12,000 per session |
| Event page | 100 events / 256 KiB |
| Completed sessions | 256 |
| Output drain | 2 seconds |
| Termination operation | 2 seconds |
| Runtime shutdown | 5 seconds |

These defaults are part of the current implementation, not protocol constants.
Hosts with different workload and memory requirements should construct their own
`SessionManager` and record the chosen policy.

## JSON-RPC transport limits

The stdio server accepts newline-delimited JSON-RPC 2.0. A request line is
limited to 1 MiB and pending requests are bounded. `session.subscribe` pages
are bounded by both event count and serialized bytes. Exceeding a limit returns
a structured error; the server does not silently accept partial requests.

## Logging and audit files

`SHARKRAIL_LOG_LEVEL` controls structured stderr logging and defaults to
`WARNING`. Supported values follow Python logging levels.

```bash
SHARKRAIL_LOG_LEVEL=INFO sharkrail serve
```

Audit files are opt-in:

```bash
sharkrail serve \
  --event-log ./sharkrail-events.jsonl \
  --event-log-max-bytes 16777216
```

Output content is redacted by default. `--event-log-include-output` stores raw
command output and should only be used with a protected destination. See
[OBSERVABILITY.md](OBSERVABILITY.md).
