# Versioning and compatibility

SharkRail versions its Python package and JSON-RPC protocol separately because
they evolve at different rates.

## Package versions

Published packages use Semantic Versioning: `MAJOR.MINOR.PATCH`.

- Before `1.0`, minor releases may contain breaking Python API or CLI changes.
- Patch releases fix defects without intentionally breaking documented behavior.
- Every breaking change must be called out in the changelog with migration notes.
- After `1.0`, breaking public API or CLI changes require a new major version.

Anything exported through `sharkrail.__all__`, documented CLI syntax, and
documented result behavior is treated as public. Internal modules and
undocumented attributes are not compatibility guarantees.

## Protocol versions

`runtime.hello` reports a `MAJOR.MINOR.PATCH` protocol version. Compatibility is
negotiated by protocol major version and runtime capabilities.

Within one protocol major version SharkRail may add:

- optional response fields;
- event kinds;
- methods;
- error codes; and
- capability flags.

Clients must ignore unknown object fields and event kinds. They must treat
unsupported methods and capabilities as structured, recoverable outcomes.
Removing or changing the meaning of an existing required field, method, event,
or error requires a protocol major-version change.

## Deprecation policy

When practical, a public API scheduled for removal is documented as deprecated
for at least one minor release before removal. Security and correctness issues
may require faster changes; those exceptions are documented prominently in the
release notes.

## Platform compatibility

Capability negotiation is authoritative. The package version alone does not
guarantee that a machine exposes PTY, a particular shell, WSL, or a resource
control. CI tests the supported OS/Python matrix described in [BUILD.md](../BUILD.md),
while `runtime.capabilities` describes the machine actually running SharkRail.
