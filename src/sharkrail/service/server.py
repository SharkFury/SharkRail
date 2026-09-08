"""State-driven asynchronous Job controller."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import json
import logging
import socket
import ssl
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit
from uuid import uuid4

from ..core.errors import SharkRailError
from ..core.models import CommandMode, CommandSpec
from ..runtime.executor import CompletionReason
from ..runtime.sessions import SessionManager
from .config import (
    CallbackEndpoint,
    ServiceConfig,
    state_directory,
    validate_callback_destination,
    validate_service_config,
)
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
        config = validate_service_config(config, resolve_callbacks=False)
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
        self.store.set_output_cleaner(self.output.delete_job)
        self._executor = ThreadPoolExecutor(
            max_workers=config.executor.workers,
            thread_name_prefix="sharkrail-executor",
        )
        self._notification_executor = ThreadPoolExecutor(
            max_workers=config.notifications.max_concurrent_deliveries,
            thread_name_prefix="sharkrail-callback",
        )
        self._notification_slots = threading.BoundedSemaphore(
            config.notifications.max_concurrent_deliveries
        )
        self._notification_state_lock = threading.Lock()
        self._notification_started: dict[str, float] = {}
        self._callback_resolution_slots = threading.BoundedSemaphore(
            config.notifications.max_concurrent_deliveries
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
            self._notification_executor.shutdown(wait=True, cancel_futures=False)
            self._executor.shutdown(wait=True, cancel_futures=False)
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
        self._notification_executor.shutdown(wait=True, cancel_futures=False)
        self._executor.shutdown(wait=True, cancel_futures=False)
        self.output.close()
        self.store.close()
        self._started = False
        self._closed = True

    def submit(
        self, tenant_id: str, idempotency_key: str, payload: dict[str, Any]
    ) -> tuple[JobRecord, bool]:
        spec = JobSpec.from_dict(payload)
        if spec.callback_endpoint_id:
            endpoint = self.config.callback_endpoints.get(spec.callback_endpoint_id)
            if endpoint is None or endpoint.tenant_id != tenant_id:
                raise ValueError("unknown callback endpoint_id for tenant")
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
        with self._active_lock:
            requested = self.store.request_cancel(job_id, tenant_id)
            active = self._active.get(job_id)
        if active is not None and requested.spec.desired_state == "cancelled":
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
        with self._notification_state_lock:
            notification_inflight = len(self._notification_started)
            oldest_notification_age = (
                now - min(self._notification_started.values())
                if self._notification_started
                else None
            )
        notification_stalled = (
            oldest_notification_age is not None
            and oldest_notification_age
            > self.config.notifications.request_timeout_seconds + 5.0
        )
        degraded = self.config.durability == "volatile"
        return {
            "live": not self._stop.is_set(),
            "ready": (
                self._started
                and not self._stop.is_set()
                and all(thread.is_alive() for thread in self._controllers)
                and all(thread.is_alive() for thread in self._notification_workers)
                and not notification_stalled
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
            "notification_inflight": notification_inflight,
            "notification_stalled": notification_stalled,
            "oldest_notification_age_seconds": (
                round(oldest_notification_age, 3)
                if oldest_notification_age is not None
                else None
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
                current = self.store.get(claimed.id)
                self.store.mark_running(claimed.id, claimed.attempt_id, self.worker_id)
                cancel_requested = current.spec.desired_state == "cancelled"
            if self._stop.is_set() or cancel_requested:
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
            capacity = 0
            for _ in range(self.config.notifications.max_concurrent_deliveries):
                if not self._notification_slots.acquire(blocking=False):
                    break
                capacity += 1
            if capacity == 0:
                with self._notification_state_lock:
                    oldest = (
                        min(self._notification_started.values())
                        if self._notification_started
                        else None
                    )
                if (
                    oldest is None
                    or time.monotonic() - oldest
                    <= self.config.notifications.request_timeout_seconds + 5.0
                ):
                    self._last_notification_progress = time.monotonic()
                continue
            self._last_notification_progress = time.monotonic()
            try:
                records = self.store.claim_outbox_due(
                    capacity,
                    lease_seconds=(
                        self.config.notifications.request_timeout_seconds
                        + self.config.control.worker_progress_timeout_seconds
                        + 10.0
                    ),
                )
            except Exception:
                for _ in range(capacity):
                    self._notification_slots.release()
                self._notification_errors += 1
                LOGGER.exception("notification reconciliation failed")
                self._stop.wait(min(5.0, 0.1 * self._notification_errors))
                continue
            for _ in range(capacity - len(records)):
                self._notification_slots.release()
            for record in records:
                try:
                    self._notification_executor.submit(self._deliver_boundary, record)
                except Exception as err:
                    self._notification_slots.release()
                    self._notification_errors += 1
                    LOGGER.exception("notification scheduling failed")
                    self._retry_delivery(record, str(err), permanent=False)

    def _deliver_boundary(self, record: OutboxRecord) -> None:
        tracking_id = record.delivery_attempt_id or record.event_id
        with self._notification_state_lock:
            self._notification_started[tracking_id] = time.monotonic()
        try:
            self._deliver(record)
        except Exception as err:
            self._notification_errors += 1
            LOGGER.exception("notification delivery boundary failed")
            self._retry_delivery(record, str(err), permanent=False)
        finally:
            with self._notification_state_lock:
                self._notification_started.pop(tracking_id, None)
            self._last_notification_progress = time.monotonic()
            self._notification_slots.release()

    def _resolve_callback_destination(
        self, endpoint: CallbackEndpoint, *, timeout: float
    ) -> tuple[str, int, tuple[str, ...]]:
        if timeout <= 0 or not self._callback_resolution_slots.acquire(blocking=False):
            raise TimeoutError("callback destination resolution deadline exceeded")
        completed = threading.Event()
        results: list[tuple[str, int, tuple[str, ...]]] = []
        errors: list[Exception] = []

        def resolve() -> None:
            try:
                results.append(validate_callback_destination(endpoint))
            except Exception as err:  # noqa: BLE001 - resolver thread boundary
                errors.append(err)
            finally:
                self._callback_resolution_slots.release()
                completed.set()

        resolver = threading.Thread(
            target=resolve,
            name="sharkrail-callback-resolver",
            daemon=True,
        )
        try:
            resolver.start()
        except Exception:
            self._callback_resolution_slots.release()
            raise
        if not completed.wait(timeout):
            raise TimeoutError("callback destination resolution deadline exceeded")
        if errors:
            raise errors[0]
        if not results:
            raise RuntimeError("callback destination resolution failed")
        return results[0]

    def _deliver(self, record: OutboxRecord) -> None:
        endpoint = self.config.callback_endpoints.get(record.endpoint_id)
        if endpoint is None or endpoint.tenant_id != record.tenant_id:
            self.store.outbox_failed(
                record.event_id,
                record.delivery_attempt_id,
                "callback endpoint no longer belongs to Job tenant",
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
        deadline = time.monotonic() + self.config.notifications.request_timeout_seconds
        try:
            hostname, port, addresses = self._resolve_callback_destination(
                endpoint, timeout=deadline - time.monotonic()
            )
            parsed = urlsplit(endpoint.url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("callback request deadline exceeded")
            status = _post_callback(
                scheme=parsed.scheme,
                hostname=hostname,
                port=port,
                addresses=addresses,
                target=(parsed.path or "/")
                + (f"?{parsed.query}" if parsed.query else ""),
                body=body,
                headers=headers,
                timeout=remaining,
            )
            if 200 <= status < 300:
                self.store.outbox_delivered(record.event_id, record.delivery_attempt_id)
                return
            raise RuntimeError(f"callback returned HTTP {status}")
        except _CallbackHTTPError as err:
            permanent = 400 <= err.status < 500 and err.status != 429
            self._retry_delivery(record, str(err), permanent=permanent)
        except (OSError, RuntimeError, http.client.HTTPException) as err:
            self._retry_delivery(record, str(err), permanent=False)

    def _retry_delivery(
        self, record: OutboxRecord, error: str, *, permanent: bool
    ) -> None:
        attempts = record.attempts + 1
        exhausted = attempts >= self.config.notifications.max_attempts
        delay = min(3600.0, 5.0 * (2 ** min(attempts, 9)))
        self.store.outbox_failed(
            record.event_id,
            record.delivery_attempt_id,
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


class _CallbackHTTPError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"callback returned HTTP {status}")
        self.status = status


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        address: str,
        port: int,
        *,
        server_hostname: str,
        timeout: float,
        deadline: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(server_hostname, port, timeout=timeout, context=context)
        self._approved_address = address
        self._callback_context = context
        self._callback_deadline = deadline

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._approved_address, self.port),
            self.timeout,
        )
        self.sock = raw_socket
        try:
            tls_socket = self._callback_context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
                do_handshake_on_connect=False,
            )
        except BaseException:
            raw_socket.close()
            raise
        self.sock = tls_socket
        remaining = self._callback_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("callback TLS handshake deadline exceeded")
        tls_socket.settimeout(remaining)
        tls_socket.do_handshake()


def _post_callback(
    *,
    scheme: str,
    hostname: str,
    port: int,
    addresses: tuple[str, ...],
    target: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> int:
    encoded_hostname = hostname.encode("idna").decode("ascii")
    default_port = 443 if scheme == "https" else 80
    host_value = (
        f"[{encoded_hostname}]" if ":" in encoded_hostname else encoded_hostname
    )
    if port != default_port:
        host_value = f"{host_value}:{port}"
    request_headers = {**headers, "Host": host_value}
    last_error: Optional[BaseException] = None
    deadline = time.monotonic() + timeout
    https_context = ssl.create_default_context() if scheme == "https" else None
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = TimeoutError("callback request deadline exceeded")
            break
        if scheme == "https":
            assert https_context is not None
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                address,
                port,
                server_hostname=encoded_hostname,
                timeout=remaining,
                deadline=deadline,
                context=https_context,
            )
        else:
            connection = http.client.HTTPConnection(address, port, timeout=remaining)
        timed_out = threading.Event()

        def abort_connection(
            connection_to_abort: http.client.HTTPConnection = connection,
            timed_out_event: threading.Event = timed_out,
        ) -> None:
            timed_out_event.set()
            sock = connection_to_abort.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection_to_abort.close()

        deadline_timer = threading.Timer(remaining, abort_connection)
        deadline_timer.daemon = True
        deadline_timer.start()
        try:
            connection.connect()
            if timed_out.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("callback request deadline exceeded")
            connection.request("POST", target, body=body, headers=request_headers)
            response = connection.getresponse()
            status = response.status
            if not 200 <= status < 300:
                raise _CallbackHTTPError(status)
            return status
        except _CallbackHTTPError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as err:
            if timed_out.is_set() or time.monotonic() >= deadline:
                last_error = TimeoutError("callback request deadline exceeded")
                break
            last_error = err
        finally:
            deadline_timer.cancel()
            connection.close()
    if last_error is None:
        raise OSError("callback endpoint resolved to no approved addresses")
    raise OSError(f"callback connection failed: {last_error}") from last_error
