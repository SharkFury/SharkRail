# Changelog

Notable changes to SharkRail are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and package releases
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- Re-license SharkRail under the MIT License.
- Reorganize project, integration, operations, governance, and support documentation.

### Fixed

- Bound backend disposal inside the session monitor so a stuck pipe close cannot
  prevent a session from reaching a terminal state.

## 0.1.0 - 2026-09-03

### Added

- Concurrent pipe and persistent terminal sessions.
- Native POSIX PTY and Windows ConPTY support.
- POSIX process-group and Windows Job Object tree cleanup.
- Stdin, EOF, resize, interrupt, cancellation escalation, timeout, wait, and dispose.
- Ordered output/lifecycle events and cursor-based reads.
- Byte-accurate output truncation and runtime resource limits.
- Structured error codes and execution stages.
- Explicit shell and WSL target routing.
- Stdio JSON-RPC 2.0 runtime service.
- Capability negotiation and active doctor diagnostics.
- Bounded event pages, session retention, input, RPC, and shutdown behavior.
- CPU, memory, process-count, wall-time, and idle-time policies.
- Lossless Base64 output and incremental UTF-8 stream decoding.
- Event timestamps, trace IDs, runtime health/stats, and session inspection.
- Structured logs, redacted bounded event audits, diagnostic bundles, and optional OpenTelemetry.
- Windows, Linux, and macOS CI with reliability stress tests and a coverage gate.
- Tested PyPI trusted-publishing and GitHub Release workflows.

### Changed

- Unified CLI, Python, and JSON-RPC execution on the supervised session runtime.
- Guaranteed structured terminal states for backend, drain, termination, and disposal failures.

## 0.0.1

### Added

- Initial open-source repository layout.
- CLI entry point and command execution core.
- Unit tests for command parsing and execution result handling.
