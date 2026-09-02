# Changelog

## 0.1.0 - 2026-09-03

- Add concurrent pipe and persistent terminal sessions
- Add native POSIX PTY and Windows ConPTY support
- Add POSIX process-group and Windows Job Object tree cleanup
- Add stdin, EOF, resize, interrupt, cancellation escalation, timeout, wait, and dispose
- Add ordered output/lifecycle events and cursor-based reads
- Add byte-accurate output truncation and runtime resource limits
- Add structured error codes and execution stages
- Add explicit shell and WSL target routing
- Add stdio JSON-RPC 2.0 runtime service
- Add capability negotiation and doctor diagnostics
- Add Windows, Linux, and macOS CI for Python 3.9, 3.11, and 3.14
- Add a tested PyPI trusted-publishing and GitHub Release pipeline
- Unify CLI, Python, and JSON-RPC execution on the supervised session runtime
- Guarantee structured terminal states for backend, drain, termination, and disposal failures
- Add bounded drain/shutdown, idempotent cancellation, serialized session operations, and RPC backpressure
- Add bounded event pages, explicit expired cursors, completed-session retention, and total input limits
- Add CPU, memory, process-count, wall-time, and idle-time policies
- Add lossless Base64 output and incremental UTF-8 stream decoding
- Add event timestamps, trace IDs, runtime health/stats, session inspection, and active doctor probes
- Add structured logs, redacted bounded event audits, diagnostic bundles, and optional OpenTelemetry
- Add reliability stress tests, test deadlines, and a coverage gate

## 0.0.1

- Initialize open-source repository layout for SharkRail
- Add initial CLI entrypoint and command execution core
- Add unit tests for command parsing and execution result handling
