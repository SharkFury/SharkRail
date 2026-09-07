# Configuration and limits

SharkRail favors explicit request options and constructor arguments over hidden
global configuration. Limits are enforced by the shared session runtime, so the
CLI, Python API, and JSON-RPC service observe the same core behavior.

## Asynchronous service configuration

`sharkrail server` reads strict TOML from `/etc/sharkrail/sharkrail.toml` on
Unix or `%ProgramData%\SharkRail\sharkrail.toml` on Windows. An explicitly
selected missing or invalid file fails startup; an absent implicit system file
is valid and selects bounded SQLite memory mode.

```bash
sharkrail config paths
sharkrail config sample
sharkrail config init --system
sharkrail config validate
sharkrail config show
```

Use `--config PATH` for a specific file or `SHARKRAIL_CONFIG_FILE` for a process
default. CLI selection has precedence over environment, which has precedence
over the system path. The following environment values override individual
TOML settings:

| Environment variable | Setting |
| --- | --- |
| `SHARKRAIL_JOB_STORE_URL` | `job_store.url` |
| `SHARKRAIL_JOB_STORE_URL_FILE` | `job_store.url_file` |
| `SHARKRAIL_OUTPUT_STORE_URL` | `output_store.url` |
| `SHARKRAIL_LISTEN` | `server.listen` |
| `SHARKRAIL_AUTH_TOKEN` | `server.auth_token` |
| `SHARKRAIL_STATE_DIR` | Base directory for relative durable paths |

Without a database setting, `sqlite:///:memory:` keeps the service immediately
usable but loses jobs, idempotency records, and callbacks when the Worker
restarts. Responses and `/health/state` identify this as `volatile`. For
single-host restart recovery use:

```toml
[job_store]
url = "sqlite:///sharkrail.db"

[output_store]
url = "file://./output"
max_total_bytes = 1073741824
```

Relative paths resolve under `/var/lib/sharkrail` on Unix and
`%ProgramData%\SharkRail\data` on Windows, unless `SHARKRAIL_STATE_DIR` is set.
File SQLite uses WAL, full synchronous writes, foreign keys, and a local
single-instance lock. The current release supports SQLite JobStore and local
file OutputStore only; unsupported schemes fail startup instead of silently
falling back to memory.

The complete installed example is
[`configs/sharkrail.toml.example`](../configs/sharkrail.toml.example). Unknown
keys, invalid values, a non-loopback listener without a bearer token, and a
group/world-writable Unix configuration are rejected.

## Command controls

| Control | CLI | JSON-RPC `session.start` | Meaning |
| --- | --- | --- | --- |
| Execution mode | `--mode pipe\|pty` | `spec.mode` | Separate pipes or merged terminal stream |
| Working directory | `--cwd PATH` | `spec.cwd` | Child working directory |
| Parent environment | `--clean-env` | `spec.inherit_env` | Inherit all parent variables or start clean |
| Total timeout | `--timeout-ms N` | `timeout_ms` | Wall-clock execution deadline |
| Idle timeout | `--idle-timeout-ms N` | `idle_timeout_ms` | Deadline with no output activity |
| Output budget | `--max-output-bytes N` | `max_output_bytes` | Combined retained output budget |
| Memory | `--memory-bytes N` | `spec.resources.memory_bytes` | OS-enforced memory policy where supported |
| CPU time | `--cpu-time-seconds N` | `spec.resources.cpu_time_seconds` | OS-enforced CPU policy where supported |
| Process count | `--process-count N` | `spec.resources.process_count` | Descendant/process policy where supported |
| Target | `--target native\|wsl` | `spec.target` | Native OS or WSL routing |

`inherit_env=false` (or CLI `--clean-env`) removes application environment
variables. On Windows, SharkRail retains `SYSTEMROOT` when present because the
Windows loader and `CreateProcess` require that bootstrap value for some
executables. Explicit request environment values are then applied as an
overlay.

Use `sharkrail run --help`, `sharkrail shell --help`, and
`runtime.capabilities` as the executable source of truth. Resource policies
follow OS semantics and may expose degradation reasons.
Capability collection is discovery-only and labels its evidence accordingly;
run `sharkrail doctor` for active probes and inspect each started session for
the process-tree mechanism actually acquired.

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

## Execution policy

Hosts can reject unsafe requests before process creation by passing an
`ExecutionPolicy` to `SessionManager` or `CommandRunner`. The CLI and stdio
server accept the same policy as strict JSON:

```bash
sharkrail serve --policy ./examples/security/policy.json
sharkrail run --policy ./examples/security/policy.json --clean-env \
  --timeout-ms 30000 --max-output-bytes 1048576 -- python -V
```

Policies can allow or deny executable names, restrict working directories and
environment overlays, prohibit parent-environment inheritance, require an
absolute executable and deadline, and cap output, memory, CPU, process count,
and runtime. Unknown policy fields fail closed. A rejection returns the stable
`POLICY_DENIED` code and the rule name without starting a process.

Name-based executable allowlists are convenient but still depend on `PATH`.
Use `require_absolute_executable` together with a controlled clean environment
when the executable identity is security-sensitive. SharkRail policy reduces
accidental authority; it is not an isolation boundary.
