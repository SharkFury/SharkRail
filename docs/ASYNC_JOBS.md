# Reliable asynchronous jobs

Status: design proposal; not implemented in v0.1.

This document proposes an optional, self-hosted client/server layer for long-
running SharkRail commands. A client submits a command, receives a durable job
ID, disconnects, and later receives a terminal notification. The server owns
execution supervision, bounded output, cancellation, result retention, and
notification delivery.

The proposal does not turn SharkRail into a workflow engine or hosted service.
It supervises one command or interactive session per job. DAGs, schedules,
business retries, credentials, and sandbox provisioning remain outside the
execution core.

See [ASYNC_JOBS.zh-CN.md](ASYNC_JOBS.zh-CN.md) for the Chinese translation.

## Goals and guarantees

The design aims to guarantee that:

- an accepted job has been durably recorded;
- duplicate submissions with the same idempotency key do not create duplicate
  jobs;
- at most one current execution attempt can update a job;
- every started attempt reaches an explicit terminal or lost state;
- the terminal result and notification intent are committed atomically;
- notifications are delivered at least once and can be deduplicated;
- client, API, scheduler, and notification restarts do not lose accepted work;
- output loss, executor loss, retry, and callback failure are never silent.

SharkRail must not claim exactly-once command execution. A host can fail after a
process starts but before durable confirmation. Commands with external side
effects may therefore be unsafe to retry. The default is no automatic retry
after an attempt has started; callers may opt in only for commands they know to
be idempotent.

## Architecture

```text
Client
  |
  | HTTP: submit, inspect, cancel
  v
API server ------------------------------+
  |                                      |
  | transaction                          | result queries
  v                                      |
PostgreSQL                               |
  | jobs · attempts · leases · outbox    |
  |                                      |
  +--> Scheduler --> Executor node ------+
  |                    |
  |                    +--> SessionManager --> OS process / PTY
  |                    +--> Output sink --> file or object storage
  |
  +--> Notification dispatcher --> signed webhook --> Client
```

The API, scheduler, and notification dispatcher are stateless apart from the
database. `SessionManager` remains the semantic execution core. A new
`JobManager` coordinates durable jobs and attempts without duplicating process
lifecycle logic.

Recommended production storage is PostgreSQL plus S3-compatible object storage.
SQLite and local files may be supported for development and single-node tests,
but must not be presented as equivalent multi-node guarantees.

## API

### Submit

```http
POST /v1/jobs
Authorization: Bearer <token>
Idempotency-Key: <caller-generated-key>
Content-Type: application/json
```

```json
{
  "command": ["pytest", "-q"],
  "cwd": "/workspace/project",
  "timeout_seconds": 3600,
  "callback": {"endpoint_id": "build-system"},
  "retry_policy": {"before_start": 3, "after_start": 0}
}
```

The server returns `202 Accepted` only after the job and its immutable request
hash are committed:

```json
{
  "job_id": "job_01K...",
  "status": "queued",
  "revision": 1,
  "status_url": "/v1/jobs/job_01K..."
}
```

The uniqueness boundary is `(tenant_id, idempotency_key)`. Reusing a key with
the same canonical request returns the original job. Reusing it with different
content returns `409 Conflict`.

### Inspect and control

```http
GET  /v1/jobs/{job_id}
GET  /v1/jobs/{job_id}/result
GET  /v1/jobs/{job_id}/output?stream=stdout&cursor=...
POST /v1/jobs/{job_id}/cancel
```

Polling remains a supported recovery path when a webhook cannot be delivered.
SSE or protocol event subscriptions may be added for operators and interactive
clients, but business clients must not need a live connection.

## Job and attempt states

Business job states are distinct from in-process session states:

```text
queued -> assigned -> running -> succeeded
                            +--> failed
                            +--> timed_out
                            +--> canceled
                            +--> executor_lost
queued/assigned ----------------> expired
```

A job may contain multiple attempts, but only when its retry policy permits it:

```text
Job
  +-- Attempt 1: executor_lost
  +-- Attempt 2: succeeded
```

Terminal job outcomes are immutable. Callback delivery has a separate state;
failure to notify cannot change a successful command into a failed command.
Every mutation increments a job `revision`, and every event has a unique ID and
monotonic per-job sequence.

## Claims, leases, and fencing

The scheduler claims a queued job in one database transaction and records:

```text
executor_id
lease_expires_at
lease_epoch
```

Executors heartbeat before the lease expires. Each reassignment increments
`lease_epoch`. All executor writes must compare the expected epoch and job
revision. A stale executor may finish work, but it cannot overwrite the result
chosen by the current owner. A database constraint must allow at most one active
attempt per job.

Queue admission is bounded by global, tenant, executor, and policy limits.
Overload returns `429` or `503`; it must not create an unbounded queue. Jobs that
exceed their queue deadline transition to `expired` and generate a terminal
notification.

## Crash and restart semantics

| Failure | Required behavior |
| --- | --- |
| Client disconnect | Does not affect an accepted job |
| API restart | No loss; state is read from PostgreSQL |
| Scheduler restart | Expired leases are reconciled |
| Notification restart | Pending Outbox records are resumed |
| Executor control-process loss | Owned process tree is cleaned; attempt becomes `executor_lost` |
| Executor host loss | Lease expiry produces `executor_lost` |
| Database unavailable | Stop admission and assignment; do not run unrecorded work |

The first implementation should not promise to reattach to an arbitrary live
process after executor restart. Reconstructing pipes, PTYs, Job Object handles,
process groups, and byte offsets is not portable. The reliable v1 behavior is to
contain and terminate the owned tree, report `executor_lost`, and apply the
explicit retry policy.

