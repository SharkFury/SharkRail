# Changelog

Notable changes to SharkRail are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and package releases
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Host-enforced execution policies for executable, cwd, environment, deadline,
  output, memory, CPU, and process-count constraints.
- MCP `2025-11-25` tools and an ACP v1 client-side terminal adapter.
- Bundled protocol JSON Schema, typed-package marker, mypy gate, and runnable
  MCP host example.
- Runtime capability verification labels, per-session process-tree degradation,
  and bounded byte-accurate protocol frame input.
- Property-based protocol/output tests and clean installed-wheel smoke tests.
- Dependabot, CodeQL, OpenSSF Scorecard, CycloneDX SBOM generation, and GitHub
  artifact attestations with commit-pinned CI Actions.

### Changed

- Re-license SharkRail under the MIT License.
- Reorganize project, integration, operations, governance, and support documentation.
- Define the project's public-value charter, non-commercial stewardship,
  evidence model, and feature-admission criteria.

### Fixed

- Decouple root-process exit detection from inherited pipe EOF and bound backend
  disposal so a stuck pipe close cannot prevent a session from reaching a
  terminal state.
- Make concurrent session admission atomic and preserve forced cleanup after a
  cancellation backend error.
- Enforce MCP tool input schemas and allow no-argument PTY/REPL commands.
- Attempt Windows fallback descendant cleanup after root exit and bound the
  drain-stage kill operation.
- Attribute CPU resource termination only when the operating system reports the
  corresponding signal, avoiding false resource-limit diagnoses.

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
