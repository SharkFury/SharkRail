# Reliable asynchronous jobs

Status: experimental single-host implementation in `Unreleased`; the
multi-host architecture in this document remains a design direction.

This document defines an optional, self-hosted client/server layer for long-
running SharkRail commands. Clients declare desired state, the API persists it,
and idempotent controllers continuously reconcile observed state toward that
desired state. A client can submit a command, receive a job ID, disconnect, and
later receive a terminal notification without supervising the execution
connection. Persistence across restart requires file SQLite mode.

This service does not turn SharkRail into a workflow engine or hosted service.
It supervises one command or interactive session per job. DAGs, schedules,
business retries, credentials, and sandbox provisioning remain outside the
execution core.

See [ASYNC_JOBS.zh-CN.md](ASYNC_JOBS.zh-CN.md) for the Chinese translation.

## Current implementation boundary

The current release implements the useful single-host core:

| Implemented now | Deliberately deferred |
| --- | --- |
| HTTP submit, inspect, result, output, and cancel APIs | Multi-host scheduling and failover |
| Required idempotency keys and fenced attempt ownership | PostgreSQL and S3-compatible adapters |
| SQLite memory default and durable file SQLite | Independent Executor Master and process pool |
| Local bounded output files | Incremental durable output streaming and SSE |
| One Control Master supervising one replaceable Worker | Automatic retry after a command starts |
| Bounded controller, executor, request, and notification concurrency | Interactive PTY Jobs and remote executors |
| Transactional callback Outbox, HMAC signatures, retry, and dead letters | Language SDKs and workflow orchestration |

The Worker owns one authoritative SQLite connection and uses bounded role
threads. The Master monitors heartbeat and reconciliation progress, replaces a
failed or stalled Worker with bounded exponential backoff, and makes a
best-effort cleanup of process groups reported by the failed Worker. Native OS
process-tree ownership remains the primary cleanup mechanism.

Memory mode is intentionally volatile. Only file SQLite mode claims that an
accepted resource survives a service restart. Output is committed when the
command completes; live durable chunk streaming is not implemented. All later
sections that describe separate Control/Executor Masters, PostgreSQL, object
storage, multi-host leases, or SSE are target design, not shipped behavior.

## Goals and guarantees

Within the current implementation boundary and selected durability class,
SharkRail guarantees that:

- the SQLite resource record, rather than an application queue, is the source
  of truth while its configured store exists;
- in file-SQLite mode, an accepted job has been durably recorded;
- duplicate submissions with the same idempotency key do not create duplicate
  jobs;
- at most one current execution attempt can update a job;
- every started attempt reaches an explicit terminal or lost state;
- the terminal result and notification intent are committed atomically;
- notifications are delivered at least once and can be deduplicated;
- file-SQLite service restarts do not lose accepted resource records or pending
  notifications; interrupted running attempts become `executor_lost`;
- output loss, executor loss, retry, and callback failure are never silent.

Controllers provide eventual convergence, not instantaneous success. Every
reconciliation action must be repeatable after a crash, and all externally
visible state changes must use optimistic concurrency and fencing.

SharkRail must not claim exactly-once command execution. A host can fail after a
process starts but before durable confirmation. Commands with external side
effects may therefore be unsafe to retry. The default is no automatic retry
after an attempt has started; callers may opt in only for commands they know to
be idempotent.

## Implemented single-host architecture

```text
Client --HTTP--> Control Master --> integrated Worker
                                    |--> bounded request threads
                                    |--> reconciliation threads
                                    |--> bounded execution threads --> SessionManager --> OS process
                                    |--> notification threads --> signed webhook
                                    +--> SQLite JobStore + local OutputStore
```

## Target multi-host architecture