There is an unavoidable crash window between OS process creation and durable
start confirmation. SharkRail records an attempt before starting the process,
uses an executor-generated fencing token, and reports uncertainty rather than
silently rerunning a command that may have side effects.

## Durable output

Long-running output must not live only in `SessionManager` memory. Add an output
sink contract:

```python
class OutputSink(Protocol):
    async def append(self, job_id, attempt_id, stream, offset, data): ...
    async def finalize(self, job_id, attempt_id): ...
```

Store output objects separately from database rows. Metadata includes job and
attempt IDs, stream, absolute byte offset, byte length, checksum, storage key,
retained range, and dropped-byte count. Preserve the current raw-byte,
monotonic-offset, truncation, and drain-before-completion contracts.

Final results contain output sizes, retention/truncation facts, checksums, and
authorized result URLs. Retention expiry must be observable and independent of
job outcome retention.

## Transactional notifications

When an attempt reaches a terminal state, the server updates the job and inserts
a notification into an Outbox in the same transaction:

```sql
BEGIN;
UPDATE jobs SET status = 'succeeded', revision = revision + 1 ...;
INSERT INTO callback_outbox (...);
COMMIT;
```

This prevents a crash between result persistence and notification scheduling.
The dispatcher claims Outbox rows with a lease and delivers each event at least
once. Suggested retry delays are immediate, 5 seconds, 30 seconds, 2 minutes,
10 minutes, and 1 hour, with randomized jitter. Respect `Retry-After` for `429`.
Timeouts, network errors, and `5xx` retry; permanent `4xx` responses enter a
dead-letter state. Operators can inspect and replay dead letters.

Example terminal webhook:

```json
{
  "event_id": "evt_01K...",
  "event_type": "job.completed",
  "job_id": "job_01K...",
  "revision": 8,
  "attempt": 1,
  "status": "succeeded",
  "exit_code": 0,
  "started_at": "...",
  "completed_at": "...",
  "result_url": "https://rail.example/v1/jobs/job_01K.../result",
  "output": {
    "stdout_bytes": 18230,
    "stderr_bytes": 320,
    "truncated": false,
    "sha256": "..."
  }
}
```

Sign the timestamp and exact body with HMAC-SHA256, expose the event ID in a
header, and reject stale signatures. Receivers deduplicate by `event_id` and
return any `2xx` only after their own durable commit.

## Security boundary

Callers should reference administrator-registered callback endpoints rather
than submit arbitrary URLs. Endpoint registration validates scheme, host, port,
DNS resolution, redirect behavior, and tenant ownership. Production delivery
must prevent loopback, link-local, metadata-service, and unauthorized private-
network targets, including DNS rebinding.

The server also requires:

- authenticated tenants and per-job authorization;
- host-owned execution policy that callers cannot weaken;
- command, cwd, environment, runtime, output, and concurrency limits;
- encrypted callback secrets and credential references;
- HMAC or mTLS callback authentication;
- redacted logs and bounded audit records;
- sandbox/container/VM targets for untrusted commands;
- result and output retention with explicit expiry.

SharkRail remains an execution supervisor, not a security sandbox.

## Data model

The minimum durable model contains:

- `jobs`: immutable request, request hash, business state, revision, policy;
- `job_attempts`: executor ownership, lease epoch, timestamps, result/error;
- `executor_nodes`: capacity, capabilities, heartbeat, drain state;
- `output_objects`: byte ranges, hashes, retention and storage keys;
- `callback_endpoints`: tenant-owned destination and secret reference;
- `callback_outbox`: immutable event and delivery state;
- `callback_deliveries`: attempt history, response class, next retry;
- `audit_events`: bounded administrative and security history.

The database must enforce idempotency-key uniqueness, event-ID uniqueness, one
active attempt per job, valid state transitions, revision checks, and fencing-
epoch checks. Application checks alone are insufficient.

## Observability and SLOs

Expose queue age, admission rejection, scheduling latency, active leases,
expired leases, executor heartbeat age, execution duration, terminal outcomes,
output bytes, truncation, Outbox age, callback attempts, callback latency, and
dead-letter count. Logs correlate `tenant_id`, `job_id`, `attempt_id`,
`executor_id`, `trace_id`, and `event_id` without logging secrets.

Initial service objectives should be measurable rather than absolute, for
example:

- zero acknowledged jobs missing from durable storage;
- zero terminal results without a corresponding Outbox event;
- zero concurrent active attempts for the same job in conformance tests;
- bounded detection time for expired executor leases;
- published callback-delivery latency percentiles and dead-letter rate.

## Delivery plan

### Phase 1: single-node durable service

- REST submit, inspect, output, result, and cancel APIs;
- PostgreSQL `JobStore` and idempotent admission;
- one scheduler and executor using the existing `SessionManager`;
- local-file `OutputSink`;
- transactional Outbox, signed webhook, retry, and dead letters;
- restart, duplicate-submit, callback-failure, and crash-injection tests.

### Phase 2: multi-executor reliability

- executor registration, capability matching, lease, heartbeat, and fencing;
- reconciler for expired leases and uncertain attempts;
- S3-compatible output storage;
- tenant quotas, fair scheduling, queue deadlines, and draining;
- failover, network-partition, and duplicate-delivery conformance tests.

### Phase 3: optional adapters

- SSE/operator event stream and language SDKs;
- external sandbox and execution-target adapters;
- bounded soak tests and published reliability evidence;
- optional message-broker adapter only when PostgreSQL Outbox throughput is a
  measured constraint.

Promotion from proposal to supported contract requires failure-injection tests
for every claimed transition, native process-leak tests on Windows/Linux/macOS,
and documented recovery evidence. Implementation progress alone is not a
reliability claim.
