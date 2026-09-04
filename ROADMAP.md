# Roadmap

This roadmap communicates direction, not a delivery promise. Priorities may
change as production integrations expose new constraints. Shipped behavior is
documented in [CHANGELOG.md](CHANGELOG.md).

## Now: harden the v0.1 contract

- Validate lifecycle and cleanup behavior across the complete CI matrix
- Expand Windows ConPTY and WSL failure coverage
- Improve examples and integration feedback from early adopters
- Keep package, protocol, reliability, and observability documentation aligned

## Next: easier agent integration

- MCP and ACP adapters built on the existing session contract
- Reference integrations for an agent host and an IDE extension
- Optional Unix Domain Socket and current-user Windows Named Pipe transports
- Compatibility fixtures for third-party client implementations

## Later: stronger terminal and distribution support

- WSL in-distribution supervisor for stronger Linux descendant cleanup
- Optional VT screen model and snapshot events
- Signed release artifacts and provenance verification guidance
- Long-running workload soak tests and published performance baselines

## Explicit non-goals

- A terminal emulator UI
- A hosted multi-tenant execution service
- Credential storage or automatic privilege elevation
- Malware isolation or a replacement for a sandbox, container, or VM
- Prompt-regex lifecycle detection

Proposals should begin with a concrete agent or automation failure mode. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the design-change process.