```text
Client
  |
  | HTTP: declare desired state, inspect status, request cancellation
  v
Control Master
  |--> API Workers
  |--> Controller Workers
  +--> Notification Workers
  |
  | validate + compare-and-swap transaction
  v
JobStore (source of truth)
  | resources · attempts · leases · events · outbox
  |
  +--> Job/Scheduler/Lease reconcilers --> Executor Master
  |                                          |
  |                                          +--> Executor Workers
  |                                                  |--> SessionManager
  |                                                  |--> OS process / PTY
  |                                                  +--> OutputStore
  +--> Notification reconciler --> signed webhook --> Client
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
    "max_output_bytes": 16777216,
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

## Configuration discovery and installation

The server reads one system configuration file by default:

| Environment | Active configuration | Installed example |
| --- | --- | --- |
| Linux and other Unix services | `/etc/sharkrail/sharkrail.toml` | `/etc/sharkrail/sharkrail.toml.example` |
| Windows system service | `%ProgramData%\SharkRail\sharkrail.toml` | `%ProgramData%\SharkRail\sharkrail.toml.example` |
| Container | `/etc/sharkrail/sharkrail.toml` | included in the image and source distribution |

The Windows location follows the system-wide convention used by
[Docker Engine](https://docs.docker.com/engine/daemon/) and
[Git for Windows](https://git-scm.com/book/en/v2/Getting-Started-First-Time-Git-Setup).
The implementation resolves `FOLDERID_ProgramData` through the Windows Known
Folder API; it must not assume that the system drive is `C:`. A service does not
implicitly read a login user's `%APPDATA%`, because its identity may differ from
the installing user's identity.

`--config <path>` has highest precedence, followed by
`SHARKRAIL_CONFIG_FILE`, then the platform system path. A missing implicit file
is valid and uses built-in defaults. A missing explicitly requested file,
invalid TOML, unknown key, conflicting setting, insecure permission, or invalid
value fails startup with a precise error; it must not be mistaken for an
unconfigured database.

Configuration values use the precedence CLI flags, environment variables,
configuration file, then built-in defaults. The server reports the selected
file, non-secret effective settings, and origin of each value through
`sharkrail config show`; `sharkrail config validate` validates without starting
the service.

Every async-service distribution must include
[`configs/sharkrail.toml.example`](../configs/sharkrail.toml.example). Native
system packages and the Windows installer place a copy at the installed example
path without overwriting an existing file. A Python wheel cannot safely write to
a privileged system directory, so it embeds the same resource and provides:

```text
sharkrail config sample
sharkrail config init --system
```

`sample` writes to stdout. `init --system` copies the example atomically to the
platform system path, requests the platform's normal elevation when needed, and
refuses to overwrite an existing file unless `--force` is explicit. Package
removal must not delete an operator-modified active configuration.

## Persistence configuration

Persistence is replaceable behind two independent contracts:

- `JobStore`: transactional resources, attempts, leases, revisions, durable
  events, and callback Outbox records;
- `OutputStore`: stdout/stderr objects and checksums.

When no database URL is configured, SharkRail starts with SQLite's in-memory
mode. This is the availability-first, zero-configuration mode:

```text
SHARKRAIL_JOB_STORE_URL=sqlite:///:memory:
SHARKRAIL_OUTPUT_STORE_URL=file://<runtime-directory>/output
```

Command output is captured under a hard in-memory byte limit and written to the
local OutputStore when the command finishes; incremental durable output is not
implemented. The volatile runtime directory is private to the service instance
and may be removed after restart. To enable durable single-host state, configure
SQLite:

```text
SHARKRAIL_STATE_DIR=/var/lib/sharkrail
SHARKRAIL_JOB_STORE_URL=sqlite:////var/lib/sharkrail/sharkrail.db
SHARKRAIL_OUTPUT_STORE_URL=file:///var/lib/sharkrail/output
```

Future multi-node installations are expected to use PostgreSQL and an
S3-compatible output store; these URLs are not accepted by the current release:

```text
SHARKRAIL_JOB_STORE_URL=postgresql://user:password@db/sharkrail
SHARKRAIL_OUTPUT_STORE_URL=s3://sharkrail-output/jobs
```

On Windows, a relative SQLite or file URL resolves under
`%ProgramData%\SharkRail\data`; on Unix it resolves under
`SHARKRAIL_STATE_DIR`, whose service default is `/var/lib/sharkrail`.
`SHARKRAIL_JOB_STORE_URL_FILE` supports a DSN mounted from a
secret; it is mutually exclusive with the direct URL. Unknown schemes must fail
startup. A backend is supported only after passing the same
transaction, revision, lease, migration, crash-recovery, and Outbox conformance
suite; a generic database driver alone is not a reliability guarantee.

SQLite is the recommended durable store for local and single-instance
operation, not a multi-node database. Enable foreign keys, WAL, busy timeout,
explicit transactions, and a documented durability setting; perform schema
migrations and backups. The database and output directories must be on durable
storage. Never let multiple server instances share one SQLite file over NFS,
SMB, or another network filesystem.

### Volatile SQLite memory mode

SQLite memory mode keeps the program usable without database setup, but it is
an explicitly degraded durability class:

- Job state, idempotency keys, leases, status history, and pending callbacks are
  lost when the in-memory SQLite State Worker or its host exits;
- restart recovery, multi-instance ownership, durable callbacks, and accepted-
  Job durability are not claimed;
- Job count, metadata bytes, event history, TTL, and temporary-output bytes are
  bounded; overload is rejected with `429` rather than risking OOM;
- submit and status responses include `"durability": "volatile"`, startup logs
  emit one prominent warning, and `/health/state` reports
  `DEGRADED_VOLATILE_STORE`;
- a caller that needs restart survival must configure file SQLite.

The integrated Worker owns exactly one authoritative in-memory SQLite
connection shared by its bounded role threads. It generates a new `store_epoch`
on every start. If the Worker fails, the Master uses its latest heartbeat to
make a best-effort process-group cleanup before replacement; the new Worker has
a new empty store and epoch.

An absent database setting selects SQLite memory mode. An explicit
`sqlite:///:memory:` selects it intentionally. By contrast, an invalid or
unusable configured file SQLite database never triggers automatic memory
fallback: Worker startup fails and the Master applies its bounded restart
policy. Silent fallback would create split state and false success.

