# Troubleshooting

Start every investigation with:

```bash
sharkrail --version
sharkrail capabilities --json
sharkrail doctor
```

For a shareable, secret-minimized snapshot:

```bash
sharkrail doctor --bundle sharkrail-diagnostics.json
```

Review the bundle before attaching it to an issue. It excludes command
arguments, environment values, and session output by design.

## A command works in my terminal but not in SharkRail

SharkRail direct mode does not evaluate aliases, shell functions, pipelines,
redirections, variable expansion, or shell startup files. Pass the executable
and each argument directly, or explicitly select a shell:

```bash
sharkrail run -- git status --short
sharkrail shell bash "git status --short | head"
```

Also verify the working directory, executable path, and environment overlay.

## PTY or resize is unavailable

Inspect `runtime.capabilities` or `sharkrail capabilities --json`. On Windows,
ConPTY requires a supported Windows version and `pywinpty`; on Linux and macOS,
SharkRail uses the native PTY facilities. Do not silently fall back to pipe mode
when terminal behavior matters.

## The process exited but the session has not completed

SharkRail drains output after the root process exits. A descendant may still
hold a pipe or terminal handle open. The drain is bounded; when the deadline is
exceeded SharkRail attempts tree cleanup and returns `DRAIN_TIMEOUT` instead of
hanging indefinitely.

## Output is missing or a cursor expired

Check retained and dropped byte counters in the result or `session.inspect`.
Output budgets are byte-based. Event history is a bounded ring; a client whose
cursor falls behind receives `EVENT_CURSOR_EXPIRED` with the first available
cursor. Resubscribe from that cursor and treat the gap as explicit data loss.

For arbitrary binary output, consume `data_base64` or the result's Base64 fields
instead of relying on the UTF-8 convenience view.

## Cancellation leaves work behind

Inspect cancellation events and the capability response. SharkRail escalates
from interrupt to terminate to process-tree kill. POSIX process groups and
Windows Job Objects cover ordinary descendants, but sufficiently privileged
processes may escape. WSL descendant cleanup is explicitly best effort in v0.1.

## JSON-RPC responses appear out of order

This is expected: requests may run concurrently. Match every response by its
JSON-RPC `id`. Keep protocol stdout exclusively for JSON messages and send host
logs elsewhere.

## Preparing an issue

Follow [SUPPORT.md](../SUPPORT.md). Include the diagnostic bundle, smallest
reproduction, expected and actual result, SharkRail version, OS version, Python
version, execution mode, and target. Never attach credentials or unreviewed raw
audit output.
