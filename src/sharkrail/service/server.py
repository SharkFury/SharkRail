"""State-driven asynchronous Job controller."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from ..core.errors import SharkRailError
from ..core.models import CommandMode, CommandSpec
from ..runtime.executor import CompletionReason
from ..runtime.sessions import SessionManager
from .config import ServiceConfig, state_directory
from .models import JobPhase, JobRecord, JobSpec, OutboxRecord
from .output import FileOutputStore
from .store import SqliteJobStore, StoreError

LOGGER = logging.getLogger("sharkrail.runtime.service")


@dataclass
class _ActiveRun:
    loop: asyncio.AbstractEventLoop
    manager: SessionManager
    session_id: str
    attempt_id: str


class JobService:
    """Own the JobStore, reconcilers, execution pool, and callback dispatcher."""

    def __init__(
        self, config: ServiceConfig, *, state_dir: Optional[Path] = None
    ) -> None:
        self.config = config
        self.worker_id = f"worker_{uuid4().hex}"
        resolved_state_dir = state_dir or state_directory()
        self.store = SqliteJobStore(
            config.job_store.url,
            max_jobs=config.volatile_store.max_jobs
            if config.durability == "volatile"
            else 2**31 - 1,
            max_metadata_bytes=config.volatile_store.max_metadata_bytes,
            max_event_records=config.volatile_store.max_event_records,
            job_ttl_seconds=config.volatile_store.job_ttl_seconds,
            max_queued_jobs=config.admission.max_queued_jobs,
            max_queued_jobs_per_tenant=config.admission.max_queued_jobs_per_tenant,
            state_dir=resolved_state_dir,
        )
        self.output = FileOutputStore(
            config.output_store.url,
            state_dir=resolved_state_dir,
            volatile=config.durability == "volatile",
            max_total_bytes=(
                config.volatile_store.max_temporary_output_bytes
                if config.durability == "volatile"
                else config.output_store.max_total_bytes
            ),
        )
        self._executor = ThreadPoolExecutor(
            max_workers=config.executor.workers,
            thread_name_prefix="sharkrail-executor",
        )
        self._active: dict[str, _ActiveRun] = {}
        self._inflight: set[str] = set()
        self._active_lock = threading.RLock()
        self._schedule_lock = threading.Lock()
        self._stop = threading.Event()
        self._controllers = tuple(
            threading.Thread(
                target=self._controller_loop,
                name=f"sharkrail-controller-{index}",
                daemon=True,
            )
            for index in range(config.control.controller_workers)
        )
        self._notification_workers = tuple(
            threading.Thread(
                target=self._notification_loop,
                name=f"sharkrail-notifications-{index}",
                daemon=True,
            )
            for index in range(config.control.notification_workers)
        )
        self._notification_delivery_lock = threading.Lock()
        self._started = False
        self._closed = False
        self._last_controller_progress = time.monotonic()
        self._last_notification_progress = time.monotonic()
        self._last_lease_renew = 0.0
        self._controller_errors = 0
        self._notification_errors = 0

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("JobService is closed")
        if self._started:
            return
        recovered = self.store.recover_interrupted()
        if recovered:
            LOGGER.warning("marked %d interrupted Jobs as executor_lost", recovered)
        if self.config.durability == "volatile":
            LOGGER.warning(
                "DEGRADED_VOLATILE_STORE: Job state and callbacks are lost on restart"
            )
        self._started = True
        for thread in (*self._controllers, *self._notification_workers):
            thread.start()

    def close(self) -> None:
        if self._closed:
            return
        if not self._started:
            self.output.close()
            self.store.close()
            self._closed = True
            return
        self._stop.set()
        with self._active_lock:
            active = tuple(self._active.values())
        for run in active:
            try:
                asyncio.run_coroutine_threadsafe(
                    run.manager.cancel(run.session_id), run.loop
                ).result(timeout=self.config.control.worker_drain_timeout_seconds)
            except Exception:
                LOGGER.exception("failed to cancel session during service shutdown")
        for thread in (*self._controllers, *self._notification_workers):
            thread.join(timeout=self.config.control.worker_drain_timeout_seconds)
        self._executor.shutdown(wait=True, cancel_futures=False)
        self.output.close()
        self.store.close()
        self._started = False
        self._closed = True

    def submit(
        self, tenant_id: str, idempotency_key: str, payload: dict[str, Any]
    ) -> tuple[JobRecord, bool]:
        spec = JobSpec.from_dict(payload)
        if (
            spec.callback_endpoint_id
            and spec.callback_endpoint_id not in self.config.callback_endpoints
        ):
            raise ValueError("unknown callback endpoint_id")
        return self.store.submit(tenant_id, idempotency_key, spec)

    def get(self, job_id: str, tenant_id: Optional[str] = None) -> JobRecord:
        return self.store.get(job_id, tenant_id)

    def result(self, job_id: str, tenant_id: Optional[str] = None) -> dict[str, Any]:
        job = self.get(job_id, tenant_id)
        if not job.phase.terminal:
            raise StoreError("job has not reached a terminal state")
        return job.to_dict(include_spec=False)

    def read_output(
        self, job_id: str, stream: str, tenant_id: Optional[str] = None
    ) -> bytes:
        job = self.get(job_id, tenant_id)
        if stream == "stdout":
            return self.output.read(job.stdout_path)
        if stream == "stderr":
            return self.output.read(job.stderr_path)
        raise ValueError("stream must be stdout or stderr")

    def cancel(self, job_id: str, tenant_id: Optional[str] = None) -> JobRecord:
        self.store.request_cancel(job_id, tenant_id)
        with self._active_lock:
            active = self._active.get(job_id)
        if active is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    active.manager.cancel(active.session_id), active.loop
                ).result(timeout=5)
            except Exception:
                LOGGER.exception("failed to signal cancellation for Job %s", job_id)
        return self.get(job_id, tenant_id)

    def health(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._active_lock:
            active = len(self._active)
            active_processes: list[dict[str, object]] = []
            for job_id, run in self._active.items():
                try:
                    inspected = run.manager.inspect(run.session_id)
                except SharkRailError:
                    continue
                active_processes.append(
                    {
                        "job_id": job_id,
                        "pid": inspected["pid"],
                        "process_tree": inspected["process_tree"],
                    }
                )
        degraded = self.config.durability == "volatile"
        return {
            "live": not self._stop.is_set(),
            "ready": (
                self._started
                and not self._stop.is_set()
                and all(thread.is_alive() for thread in self._controllers)
                and all(thread.is_alive() for thread in self._notification_workers)
            ),
            "degraded": degraded,
            "reason": "DEGRADED_VOLATILE_STORE" if degraded else None,
            "worker_id": self.worker_id,
            "active_jobs": active,
            "active_processes": active_processes,
            "controller_progress_age_seconds": round(
                now - self._last_controller_progress, 3
            ),
            "notification_progress_age_seconds": round(
                now - self._last_notification_progress, 3
            ),
            "controller_errors": self._controller_errors,
            "notification_errors": self._notification_errors,
            "store": self.store.stats(),
        }

    def _controller_loop(self) -> None:
        while not self._stop.wait(0.05):
            try:
                self._last_controller_progress = time.monotonic()
                with self._schedule_lock:
                    with self._active_lock:
                        capacity = self.config.executor.workers - len(self._inflight)
                    for _ in range(max(0, capacity)):
                        job = self.store.claim_next(
                            self.worker_id, self.config.executor.lease_seconds
                        )
                        if job is None:
                            break
                        with self._active_lock:
                            self._inflight.add(job.id)
                        future = self._executor.submit(self._execute_job, job)
                        future.add_done_callback(partial(self._execution_done, job.id))
                if (
                    time.monotonic() - self._last_lease_renew
                    >= self.config.executor.heartbeat_seconds
                ):
                    self._renew_active_leases()
                    self._last_lease_renew = time.monotonic()
            except Exception:
                self._controller_errors += 1
                LOGGER.exception("controller reconciliation failed")
                self._stop.wait(min(5.0, 0.1 * self._controller_errors))

    def _execution_done(self, job_id: str, future: Future[None]) -> None:
        with self._active_lock:
            self._active.pop(job_id, None)
            self._inflight.discard(job_id)
        try:
            future.result()
        except Exception:
            LOGGER.exception("Job execution worker failed for %s", job_id)

    def _renew_active_leases(self) -> None:
        with self._active_lock:
            active = tuple((job_id, run) for job_id, run in self._active.items())
        for job_id, run in active:
            self.store.renew_lease(
                job_id,
                run.attempt_id,
                self.worker_id,
                self.config.executor.lease_seconds,
            )

    def _execute_job(self, claimed: JobRecord) -> None:
        assert claimed.attempt_id is not None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager = SessionManager(default_max_output_bytes=claimed.spec.max_output_bytes)
        registered = False
        try:
            if self._stop.is_set():
                self.store.finish(
                    claimed.id,
                    claimed.attempt_id,
                    self.worker_id,
                    phase=JobPhase.EXECUTOR_LOST,
                    exit_code=None,
                    reason="service_shutdown_before_start",
                    error=None,
                    stdout_path=None,
                    stderr_path=None,
                    stdout_bytes=0,
                    stderr_bytes=0,
                    output_truncated=False,
                )
                return
            current = self.store.get(claimed.id)
            if current.spec.desired_state == "cancelled":
                self.store.finish(
                    claimed.id,
                    claimed.attempt_id,
                    self.worker_id,
                    phase=JobPhase.CANCELED,
                    exit_code=None,
                    reason="cancelled_before_start",
                    error=None,
                    stdout_path=None,
                    stderr_path=None,
                    stdout_bytes=0,
                    stderr_bytes=0,
                    output_truncated=False,
                )
                return
            spec = CommandSpec(
                executable=claimed.spec.command[0],
                argv=claimed.spec.command[1:],
                cwd=claimed.spec.cwd,
                env=claimed.spec.env,
                mode=CommandMode.PIPE,
            )
            session = loop.run_until_complete(
                manager.start(
                    spec,
                    timeout_ms=_seconds_to_ms(claimed.spec.timeout_seconds),
                    idle_timeout_ms=_seconds_to_ms(claimed.spec.idle_timeout_seconds),
                    max_output_bytes=claimed.spec.max_output_bytes,
                    request_id=claimed.id,
                )
            )
            with self._active_lock:
                self._active[claimed.id] = _ActiveRun(
                    loop=loop,
                    manager=manager,
                    session_id=session.id,
                    attempt_id=claimed.attempt_id,
                )
            registered = True
            self.store.mark_running(claimed.id, claimed.attempt_id, self.worker_id)
            if self._stop.is_set():
                loop.run_until_complete(manager.cancel(session.id))
            result = loop.run_until_complete(manager.wait(session.id))
            if result is None:
                raise RuntimeError("session ended without a result")
            stdout_path = self.output.write(claimed.id, "stdout", result.stdout_bytes)
            stderr_path = self.output.write(claimed.id, "stderr", result.stderr_bytes)
            phase = _phase_for_result(result.reason, result.exit_code)
            self.store.finish(
                claimed.id,
                claimed.attempt_id,
                self.worker_id,
                phase=phase,
                exit_code=result.exit_code,
                reason=result.reason.value,
                error=result.error.to_dict() if result.error else None,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                stdout_bytes=len(result.stdout_bytes),
                stderr_bytes=len(result.stderr_bytes),
                output_truncated=result.output_truncated,
            )
            loop.run_until_complete(manager.dispose(session.id))
        except Exception as err:
            LOGGER.exception("Job %s failed in execution boundary", claimed.id)
            try:
                self.store.finish(
                    claimed.id,
                    claimed.attempt_id,
                    self.worker_id,
                    phase=JobPhase.FAILED,
                    exit_code=1,
                    reason="executor_error",
                    error={"message": str(err), "type": type(err).__name__},
                    stdout_path=None,
                    stderr_path=None,
                    stdout_bytes=0,
                    stderr_bytes=0,
                    output_truncated=False,
                )
            except StoreError:
                LOGGER.exception("could not persist Job failure for %s", claimed.id)
        finally:
            if registered:
                with self._active_lock:
                    self._active.pop(claimed.id, None)
            try:
                loop.run_until_complete(manager.shutdown())
            except Exception:
                LOGGER.exception("session cleanup failed for Job %s", claimed.id)
            loop.close()

    def _notification_loop(self) -> None:
        while not self._stop.wait(0.1):
            self._last_notification_progress = time.monotonic()
            with self._notification_delivery_lock:
                try:
                    records = self.store.outbox_due(
                        self.config.notifications.max_concurrent_deliveries
                    )
                except Exception:
                    self._notification_errors += 1
                    LOGGER.exception("notification reconciliation failed")
                    self._stop.wait(min(5.0, 0.1 * self._notification_errors))
                    continue
                for record in records:
                    try:
                        self._deliver(record)
                    except Exception as err:
                        self._notification_errors += 1
                        LOGGER.exception("notification delivery boundary failed")
                        self.store.outbox_failed(
                            record.event_id,
                            str(err),
                            next_attempt_at=time.time() + 30,
                            permanent=False,
                        )

    def _deliver(self, record: OutboxRecord) -> None:
        endpoint = self.config.callback_endpoints.get(record.endpoint_id)
        if endpoint is None:
            self.store.outbox_failed(
                record.event_id,
                "callback endpoint no longer exists",
                next_attempt_at=time.time(),
                permanent=True,
            )
            return
        body = json.dumps(record.payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-SharkRail-Event-ID": record.event_id,
            "X-SharkRail-Timestamp": timestamp,
        }
        secret = endpoint.resolved_secret()
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"),
                timestamp.encode("ascii") + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-SharkRail-Signature"] = f"sha256={signature}"
        request = urllib.request.Request(endpoint.url, body, headers, method="POST")
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.notifications.request_timeout_seconds
            ) as response:
                status = response.status
            if 200 <= status < 300:
                self.store.outbox_delivered(record.event_id)
                return
            raise RuntimeError(f"callback returned HTTP {status}")
        except urllib.error.HTTPError as err:
            permanent = 400 <= err.code < 500 and err.code != 429
            self._retry_delivery(record, f"HTTP {err.code}", permanent=permanent)
        except (OSError, RuntimeError) as err:
            self._retry_delivery(record, str(err), permanent=False)

    def _retry_delivery(
        self, record: OutboxRecord, error: str, *, permanent: bool
    ) -> None:
        attempts = record.attempts + 1
        exhausted = attempts >= self.config.notifications.max_attempts
        delay = min(3600.0, 5.0 * (2 ** min(attempts, 9)))
        self.store.outbox_failed(
            record.event_id,
            error,
            next_attempt_at=time.time() + delay,
            permanent=permanent or exhausted,
        )


def _seconds_to_ms(value: Optional[float]) -> Optional[int]:
    return None if value is None else int(value * 1000)


def _phase_for_result(reason: CompletionReason, exit_code: int) -> JobPhase:
    if reason == CompletionReason.SUCCESS and exit_code == 0:
        return JobPhase.SUCCEEDED
    if reason in {CompletionReason.TIMEOUT, CompletionReason.IDLE_TIMEOUT}:
        return JobPhase.TIMED_OUT
    if reason == CompletionReason.CANCELLED:
        return JobPhase.CANCELED
    return JobPhase.FAILED