H2 is not used. It is a strong embedded database for JVM applications, but in a
Python runtime it would add a JVM, JDBC integration, another server process for
cross-process access, and a second operational toolchain without improving the
volatile durability guarantee. In-memory SQLite preserves SQL transactions,
constraints, and most of the same schema path as durable file SQLite with no
new runtime dependency. Backend-independent conformance tests still protect
against dialect-specific behavior.

## Deployment portability

The same architecture must run on a bare-metal host, a virtual machine, or in a
container. Platform packaging may change, but the persistence and recovery
contract does not:

- a zero-configuration installation uses bounded volatile memory state; a
  single-instance durable installation uses SQLite and local output on a durable
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

## Control-plane process reliability

A durable database does not by itself make the control plane reliable. The
design must prevent overload where possible, contain failures when prevention
fails, restart failed processes, and reconstruct all work from durable state.
An accepted Job must never depend on one API or controller process remaining
alive.

### Master and Worker process model

The production process model uses a small Master process and replaceable Worker
processes. The Master never accepts business requests, owns Jobs, schedules
commands, or stores authoritative state. It only starts Workers, monitors their
progress, drains them, replaces failed or stale Workers, aggregates process
health, and coordinates shutdown. Keeping it free of business work minimizes its
memory footprint and failure surface.

Worker pools are separated by responsibility:

```text
Control Master
  |-- API Worker pool
  |-- Controller Worker pool
  +-- Notification Worker pool

Executor Master
  +-- Executor Worker pool
        +-- owned command process tree
```

The Control Master and Executor Master are independent failure domains. A
control-plane overload therefore cannot directly terminate running commands.
On a small installation the same executable can start both service trees, but
they still use separate masters, process groups, resource budgets, and shutdown
policies. Command processes and output pumps never run inside an API or
Controller Worker.

Cross-platform implementations create Workers with spawn semantics rather than
depending on Unix-only fork behavior. The Master maintains a bounded, configured
worker count; it does not create a new process per request. API listener
ownership and handoff must use one documented cross-platform strategy so a
Worker replacement cannot drop accepted requests or allow two Workers to write
one response.

