# Roadmap

This roadmap is ordered by public value and evidence, not feature count or a
commercial release calendar. Priorities may change when reproducible failures
or independent integrations disprove an assumption. Shipped behavior belongs
in [CHANGELOG.md](CHANGELOG.md); decision criteria belong in the
[public value design](docs/VALUE.md).

## Now: prove the v0.1 contract

- Keep known contract mismatches at zero. Atomic admission, cleanup after
  cancellation errors, MCP input-schema enforcement, no-argument PTY/REPL, and
  Windows post-exit descendant cleanup now have regression coverage.
- Turn lifecycle, inherited-pipe, cancellation, output, encoding, process-tree,
  ConPTY, and WSL failures into a named, documented corpus.
- Define one black-box conformance manifest for CLI and protocol implementations:
  monotonic events, exactly one terminal result, `exited < drained < completed`,
  byte conservation, bounded deadlines, and owned-descendant cleanup.
- Run applicable fixtures against native Windows, Linux, and macOS behavior.
  Replace Windows mocks with child/grandchild survival checks where CI permits;
  report WSL cleanup separately from Windows launcher cleanup.
- Publish a reliability status matrix with the OS build, runtime versions,
  capabilities, iteration count, last verified commit, known gaps, and raw test
  artifacts. Do not treat line coverage alone as reliability evidence.
- Extend the shipped discovery/active-probe/session-degradation labels into a
  published history of platform verification results.
- Add measured overhead baselines against the platform's standard process API.
  The goal is bounded supervision cost, not a claim that SharkRail starts a
  process faster than `subprocess`.
- Keep the README, protocol, schema, examples, and implementation aligned with
  what has actually shipped. MCP and ACP adapters are current integrations, not
  future roadmap items.

## Next: make the contract independently useful

- Extract the failure corpus and conformance runner so they can test an
  implementation without importing the Python reference runtime.
- Validate ACP terminal and MCP tool mappings with their upstream schemas and
  compatibility fixtures instead of adding competing general protocol concepts.
- Publish reference integrations that measure setup time, host-specific glue,
  failure classification, and cleanup behavior.
- Seek independent integration reports and convert their failures into shared
  regressions; do not substitute download or star counts for this evidence.
- Maintain golden protocol fixtures and migration tests before claiming a
  compatibility level beyond the project itself.

## Later: strengthen difficult boundaries

- Evaluate a WSL in-distribution supervisor for per-session Linux descendant
  ownership without terminating an entire distribution.
- Add bounded nightly soak and race testing with reproducible seeds, process/FD/
  handle leak reports, and historical performance trends.
- Support alternative native backends only when they reduce proven reliability
  or maintenance risk; keep the contract independent of any one implementation.
- Extend the shipped SBOM and GitHub provenance workflow with reproducible-build
  verification and additional signing only when consumers can validate it.
- Consider an optional VT screen model only after integrations prove that raw
  terminal events cannot meet a concrete agent need.

## Graduation criteria

### Credible

- All advertised guarantees link to an applicable test or an explicit limit.
- Known fixtures have no silent output loss, unclassified terminal result, or
  unbounded runtime wait.
- Native platform results and degradation boundaries are publicly visible.

### Useful

- Independent hosts adopt the contract without duplicating supervision logic.
- External failure reports regularly become fixtures and regression tests.
- Compatible upgrades have executable migration and replay evidence.

### Infrastructure

- The conformance suite runs independently from the Python implementation.
- Independently maintained clients or runtimes pass it.
- Review and release responsibility is shared across maintainers with different
  platform or organizational backgrounds.

## Admission rule

A roadmap proposal must identify a beneficiary, reproducible failure,
insufficiency of existing process or PTY primitives, observable result,
platform-specific behavior, proof strategy, compatibility cost, and maintenance
owner. It is placed in the core, a backend, an adapter, an example, or rejected.
Implementation possibility alone is not a reason to add it.

## Permanent non-goals

- A terminal emulator UI or general agent framework
- A hosted, remote, or multi-tenant execution service
- Credential storage or automatic privilege elevation
- Malware isolation or a replacement for a sandbox, container, or VM
- Prompt-regex lifecycle detection
- A shell/pipeline DSL, workflow scheduler, or daemon restart manager
- Vendor-specific behavior in the execution core
