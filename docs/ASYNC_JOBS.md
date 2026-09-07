# Reliable asynchronous jobs

Status: design proposal; not implemented in v0.1.

This document proposes an optional, self-hosted client/server layer for long-
running SharkRail commands. Clients declare desired state, the API persists it,
and idempotent controllers continuously reconcile observed state toward that
desired state. A client can submit a command, receive a durable job ID,
disconnect, and later receive a terminal notification without supervising the
execution connection.

The proposal does not turn SharkRail into a workflow engine or hosted service.
It supervises one command or interactive session per job. DAGs, schedules,
business retries, credentials, and sandbox provisioning remain outside the
execution core.

See [ASYNC_JOBS.zh-CN.md](ASYNC_JOBS.zh-CN.md) for the Chinese translation.

## Goals and guarantees

The design aims to guarantee that:

- the durable resource record, rather than an in-memory queue, is the source of
  truth;
- an accepted job has been durably recorded;
- duplicate submissions with the same idempotency key do not create duplicate
  jobs;
- at most one current execution attempt can update a job;
- every started attempt reaches an explicit terminal or lost state;
- the terminal result and notification intent are committed atomically;
- notifications are delivered at least once and can be deduplicated;
- client, API, scheduler, and notification restarts do not lose accepted work;
- output loss, executor loss, retry, and callback failure are never silent.

Controllers provide eventual convergence, not instantaneous success. Every
reconciliation action must be repeatable after a crash, and all externally
visible state changes must use optimistic concurrency and fencing.

SharkRail must not claim exactly-once command execution. A host can fail after a
process starts but before durable confirmation. Commands with external side
effects may therefore be unsafe to retry. The default is no automatic retry
after an attempt has started; callers may opt in only for commands they know to
be idempotent.

## Architecture

```text
Client
  |
  | HTTP: declare desired state, inspect status, request cancellation
  v
API server
  |
  | validate + compare-and-swap transaction
  v
JobStore (source of truth)
  | resources · attempts · leases · events · outbox
  |
  +--> Job controller --------+
  +--> Scheduler controller --+--> Executor agent --> SessionManager
  +--> Lease reconciler ------+                         |--> OS process / PTY
  +--> Notification controller                         +--> OutputStore
  +--> Retention controller
             |
             +--> signed webhook --> Client
```

The API and controllers are stateless apart from the `JobStore`. Controllers do
not pass ownership through an in-memory queue; the queue is a query over durable
resources that still require reconciliation. `SessionManager` remains the
semantic execution core. A new `JobManager` implements resource validation and
controller coordination without duplicating process lifecycle logic.

## Declarative resource model

Each Job is one durable resource with immutable identity, desired state, and
controller-owned observed state:

```json
{
  "metadata": {
    "id": "job_01K...",
    "tenant_id": "tenant_123",
    "generation": 1,
    "revision": 8,
    "created_at": "..."
  },
  "spec": {
    "command": ["pytest", "-q"],
    "cwd": "/workspace/project",
    "desired_state": "active",
    "timeout_seconds": 3600,
    "retry_policy": {"before_start": 3, "after_start": 0},
    "callback_endpoint_id": "build-system"
  },
  "status": {
    "observed_generation": 1,
    "phase": "running",
    "attempt_id": "attempt_01K...",
    "conditions": [
      {"type": "Accepted", "status": true, "reason": "Persisted"},
      {"type": "Ready", "status": false, "reason": "CommandRunning"}
    ]
  }
}
```

`spec` expresses caller intent. After admission, execution fields are immutable;
only supported intent such as `desired_state=cancelled` may change and increment
`generation`. Controllers alone update `status`. Every write increments
`revision`; compare-and-swap rejects stale writers. A controller has acted on
the current intent only when `status.observed_generation == metadata.generation`.

Conditions carry stable machine-readable `type`, `status`, `reason`, and
transition time. They explain states such as accepted, scheduled, running,
output degraded, executor lost, result available, and notification delivered
without multiplying the primary phase into ambiguous combinations.

## Persistence configuration

Persistence is replaceable behind two independent contracts:

