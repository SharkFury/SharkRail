# Changelog

Notable changes to SharkRail are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and package releases
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - Unreleased

### Added

- Add an experimental, self-hosted asynchronous Job service with REST submit,
  inspect, result, output, and cancel APIs; required idempotency keys; bounded
  admission and execution; and explicit volatile/durable response state.
- Add default in-memory SQLite and durable file-SQLite Job stores, local output
  storage, attempt fencing, lease renewal, restart recovery, transactional
  callback Outbox records, HMAC-signed delivery, retry, and dead-letter state.
- Add a Control Master that supervises one replaceable integrated Worker using
  heartbeat/progress checks, graceful drain, bounded restart backoff, and
  best-effort orphan-process cleanup.
- Add strict platform-aware TOML discovery, environment overrides, packaged and
  repository configuration examples, and `config sample/paths/show/validate/init`
  commands.
- Document the portable long-term architecture and clearly separate unshipped
  PostgreSQL, object-storage, multi-host, and independent Executor work.

### Changed

- Bind HTTP tenant identity to distinct bearer credentials, reserve detailed
  health state for a separate administrator token, and require loopback HTTP
  listeners with a same-host TLS reverse proxy for remote exposure.
- Give every Job a bounded default deadline and output budget, reject unknown
  request fields, and scope registered callback endpoints to one tenant.
- Start Windows pipe processes suspended and fail closed unless SharkRail can
  assign them to their Job Object before resuming user code.

### Fixed

- Preserve child stderr and timeout exit code 124 in the human CLI, accept empty
  argument values, and return structured JSON for CLI validation failures.
- Use canonical macOS system paths without weakening verified no-follow reads.
- Require callback signing secrets, return stable JSON for unexpected HTTP
  failures, and strictly validate JSON-RPC control parameter types and ranges.
- Fail closed on unsafe pre-existing POSIX storage paths, publish Job output
  pairs atomically, reconcile crash leftovers, and bound durable retention.
- Materialize host resource ceilings for Job execution and reject policy-denied
  submissions before persistence.
- Drain Workers using the configured global deadline and sweep reported POSIX
  process groups even after their leaders exit.
- Isolate each Windows ConPTY in a pre-contained broker process, serialize close
  against writes, and verify zero active Job processes after termination.
- Bound and isolate blocking ConPTY spawn/write calls, clean up late spawn
  results after cancellation, and remove invalid waits on Windows Job handles.
- Make HTTP Job execution deny-all without a host policy, always use a clean
  child environment, recover expired executor leases, cancel starts during
  shutdown, and roll back partially persisted output pairs.
- Preserve pending callback records during volatile TTL cleanup, reconcile TTL
  without requiring a new submission, harden existing state/output directory
  permissions, and fail closed for unverifiable WSL cwd symlink boundaries.
- Send both canonical POSIX VEOF transitions for unterminated input and reject
  close-stdin in raw PTY mode where no reliable half-close exists.
- Reap POSIX process groups after root exit and on caller cancellation, account
  stdin before potentially blocking writes, make POSIX PTY I/O cancellable, and
  flush incremental UTF-8 decoders at EOF without misclassifying valid U+FFFD.
- Enforce executable and working-directory policy against the effective Linux
  command inside canonical WSL invocations.
- Close the Job cancel/start race, fence callback delivery attempts, deliver
  callbacks concurrently under one hard DNS/connect/TLS/request deadline, pin
  validated addresses, reject unsafe destinations by default, and never follow
  callback redirects.
- Create SQLite databases, journals, locks, and sensitive configuration files
  with private permissions, and bound expired volatile Job retention.
- Make ConPTY disposal cancellation-safe, reap and close stuck brokers, preserve
  executable-not-found errors across the broker boundary, report every actual
  tree-kill step, and isolate broker overhead from user aggregate quotas.
- Replace heartbeat-derived process cleanup authority with synchronous
  ownership registration, birth identities, and duplicated Windows Job handles.
- Fail notification readiness after sustained reconciliation errors, roll back
  partial service construction, account all persisted metadata, and recover
  filesystem output deletion through durable idempotent finalizers.
- Validate SQLite sidecars as service-owned private regular files and read
  configuration, policy, and secret files through verified non-following handles.
- Repair the MCP `session_read` input schema, avoid reverse DNS during HTTP
  binding, select IPv6 for IPv6 listeners, keep Python 3.9 mypy checks active,
  and emit unique runtime-only SBOM components.

### Security

- Enforce protected Windows DACLs for sensitive configuration, durable state,
  SQLite sidecars, locks, and persisted output.

## 0.1.2 - 2026-09-07

### Changed

- Use `src/sharkrail/_version.py` as the single package-version source for
  runtime imports, build metadata, and release validation.

## 0.1.1 - 2026-09-07

### Fixed

- Allow the artifact-only GitHub Release job to verify tags without requiring a
  checked-out Git repository.

## 0.1.0 - 2026-09-04

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
- GitHub Release workflow with signed distributions and SBOM assets; PyPI
  publication is explicitly deferred while the project application is pending.
- Runnable examples covering every supported integration surface and core
  execution scenario, with automated smoke coverage.

### Changed

- Re-license SharkRail under the MIT License.
- Organize the implementation by architectural responsibility under `core`,
  `runtime`, `integrations`, and `observability` packages while preserving the
  package-level public API.
- Reorganize project, integration, operations, governance, and support documentation.
- Define the project's public-value charter, non-commercial stewardship,
  evidence model, and feature-admission criteria.
- Unified CLI, Python, and JSON-RPC execution on the supervised session runtime.
- Guaranteed structured terminal states for backend, drain, termination, and disposal failures.

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

## 0.0.1

### Added

- Initial open-source repository layout.
- CLI entry point and command execution core.
- Unit tests for command parsing and execution result handling.
