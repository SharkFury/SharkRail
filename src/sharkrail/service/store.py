"""Transactional SQLite JobStore."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .models import JobPhase, JobRecord, JobSpec, OutboxRecord, utc_now


class StoreError(RuntimeError):
    """Base JobStore error."""


class JobNotFound(StoreError):
    """Requested Job does not exist."""


class IdempotencyConflict(StoreError):
    """Idempotency key was reused with different content."""


class AdmissionLimited(StoreError):
    """Durable admission limits are full."""


class InstanceAlreadyRunning(StoreError):
    """Another service instance owns the file SQLite store."""


class SqliteJobStore:
    """SQLite-backed source of truth supporting memory and file modes."""

    def __init__(
        self,
        url: str = "sqlite:///:memory:",
        *,
        max_jobs: int = 1000,
        max_metadata_bytes: int = 64 * 1024 * 1024,
        max_event_records: int = 10000,
        job_ttl_seconds: int = 3600,
        max_queued_jobs: int = 1000,
        max_queued_jobs_per_tenant: int = 100,
        state_dir: Optional[Path] = None,
    ) -> None:
        self.url = url
        self.volatile = url == "sqlite:///:memory:"
        self.durability = "volatile" if self.volatile else "durable"
        self.store_epoch = f"store_{uuid4().hex}"
        self._max_jobs = max_jobs
        self._max_metadata_bytes = max_metadata_bytes
        self._max_event_records = max_event_records
        self._job_ttl_seconds = job_ttl_seconds
        self._max_queued_jobs = max_queued_jobs
        self._max_queued_jobs_per_tenant = max_queued_jobs_per_tenant
        self._lock = threading.RLock()
        self._closed = False
        location = _sqlite_location(url, state_dir=state_dir)
        self._instance_lock: Optional[_InstanceLock] = None
        if location != ":memory:":
            Path(location).parent.mkdir(parents=True, exist_ok=True)
            self._instance_lock = _InstanceLock(Path(location + ".lock"))
            self._instance_lock.acquire()
        try:
            self._connection = sqlite3.connect(
                location,
                check_same_thread=False,
                isolation_level=None,
                timeout=5,
            )
        except BaseException:
            if self._instance_lock is not None:
                self._instance_lock.release()
            raise
        try:
            self._connection.row_factory = sqlite3.Row
            self._configure()
            self._migrate()
            if not self.volatile:
                persisted = self._metadata("store_id")
                if persisted is None:
                    persisted = f"store_{uuid4().hex}"
                    self._set_metadata("store_id", persisted)
                self.store_epoch = persisted
        except BaseException:
            self._connection.close()
            if self._instance_lock is not None:
                self._instance_lock.release()
            raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            if self._instance_lock is not None:
                self._instance_lock.release()
            self._closed = True

    def _configure(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if self.volatile:
                self._connection.execute("PRAGMA journal_mode = MEMORY")
            else:
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")

    def _migrate(self) -> None:
        # sqlite3.executescript owns its transaction boundary, so it must not be
        # nested inside _transaction().
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    observed_generation INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    durability TEXT NOT NULL,
                    store_epoch TEXT NOT NULL,
                    attempt_id TEXT,
                    lease_owner TEXT,
                    lease_epoch INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    exit_code INTEGER,
                    reason TEXT,
                    error_json TEXT,
                    stdout_path TEXT,
                    stderr_path TEXT,
                    stdout_bytes INTEGER NOT NULL DEFAULT 0,
                    stderr_bytes INTEGER NOT NULL DEFAULT 0,
                    output_truncated INTEGER NOT NULL DEFAULT 0,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (tenant_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS jobs_phase_created
                    ON jobs (phase, created_at);
                CREATE TABLE IF NOT EXISTS resource_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, revision, kind)
                );
                CREATE TABLE IF NOT EXISTS callback_outbox (
                    event_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    endpoint_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS outbox_due
                    ON callback_outbox (state, next_attempt_at);
                """
            )
            self._set_metadata("schema_version", "1")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def _metadata(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row["value"])

    def _set_metadata(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def submit(
        self, tenant_id: str, idempotency_key: str, spec: JobSpec
    ) -> tuple[JobRecord, bool]:
        spec.validate()
        if (
            not isinstance(tenant_id, str)
            or not tenant_id
            or not isinstance(idempotency_key, str)
            or not idempotency_key
        ):
            raise ValueError("tenant_id and idempotency_key are required")
        if len(tenant_id.encode("utf-8")) > 256:
            raise ValueError("tenant_id exceeds 256 UTF-8 bytes")
        if len(idempotency_key.encode("utf-8")) > 256:
            raise ValueError("idempotency_key exceeds 256 UTF-8 bytes")
        spec_json = json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
        now = utc_now()
        job_id = f"job_{uuid4().hex}"
        conditions = [
            {
                "type": "Accepted",
                "status": True,
                "reason": "Persisted" if not self.volatile else "VolatileStore",
                "transition_time": now,
            }
        ]
        conditions_json = json.dumps(conditions, separators=(",", ":"))
        with self._transaction():
            self._prune_expired()
            existing = self._connection.execute(
                "SELECT * FROM jobs WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "idempotency key was already used with different content"
                    )
                return self._row_to_job(existing), False
            total = int(
                self._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            )
            queued = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE phase = ?",
                    (JobPhase.QUEUED.value,),
                ).fetchone()[0]
            )
            tenant_queued = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE phase = ? AND tenant_id = ?",
                    (JobPhase.QUEUED.value, tenant_id),
                ).fetchone()[0]
            )
            metadata_bytes = int(
                self._connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(tenant_id AS BLOB)) + "
                    "length(CAST(idempotency_key AS BLOB)) + "
                    "length(CAST(spec_json AS BLOB)) + "
                    "length(CAST(conditions_json AS BLOB)) + length(request_hash)), 0) "
                    "FROM jobs"
                ).fetchone()[0]
            )
            new_metadata_bytes = (
                len(spec_json.encode("utf-8"))
                + len(conditions_json.encode("utf-8"))
                + len(request_hash)
                + len(tenant_id.encode("utf-8"))
                + len(idempotency_key.encode("utf-8"))
            )
            if (
                total >= self._max_jobs
                or queued >= self._max_queued_jobs
                or tenant_queued >= self._max_queued_jobs_per_tenant
                or metadata_bytes + new_metadata_bytes > self._max_metadata_bytes
            ):
                raise AdmissionLimited("job admission capacity is full")
            self._connection.execute(
                """
                INSERT INTO jobs(
                    id, tenant_id, idempotency_key, request_hash, spec_json,
                    phase, generation, observed_generation, revision, durability,
                    store_epoch, created_at, updated_at, conditions_json
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, 1, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    idempotency_key,
                    request_hash,
                    spec_json,
                    JobPhase.QUEUED.value,
                    self.durability,
                    self.store_epoch,
                    now,
                    now,
                    conditions_json,
                ),
            )
            self._event(job_id, 1, "job.accepted")
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert row is not None
            return self._row_to_job(row), True

    def get(self, job_id: str, tenant_id: Optional[str] = None) -> JobRecord:
        with self._lock:
            if tenant_id is None:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE id = ? AND tenant_id = ?",
                    (job_id, tenant_id),
                ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return self._row_to_job(row)

    def claim_next(self, owner: str, lease_seconds: int) -> Optional[JobRecord]:
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE phase = ? ORDER BY created_at LIMIT 1",
                (JobPhase.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["id"])
            attempt_id = f"attempt_{uuid4().hex}"
            revision = int(row["revision"]) + 1
            now = utc_now()
            updated = self._connection.execute(
                """
                UPDATE jobs SET phase = ?, attempt_id = ?, lease_owner = ?,
                    lease_epoch = lease_epoch + 1, lease_expires_at = ?,
                    revision = ?, updated_at = ?
                WHERE id = ? AND revision = ? AND phase = ?
                """,
                (
                    JobPhase.ASSIGNED.value,
                    attempt_id,
                    owner,
                    time.time() + lease_seconds,
                    revision,
                    now,
                    job_id,
                    row["revision"],
                    JobPhase.QUEUED.value,
                ),
            )
            if updated.rowcount != 1:
                return None
            self._event(job_id, revision, "job.assigned")
            claimed = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert claimed is not None
            return self._row_to_job(claimed)

    def mark_running(self, job_id: str, attempt_id: str, owner: str) -> JobRecord:
        with self._transaction():
            row = self._owned_row(job_id, attempt_id, owner)
            revision = int(row["revision"]) + 1
            now = utc_now()
            self._connection.execute(
                "UPDATE jobs SET phase = ?, revision = ?, started_at = ?, updated_at = ? "
                "WHERE id = ? AND attempt_id = ? AND lease_owner = ?",
                (JobPhase.RUNNING.value, revision, now, now, job_id, attempt_id, owner),
            )
            self._event(job_id, revision, "job.running")
        return self.get(job_id)

    def renew_lease(
        self, job_id: str, attempt_id: str, owner: str, lease_seconds: int
    ) -> bool:
        with self._lock:
            updated = self._connection.execute(
                "UPDATE jobs SET lease_expires_at = ? WHERE id = ? AND attempt_id = ? "
                "AND lease_owner = ? AND phase IN (?, ?)",
                (
                    time.time() + lease_seconds,
                    job_id,
                    attempt_id,
                    owner,
                    JobPhase.ASSIGNED.value,
                    JobPhase.RUNNING.value,
                ),
            )
        return updated.rowcount == 1

    def finish(
        self,
        job_id: str,
        attempt_id: str,
        owner: str,
        *,
        phase: JobPhase,
        exit_code: Optional[int],
        reason: str,
        error: Optional[dict[str, Any]],
        stdout_path: Optional[str],
        stderr_path: Optional[str],
        stdout_bytes: int,
        stderr_bytes: int,
        output_truncated: bool,
    ) -> JobRecord:
        if not phase.terminal:
            raise ValueError("finish requires a terminal phase")
        with self._transaction():
            row = self._owned_row(job_id, attempt_id, owner)
            current_phase = JobPhase(row["phase"])
            if current_phase.terminal:
                return self._row_to_job(row)
            if current_phase not in {JobPhase.ASSIGNED, JobPhase.RUNNING}:
                raise StoreError("attempt is not in a finishable phase")
            revision = int(row["revision"]) + 1
            now = utc_now()
            conditions = json.loads(row["conditions_json"])
            conditions.append(
                {
                    "type": "Ready",
                    "status": phase == JobPhase.SUCCEEDED,
                    "reason": reason,
                    "transition_time": now,
                }
            )
            self._connection.execute(
                """
                UPDATE jobs SET phase = ?, observed_generation = generation,
                    revision = ?, completed_at = ?, updated_at = ?, exit_code = ?,
                    reason = ?, error_json = ?, stdout_path = ?, stderr_path = ?,
                    stdout_bytes = ?, stderr_bytes = ?, output_truncated = ?,
                    conditions_json = ?, lease_expires_at = NULL
                WHERE id = ? AND attempt_id = ? AND lease_owner = ?
                """,
                (
                    phase.value,
                    revision,
                    now,
                    now,
                    exit_code,
                    reason,
                    json.dumps(error, separators=(",", ":")) if error else None,
                    stdout_path,
                    stderr_path,
                    stdout_bytes,
                    stderr_bytes,
                    int(output_truncated),
                    json.dumps(conditions, separators=(",", ":")),
                    job_id,
                    attempt_id,
                    owner,
                ),
            )
            self._event(job_id, revision, f"job.{phase.value}")
            completed = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert completed is not None
            self._insert_outbox(completed)
        return self.get(job_id)

    def request_cancel(self, job_id: str, tenant_id: Optional[str] = None) -> JobRecord:
        with self._transaction():
            row = self._select_job(job_id, tenant_id)
            phase = JobPhase(row["phase"])
            if phase.terminal:
                return self._row_to_job(row)
            revision = int(row["revision"]) + 1
            generation = int(row["generation"]) + 1
            now = utc_now()
            if phase == JobPhase.QUEUED:
                next_phase = JobPhase.CANCELED
                completed_at = now
                observed = generation
                reason = "cancelled_before_start"
            else:
                next_phase = phase
                completed_at = None
                observed = int(row["observed_generation"])
                reason = row["reason"]
            spec_data = json.loads(row["spec_json"])
            spec_data["desired_state"] = "cancelled"
            self._connection.execute(
                """
                UPDATE jobs SET spec_json = ?, generation = ?, observed_generation = ?,
                    revision = ?, phase = ?, completed_at = COALESCE(?, completed_at),
                    reason = ?,
                    updated_at = ? WHERE id = ? AND revision = ?
                """,
                (
                    json.dumps(spec_data, sort_keys=True, separators=(",", ":")),
                    generation,
                    observed,
                    revision,
                    next_phase.value,
                    completed_at,
                    reason,
                    now,
                    job_id,
                    row["revision"],
                ),
            )
            self._event(job_id, revision, "job.cancel_requested")
            updated = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert updated is not None
            if next_phase.terminal:
                self._insert_outbox(updated)
        return self.get(job_id)

    def recover_interrupted(self) -> int:
        with self._transaction():
            rows = self._connection.execute(
                "SELECT * FROM jobs WHERE phase IN (?, ?)",
                (JobPhase.ASSIGNED.value, JobPhase.RUNNING.value),
            ).fetchall()
            for row in rows:
                revision = int(row["revision"]) + 1
                now = utc_now()
                self._connection.execute(
                    "UPDATE jobs SET phase = ?, revision = ?, completed_at = ?, "
                    "updated_at = ?, reason = ?, lease_expires_at = NULL WHERE id = ?",
                    (
                        JobPhase.EXECUTOR_LOST.value,
                        revision,
                        now,
                        now,
                        "service_restarted",
                        row["id"],
                    ),
                )
                self._event(str(row["id"]), revision, "job.executor_lost")
                updated = self._connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                assert updated is not None
                self._insert_outbox(updated)
            return len(rows)

    def outbox_due(self, limit: int = 100) -> tuple[OutboxRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM callback_outbox
                WHERE state = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at LIMIT ?
                """,
                (time.time(), limit),
            ).fetchall()
        return tuple(
            OutboxRecord(
                event_id=row["event_id"],
                job_id=row["job_id"],
                endpoint_id=row["endpoint_id"],
                payload=json.loads(row["payload_json"]),
                attempts=row["attempts"],
                next_attempt_at=row["next_attempt_at"],
            )
            for row in rows
        )

    def outbox_delivered(self, event_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE callback_outbox SET state = 'delivered', delivered_at = ? "
                "WHERE event_id = ?",
                (utc_now(), event_id),
            )

    def outbox_failed(
        self, event_id: str, error: str, *, next_attempt_at: float, permanent: bool
    ) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE callback_outbox SET state = ?, attempts = attempts + 1, "
                "next_attempt_at = ?, last_error = ? WHERE event_id = ?",
                (
                    "dead" if permanent else "pending",
                    next_attempt_at,
                    error[:1000],
                    event_id,
                ),
            )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            phases = {
                row["phase"]: int(row["count"])
                for row in self._connection.execute(
                    "SELECT phase, COUNT(*) AS count FROM jobs GROUP BY phase"
                ).fetchall()
            }
            pending_callbacks = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM callback_outbox WHERE state = 'pending'"
                ).fetchone()[0]
            )
            dead_callbacks = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM callback_outbox WHERE state = 'dead'"
                ).fetchone()[0]
            )
        return {
            "durability": self.durability,
            "store_epoch": self.store_epoch,
            "jobs": phases,
            "pending_callbacks": pending_callbacks,
            "dead_callbacks": dead_callbacks,
        }

    def _owned_row(self, job_id: str, attempt_id: str, owner: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM jobs WHERE id = ? AND attempt_id = ? AND lease_owner = ?",
            (job_id, attempt_id, owner),
        ).fetchone()
        if row is None:
            raise StoreError("stale or invalid attempt ownership")
        return row

    def _select_job(self, job_id: str, tenant_id: Optional[str]) -> sqlite3.Row:
        if tenant_id is None:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE id = ? AND tenant_id = ?",
                (job_id, tenant_id),
            ).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return row

    def _event(self, job_id: str, revision: int, kind: str) -> None:
        self._connection.execute(
            "INSERT INTO resource_events(job_id, revision, kind, created_at) "
            "VALUES (?, ?, ?, ?)",
            (job_id, revision, kind, utc_now()),
        )
        if self.volatile:
            self._connection.execute(
                "DELETE FROM resource_events WHERE seq IN "
                "(SELECT seq FROM resource_events ORDER BY seq DESC LIMIT -1 OFFSET ?)",
                (self._max_event_records,),
            )

    def _prune_expired(self) -> None:
        if not self.volatile:
            return
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self._job_ttl_seconds)
        ).isoformat()
        terminal = tuple(phase.value for phase in JobPhase if phase.terminal)
        placeholders = ",".join("?" for _ in terminal)
        self._connection.execute(
            f"DELETE FROM jobs WHERE phase IN ({placeholders}) AND completed_at < ?",
            (*terminal, cutoff),
        )

    def _insert_outbox(self, row: sqlite3.Row) -> None:
        spec = JobSpec.from_dict(json.loads(row["spec_json"]))
        if not spec.callback_endpoint_id:
            return
        job = self._row_to_job(row)
        payload = {
            "event_id": f"evt_{uuid4().hex}",
            "event_type": "job.completed",
            **job.to_dict(include_spec=False),
        }
        self._connection.execute(
            """
            INSERT INTO callback_outbox(
                event_id, job_id, endpoint_id, payload_json,
                next_attempt_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload["event_id"],
                job.id,
                spec.callback_endpoint_id,
                json.dumps(payload, separators=(",", ":")),
                time.time(),
                utc_now(),
            ),
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            idempotency_key=row["idempotency_key"],
            spec=JobSpec.from_dict(json.loads(row["spec_json"])),
            request_hash=row["request_hash"],
            phase=JobPhase(row["phase"]),
            generation=row["generation"],
            observed_generation=row["observed_generation"],
            revision=row["revision"],
            durability=row["durability"],
            store_epoch=row["store_epoch"],
            attempt_id=row["attempt_id"],
            lease_owner=row["lease_owner"],
            lease_epoch=row["lease_epoch"],
            lease_expires_at=row["lease_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            exit_code=row["exit_code"],
            reason=row["reason"],
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            stdout_bytes=row["stdout_bytes"],
            stderr_bytes=row["stderr_bytes"],
            output_truncated=bool(row["output_truncated"]),
            conditions=tuple(json.loads(row["conditions_json"])),
        )


def _sqlite_location(url: str, *, state_dir: Optional[Path]) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("only sqlite:/// JobStore URLs are supported")
    location = url[len(prefix) :]
    if location == ":memory:":
        return location
    path = Path(location)
    if not path.is_absolute():
        path = (state_dir or Path.cwd()) / path
    return str(path)


class _InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: Optional[int] = None

    def acquire(self) -> None:
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                windows_msvcrt: Any = msvcrt
                os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                windows_msvcrt.locking(
                    descriptor,
                    windows_msvcrt.LK_NBLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            os.close(descriptor)
            raise InstanceAlreadyRunning(
                f"another SharkRail instance owns {self.path}"
            ) from err
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                windows_msvcrt: Any = msvcrt
                os.lseek(self._descriptor, 0, os.SEEK_SET)
                windows_msvcrt.locking(
                    self._descriptor,
                    windows_msvcrt.LK_UNLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None