- `JobStore`: transactional resources, attempts, leases, revisions, durable
  events, and callback Outbox records;
- `OutputStore`: stdout/stderr objects and checksums.

The zero-configuration default is SQLite plus local files. Configuration is
read with the precedence CLI flags, environment variables, configuration file,
then defaults:

```text
SHARKRAIL_STATE_DIR=<operating-system user state directory>/sharkrail
SHARKRAIL_JOB_STORE_URL=sqlite:///sharkrail.db
SHARKRAIL_OUTPUT_STORE_URL=file://./output
```

Relative SQLite and file URLs resolve under `SHARKRAIL_STATE_DIR`. An absolute
single-node server configuration may use:

```text
SHARKRAIL_STATE_DIR=/var/lib/sharkrail
SHARKRAIL_JOB_STORE_URL=sqlite:////var/lib/sharkrail/sharkrail.db
SHARKRAIL_OUTPUT_STORE_URL=file:///var/lib/sharkrail/output
```

Multi-node installations use a supported adapter, initially PostgreSQL and an
S3-compatible output store:

```text
SHARKRAIL_JOB_STORE_URL=postgresql://user:password@db/sharkrail
SHARKRAIL_OUTPUT_STORE_URL=s3://sharkrail-output/jobs
```

`SHARKRAIL_JOB_STORE_URL_FILE` should also be supported for a DSN mounted from a
secret; it is mutually exclusive with the direct environment variable. Unknown
schemes must fail startup. A backend is supported only after passing the same
transaction, revision, lease, migration, crash-recovery, and Outbox conformance
suite; a generic database driver alone is not a reliability guarantee.

SQLite is a supported default for local and single-instance operation, not a
multi-node database. Enable foreign keys, WAL, busy timeout, explicit
transactions, and a documented durability setting; perform schema migrations
and backups. The database and output directories must be on durable storage.
Never let multiple server instances share one SQLite file over NFS, SMB, or
another network filesystem.

## Deployment portability

The same architecture must run on a bare-metal host, a virtual machine, or in a
container. Platform packaging may change, but the persistence and recovery
contract does not:

- a single-instance installation may use SQLite and local output on a durable
  host directory;
- a container must mount `SHARKRAIL_STATE_DIR` from durable host or volume
  storage because its writable layer may be replaced;
- upgrades must prevent two server instances from opening the same SQLite
  database concurrently;
- multiple API/controller instances or executors on multiple hosts require a
  shared PostgreSQL `JobStore`; large output should use S3-compatible object
  storage;
- database credentials may be supplied through
  `SHARKRAIL_JOB_STORE_URL_FILE`, regardless of the process supervisor or
  container runtime.

Files surviving a process or machine restart and controllers restoring logical
state are separate requirements. Operators need tested database backups,
output retention, restart procedures, and restore drills in every deployment
form.

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
Cancellation updates the resource intent to `desired_state=cancelled`; it does
not directly signal a process and falsely report success before reconciliation.

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

Terminal job outcomes are immutable. Callback delivery has a separate
condition; failure to notify cannot change a successful command into a failed
command. Every event has a unique ID and monotonic per-job sequence.

## Reconciliation loops

Each controller repeatedly lists or watches resources for which observed state
differs from desired state, computes one bounded next action, writes it with a
revision precondition, and tries again. Reconciliation must be level-driven:
lost or duplicated wake-up events affect latency, not correctness.

```python
async def reconcile(job_id: str) -> None:
    job = await store.get(job_id)
    if job.status.phase in TERMINAL_PHASES:
        await ensure_result_and_notification(job)
    elif job.spec.desired_state == "cancelled":
        await ensure_cancelled(job)
    elif not job.status.attempt_id:
        await ensure_attempt_assigned(job)
    else:
        await ensure_attempt_observed(job)
```

`ensure_*` operations are idempotent and keyed by immutable resource or attempt
IDs. The durable event journal is only a wake-up optimization and audit trail;
periodic full resynchronization repairs missed events. Controllers use bounded
work queues with exponential backoff and jitter. A permanently invalid resource
gets a condition explaining why instead of being retried in a hot loop.

