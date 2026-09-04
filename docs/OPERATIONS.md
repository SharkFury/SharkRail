# Operations guide

This guide covers a long-lived local SharkRail stdio service embedded in an
agent host. The host remains responsible for service supervision and isolation.

## Production checklist

1. Pin a released SharkRail version and verify its GitHub attestation and SBOM.
2. Run `sharkrail doctor --json` on each deployment image and save the result.
3. Use a reviewed execution policy with absolute paths, required timeouts,
   bounded output/resources, and minimal environment inheritance.
4. Keep stdio private to the parent host; do not expose it as a network socket.
5. Capture `runtime.health`, `runtime.stats`, structured stderr logs, and
   redacted event audits according to a retention policy.
6. Give the service an outer process/memory limit and restart policy.
7. Test shutdown, cancellation, descendant cleanup, and disk-full behavior in
   the deployment environment.

## Health and alert signals

No response before the host deadline means the process is not live.
`runtime.health.ready=false` means new work should not be routed. A `degraded` status is
operationally usable only if every listed reason is accepted by the host.

Alert on increasing internal errors, drain timeouts, cancellation escalation,
dropped output, event-recorder errors, active sessions near capacity, and
completed-session evictions. Baselines are workload-specific; publish the
window, command mix, platform, and configured limits with any threshold.

## Incident runbook

- Stop admitting new sessions and retain `runtime.health`/`runtime.stats`.
- Cancel affected sessions, then close service stdin to trigger bounded global
  shutdown. If the service remains alive beyond the host deadline, terminate
  its owned process tree from the outer supervisor.
- Run `sharkrail doctor --bundle` and collect redacted structured logs. Review
  event audits before sharing because opt-in output capture may contain secrets.
- Record OS, Python/package version, backend/mode, policy limits, completion
  reason, error code, degradation, and a minimal reproduction.
- Rotate or revoke credentials if child output or an output-enabled audit may
  have exposed them.

## Capacity and recovery

Capacity is bounded by active sessions, retained sessions/events, output/input
budgets, pending RPC requests, and OS process/handle limits. Load-test the real
mix of pipe and PTY commands. Backpressure or reject work before SharkRail's
hard limit rather than retrying immediately.

Sessions are process-local and intentionally not durable. After a host crash,
start a new runtime, reconcile any externally visible work, and treat previous
session IDs as lost. SharkRail does not replay commands.

## Upgrade and rollback

Read the changelog and protocol compatibility policy, run the full conformance
and deployment smoke suite, then replace the stopped service. Do not attempt an
in-place protocol upgrade with live sessions. Roll back by stopping the new
runtime and reinstalling the previously verified wheel; commands already
started must be reconciled separately.
