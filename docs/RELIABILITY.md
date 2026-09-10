# Reliability Contract

SharkRail treats command execution as a supervised, bounded lifecycle. The
contract applies to the CLI, Python API, and JSON-RPC service because all three
use `SessionManager` as the execution core.

## Terminal-state guarantee

Every successfully started session reaches `completed` or `failed`. Backend
errors during wait, output drain, forced termination, or disposal are converted
to stable `ExecutionError` values and emitted as `session.error`. Output drain,
forced termination, individual I/O operations, and runtime shutdown all have
deadlines.

Process exit and session completion remain separate. SharkRail observes the root
process status independently from pipe EOF, then drains its pipes or terminal.
If descendants keep an inherited output handle open beyond the drain deadline,
SharkRail kills the owned process tree and reports `DRAIN_TIMEOUT` instead of
hanging forever.

## Bounded resources

- Retained output, output events, event history, event page size, active
  sessions, completed sessions, pending RPC requests, per-write input, and total
  session input all have limits.
- Old event cursors fail explicitly with `EVENT_CURSOR_EXPIRED`.
- Completed sessions expire by age or capacity and return `SESSION_EXPIRED`.
- `ResourceLimits` maps memory, CPU time, and process-count policies to POSIX
  `setrlimit` and Windows Job Objects.
- `timeout_ms` limits total runtime; `idle_timeout_ms` limits time without
  output. A non-destructive `session.wait` deadline is separate from both.

## Concurrency and shutdown

Input, EOF, resize, interrupt, and cancellation operations are serialized per
session. Cancellation and disposal are idempotent. Cancellation steps are
reported before they are attempted, followed by `cancellation.completed` with
the outcome and elapsed time.

On transport EOF, active sessions are shut down before outstanding wait
requests are joined. Both operations have finite deadlines.

## Platform limits

Windows Job Objects and POSIX process groups cover ordinary descendants.
Processes with sufficient privileges can deliberately escape these mechanisms.
Windows pipe processes are created suspended, assigned to their Job Object, and
only then resumed. Assignment or resume failure is fail-closed, so user code
does not run under the weaker `taskkill` fallback. Each ConPTY is owned by a
dedicated broker process. The broker is placed in its kill-on-close Job Object
before it may invoke `pywinpty`, so a blocked native spawn, read, or write can be
terminated without pinning an interpreter worker thread. The broker and user
tree use separate Job Objects: only the user Job carries requested aggregate
memory, CPU-time, and process-count limits, while the broker Job provides an
unmetered ownership boundary. Cleanup closes both kill-on-close handles before
awaiting broker reaping and reaches a repeatable disposed state even when Job
accounting cannot report zero before the deadline.
WSL cleanup is best effort until an in-distribution supervisor is available.
Resource-limit behavior also follows OS semantics; for example, POSIX process
limits may be account-wide and not every memory failure has a uniquely
identifiable exit status.