Controller responsibilities stay narrow:

- admission validates policy and records the initial resource atomically;
- scheduling selects an eligible executor and creates one fenced Attempt;
- execution observes `SessionManager` and persists progress or a terminal result;
- lease reconciliation detects lost executors and applies explicit retry policy;
- notification reconciles terminal results with Outbox delivery state;
- retention removes output and records expiry before final resource deletion.

Deleting a Job first sets a deletion timestamp. A retention finalizer prevents
the record from disappearing until processes are stopped, output retention is
handled, and required audit facts are durable. Finalizers must have an operator-
visible timeout and recovery procedure so a broken cleanup path cannot retain a
resource forever.

## Claims, leases, and fencing

The scheduler controller claims a reconcilable job in one database transaction
and records:

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

The Attempt ID is also passed to the executor as an operation token. Repeated
delivery of the same assignment must observe or resume the same local operation,
not start another process. Because process creation and durable acknowledgement
cannot form one cross-system transaction, SharkRail still reports uncertainty
after a host failure and never claims exactly-once execution.

Queue admission is bounded by global, tenant, executor, and policy limits.
Overload returns `429` or `503`; it must not create an unbounded queue. Jobs that
exceed their queue deadline transition to `expired` and generate a terminal
notification.

## Crash and restart semantics

| Failure | Required behavior |
| --- | --- |
| Client disconnect | Does not affect an accepted job |
| API restart | No loss; state is read from the durable `JobStore` |
| Scheduler restart | Expired leases are reconciled |
| Controller misses an event | Periodic full resync finds the state mismatch |
| Duplicate reconcile | Idempotent action and revision check prevent duplicate state changes |
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
store contract:

```python
class OutputStore(Protocol):
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

- `jobs`: metadata, immutable spec, status, conditions, generation, revision,
  deletion timestamp, finalizers, request hash, and policy;
- `job_attempts`: executor ownership, lease epoch, timestamps, result/error;
- `executor_nodes`: capacity, capabilities, heartbeat, drain state;
- `resource_events`: monotonic change sequence used for wake-up and audit;
- `output_objects`: byte ranges, hashes, retention and storage keys;
- `callback_endpoints`: tenant-owned destination and secret reference;
- `callback_outbox`: immutable event and delivery state;
- `callback_deliveries`: attempt history, response class, next retry;
- `audit_events`: bounded administrative and security history.

The database must enforce idempotency-key uniqueness, event-ID uniqueness, one
active attempt per job, valid state transitions, atomic resource-and-event
writes, revision compare-and-swap, and fencing-epoch checks. Application checks
alone are insufficient. Event compaction must retain a safe revision watermark;
a watcher behind that watermark performs a full resync instead of guessing what
it missed.

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
- `JobStore`/`OutputStore` interfaces, SQLite/local-file defaults, migrations,
  backups, and idempotent admission;
- declarative `spec`/`status`, revision checks, durable events, conditions, and
  periodic full resynchronization;
- one controller set and executor using the existing `SessionManager`;
- local-file `OutputStore`;
- transactional Outbox, signed webhook, retry, and dead letters;
- restart, duplicate-submit, duplicate-reconcile, missed-event, callback-failure,
  SQLite disk-full/corruption, and crash-injection tests.

### Phase 2: multi-executor reliability

- PostgreSQL `JobStore`, S3-compatible `OutputStore`, and backend conformance
  tests;
- executor registration, capability matching, lease, heartbeat, and fencing;
- reconciler for expired leases and uncertain attempts;
- tenant quotas, fair scheduling, queue deadlines, and draining;
- failover, network-partition, and duplicate-delivery conformance tests.

### Phase 3: optional adapters

- SSE/operator event stream and language SDKs;
- external sandbox and execution-target adapters;
- bounded soak tests and published reliability evidence;
- optional message-broker adapter only when database Outbox throughput is a
  measured constraint.

Promotion from proposal to supported contract requires failure-injection tests
for every claimed transition, native process-leak tests on Windows/Linux/macOS,
and documented recovery evidence. Implementation progress alone is not a
reliability claim.
