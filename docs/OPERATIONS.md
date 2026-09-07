# Operations guide

This guide covers both the long-lived local stdio integration and the optional
self-hosted asynchronous HTTP service. The host remains responsible for outer
service supervision and isolation.

## Run the asynchronous service

Inspect the effective settings before startup:

```bash
sharkrail config validate --config /etc/sharkrail/sharkrail.toml
sharkrail config show --config /etc/sharkrail/sharkrail.toml
sharkrail server --config /etc/sharkrail/sharkrail.toml
```

On Windows, use `%ProgramData%\SharkRail\sharkrail.toml`. By default the
Control Master supervises one integrated Worker. It checks heartbeat and
reconciliation progress, gracefully drains on shutdown, restarts a failed or
stalled Worker with bounded exponential backoff, and stops after a restart
storm so an outer supervisor can alert and decide what to do. Use
`--single-process` only for diagnostics and tests.

Probe `/health/live`, `/health/ready`, and `/health/state`. Volatile mode is
ready but reports `DEGRADED_VOLATILE_STORE`; it is suitable for zero-config
use, not restart survival. Configure file SQLite and a durable output directory
before relying on disconnect-and-return behavior across restarts.

Non-loopback listening requires `server.auth_token` or
`SHARKRAIL_AUTH_TOKEN`. This is bearer authentication, not transport security;
terminate TLS at a trusted local reverse proxy and restrict network access.
Callback targets are operator-registered by ID and can use HMAC secrets. Alert
when `pending_callbacks` grows or `dead_callbacks` is nonzero.

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
8. For HTTP Jobs, verify idempotency-key reuse, callback deduplication, Worker
   restart, SQLite backup/restore, and output capacity before production use.

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

Native sessions are process-local and intentionally not durable. After a host crash,
start a new runtime, reconcile any externally visible work, and treat previous
session IDs as lost. Async Job resource records can survive a Worker restart
only in file-SQLite mode; interrupted running attempts are marked
`executor_lost` and are not silently replayed.

## Upgrade and rollback

Read the changelog and protocol compatibility policy, run the full conformance
and deployment smoke suite, then replace the stopped service. Do not attempt an
in-place protocol upgrade with live sessions. Roll back by stopping the new
runtime and reinstalling the previously verified wheel; commands already
started must be reconciled separately.