Each Worker reports a monotonic progress heartbeat over bounded local IPC. A
Worker is replaced if it exits, misses its progress deadline, exceeds a hard
resource ceiling, or fails a bounded self-check. The Master first marks it
unready and requests a graceful drain; after the deadline it terminates the
Worker and its owned child tree. Replacement uses exponential backoff with
jitter and a per-role restart-rate limit. If a role repeatedly crashes, its
circuit opens and the Master reports the role as degraded instead of creating a
fork storm.

Worker replacement is safe because Workers are disposable. A new Worker gets a
new `worker_id` and `boot_id`, loads no ownership from its predecessor, and
reacquires all work from `JobStore`. Every claim and write remains protected by
lease, revision, and fencing checks. Planned upgrades start a new Worker
generation, wait for readiness, drain the old generation, and preserve reserved
capacity for control traffic throughout the transition.

The Master itself is managed by an external supervisor such as the host service
manager or container runtime. The external supervisor provides automatic
restart, exponential backoff with jitter, a restart-rate limit, and last-exit
diagnostics. A Master cannot recover itself from an out-of-memory kill, deadlock,
runtime failure, or machine restart. If the Master dies, surviving Workers are
terminated or adopted according to one explicit platform contract; they must
never continue indefinitely as unmonitored orphan processes.

While the Control Master is unavailable, the independently supervised Executor
service continues its owned commands, persists bounded output where possible,
and retries status reporting. If a deployment deliberately places both service
trees in one failure domain, it accepts the weaker behavior that a shared
failure can produce `executor_lost`.

Each Master start gets an `instance_id`; each Worker start gets a `worker_id` and
`boot_id`. Durable claims include these identities, a lease deadline, and an
epoch. Writes from an old or paused Worker are rejected by revision and fencing
checks.

### Startup, shutdown, and recovery

Startup is safe only after configuration is valid, the schema version is
compatible, the `JobStore` is writable, and role-specific ownership is acquired.
Schema migration uses a database lock and is a separate administrative action;
ordinary replicas do not race to migrate. The service exposes:

```text
GET /health/live   process event loop and watchdog are making progress
GET /health/ready  this role can safely accept its class of traffic
GET /health/state  dependency, queue, lease, and reconciliation diagnostics
```

Liveness must not fail merely because the database is temporarily unavailable,
which would create a restart storm. Readiness fails when the role cannot safely
serve, while diagnostic state reports saturation and dependency failures.

On graceful shutdown, the API first stops admitting new Jobs, controllers stop
claiming new work, outstanding database transactions finish within a deadline,
and owned leases are either released or allowed to expire. On an ungraceful
exit, database transactions roll back atomically. After restart, controllers
perform a full resync before normal scheduling, then reconcile unfinished Jobs,
expired claims, and pending Outbox records. No in-memory checkpoint is required
for correctness.

SQLite mode permits exactly one control-plane instance. An exclusive process
lock prevents accidental double start. This mode provides durable restart
recovery, not uninterrupted availability. Multi-instance availability requires
PostgreSQL: API replicas are stateless, controller replicas actively share work
through short database claims, and any singleton maintenance operation uses a
renewable lease plus fencing epoch. Local mutexes are never used for distributed
ownership.

### Overload protection

The control plane must shed load before memory, file descriptors, threads, or
database connections are exhausted:

- bound HTTP connections, request bodies, request duration, and per-tenant
  submission rate;
- keep the database connection pool fixed and small, transactions short, and
  pool-wait time bounded;
- bound every controller work queue, resync batch, callback batch, retry set,
  and in-flight asynchronous task count;
- stream output directly to `OutputStore`; never accumulate command output or
  complete result sets in API/controller memory;
- reserve separate concurrency budgets for submission, status/cancel, Executor
  heartbeat/result updates, and notification delivery;
- prioritize heartbeat, cancellation, and completion writes over new Job
  admission so overload cannot hide running work;
- apply global and per-tenant quotas and fair scheduling so one caller cannot
  starve recovery traffic or other tenants.

