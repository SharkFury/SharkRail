# Security model

SharkRail supervises processes with the authority of its host. It reduces
accidental command-construction, resource, cleanup, and observability risks; it
does not isolate hostile code.

## Trust boundary

The machine owner trusts the SharkRail package, its host process, selected
executable, working directory, inherited environment, and operating-system
controls. Agent-generated executable names, arguments, input, and output may be
untrusted. MCP and JSON-RPC stdio peers are local but are not automatically
trusted.

Assets in scope are host availability, child-process ownership, command and
environment confidentiality, output integrity, and the authority to select
what executes. Network isolation, filesystem isolation, credential brokering,
and malware containment are outside this boundary.

## Threats and controls

| Threat | Built-in control | Residual risk / required composition |
| --- | --- | --- |
| Shell injection | Direct argv by default; shell use is explicit | An explicitly selected shell interprets its script |
| Unexpected executable | Allow/deny and absolute-path policy rules | Path contents and executable signatures are not verified |
| Resource exhaustion | Output, input, event, session, process, CPU, memory, wall and idle limits | OS limits vary; use a container/VM for hostile workloads |
| Orphan descendants | Job Objects or process groups with bounded escalation | Privileged processes and WSL descendants may escape |
| Secret leakage | Arguments/environment omitted from default logs; output audit is opt-in | Child output itself may contain secrets |
| Protocol memory denial | Bounded frames, pages, sessions, input, output and pending requests | The host must bound process count and invocation rate too |
| Dependency or release substitution | Pinned Actions, CodeQL, Scorecard, Trusted Publishing, SBOM and artifact attestations | Consumers must verify provenance and secure their own resolver |

Execution policy is a host-owned guardrail. Keep the policy file outside an
agent-writable directory, use absolute executable and cwd allowlists, disable
parent-environment inheritance, require deadlines, and run SharkRail as a
least-privileged account. Policy is not a sandbox because permitted processes
retain the host's OS permissions.

## Security invariants

- Protocol stdout contains protocol frames only; diagnostics go to stderr.
- No default runtime feature makes a network request or exports telemetry.
- Loss, deadline intervention, resource intervention, and degraded cleanup are
  explicit structured outcomes.
- Unknown policy fields fail closed and model-supplied arguments cannot weaken
  the host policy.
- Session identifiers are unguessable; ACP terminal access is additionally
  scoped to the owning ACP session.

See [SECURITY.md](../SECURITY.md) for reporting and supported versions.