When admission capacity is full, new submissions receive `429 Too Many Requests`
with `Retry-After`. When a required dependency is unavailable, they receive
`503 Service Unavailable`. The server must reject before creating partial state.
Already accepted Jobs remain durable and are reconciled later. Status and cancel
traffic retain a reserved budget and must not share an unbounded queue with new
submissions.

Exceptions are isolated at request and Job boundaries. A malformed or repeatedly
failing Job receives a stable failure Condition and is quarantined after a
bounded retry count; it must not crash a controller loop or create a hot retry
cycle. Repeated process crashes trigger supervisor backoff and an operator-visible
degraded state rather than an immediate infinite restart loop.

### Detection and reliability tests

Monitor process RSS, CPU, file descriptors, event-loop lag, thread count,
database pool wait, request rejection, work-queue depth, reconciliation lag,
oldest lease age, Outbox age, restart count, and time since each controller last
made progress. A watchdog may terminate a deadlocked process after recording
bounded diagnostics; external supervision then restarts it.

The control plane is not release-ready until automated tests demonstrate:

| Injection | Required evidence |
| --- | --- |
| Kill API/controller during admission | No `202` without a durable Job; committed Jobs survive |
| Kill or deadlock one Worker | Master replaces only that Worker; durable work is reacquired |
| Kill the Control Master | External supervisor restarts it; no indefinite orphan Workers; full resync converges |
| Force repeated Worker crashes | Per-role backoff and circuit breaker prevent a fork storm |
| Kill controller after external action but before status write | Reconcile is idempotent or reports execution uncertainty |
| Saturate submissions | New work gets bounded `429`; status, cancel, and heartbeats still progress |
| Exhaust output rate | Memory remains bounded and output truncation/degradation is explicit |
| Slow or disconnect the database | Admission stops, transactions remain bounded, recovery does not stampede |
| Crash notification workers repeatedly | Job outcome remains unchanged; Outbox resumes without loss |
| Start a second SQLite control plane | Startup is rejected before either instance can corrupt ownership |
| Restart all control-plane roles | Full resync converges Jobs, leases, and notifications without manual repair |

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
  "max_output_bytes": 16777216,
  "callback": {"endpoint_id": "build-system"}
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

## Incremental durable output (target extension)

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

Zero-configuration loopback mode is deliberately unauthenticated and fixes all
requests to the single tenant `default`; it is for local development only. Any
production deployment or same-host TLS proxy must configure distinct tenant
bearer credentials and a separate administrator token. The service also
requires:

- credential-bound tenant identity and per-job authorization when authentication
  is configured;
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

## Delivery status and plan

### Shipped experimental single-node subset

- REST submit, inspect, output, result, and cancel APIs;
- system configuration discovery, validation/show/init commands, and the
  installed example configuration;
- one Control Master and one integrated Worker, bounded role threads,
  heartbeat/progress checks, drain, replacement, and restart backoff;
- `JobStore`/`OutputStore` interfaces, bounded in-memory SQLite and temporary-
  file defaults, durable file SQLite, migrations, backups, and idempotent
  admission;
- declarative `spec`/`status`, revision checks, durable events, conditions, and
  state-driven reconciliation;
- one controller set and executor using the existing `SessionManager`;
- local-file `OutputStore`;
- transactional Outbox, signed webhook, retry, and dead letters;
- regression tests for Master/Worker startup and restart bounds, duplicate
  submission, ownership fencing, callback signing, cancellation, timeout,
  overload limits, durable restart recovery, and instance locking.

Remaining single-host hardening includes incremental output persistence,
platform-native orphan-process fault injection, disk-full/corruption tests,
periodic full resync metrics, and independent Executor process isolation.

### Phase 2: multi-executor reliability

- PostgreSQL `JobStore`, S3-compatible `OutputStore`, and backend conformance
  tests;
- multiple Control Masters, active-active Controller Workers, rolling Worker
  generations, and fenced singleton maintenance;
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

Promotion from experimental to supported contract requires failure-injection tests
for every claimed transition, native process-leak tests on Windows/Linux/macOS,
and documented recovery evidence. Implementation progress alone is not a
reliability claim.
