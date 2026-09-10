"""Cross-platform Master process supervising a replaceable service Worker."""

from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import threading
import time
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Callable, Optional

from ..observability.telemetry import configure_logging
from ..runtime.policy import ExecutionPolicy
from ..runtime.process_identity import process_birth_identity
from ..runtime.windows import WindowsJob, duplicate_job_handle_from_process
from .config import ServiceConfig, load_config
from .http import serve_http
from .ownership import ProcessOwnershipClient
from .server import JobService


def run_worker(
    config_path: Optional[str],
    parent_pid: Optional[int] = None,
    heartbeat: Optional[Connection] = None,
    execution_policy: Optional[ExecutionPolicy] = None,
    ownership: Optional[Connection] = None,
) -> None:
    config = (
        load_config(Path(config_path), require_explicit=True)
        if config_path
        else load_config()
    )
    configure_logging(config.logging.level)
    ownership_client = (
        ProcessOwnershipClient(
            ownership,
            timeout_seconds=config.control.worker_progress_timeout_seconds,
        )
        if ownership is not None and parent_pid is not None
        else None
    )
    try:
        service = JobService(
            config,
            execution_policy=execution_policy,
            process_ownership=ownership_client,
        )
    except BaseException:
        if ownership_client is not None:
            ownership_client.close()
        raise
    if parent_pid is not None:
        _start_parent_watch(parent_pid)
    if heartbeat is not None:
        _start_worker_heartbeat(service, heartbeat)

    def stop_worker(*_args: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_worker)
    try:
        serve_http(service)
    except KeyboardInterrupt:
        pass
    finally:
        if ownership_client is not None:
            ownership_client.close()


class ControlMaster:
    """Restart a failed Worker with bounded exponential backoff."""

    def __init__(
        self,
        config: ServiceConfig,
        config_path: Optional[Path],
        *,
        worker_target: Callable[..., None] = run_worker,
        execution_policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self._worker_target = worker_target
        self._execution_policy = execution_policy
        self._stop = threading.Event()
        self._worker: Optional[BaseProcess] = None
        self._ownership_connection: Connection | None = None
        self._owned_processes: dict[str, dict[str, object]] = {}

    def stop(self, *_args: object) -> None:
        # The run loop owns the single drain deadline. Signal handlers only
        # request shutdown so they cannot accidentally start a second, shorter
        # termination sequence.
        self._stop.set()

    def run(self) -> int:
        previous_handlers = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, self.stop)
        failures: list[float] = []
        try:
            while not self._stop.is_set():
                context = multiprocessing.get_context("spawn")
                heartbeat_reader, heartbeat_writer = context.Pipe(duplex=False)
                ownership_master, ownership_worker = context.Pipe(duplex=True)
                worker = context.Process(
                    target=self._worker_target,
                    args=(
                        str(self.config_path) if self.config_path else None,
                        os.getpid(),
                        heartbeat_writer,
                        self._execution_policy,
                        ownership_worker,
                    ),
                    name="sharkrail-control-worker",
                )
                self._worker = worker
                owned_processes: dict[str, dict[str, object]] = {}
                self._ownership_connection = ownership_master
                self._owned_processes = owned_processes
                try:
                    worker.start()
                except BaseException:
                    for connection in (
                        heartbeat_reader,
                        heartbeat_writer,
                        ownership_master,
                        ownership_worker,
                    ):
                        connection.close()
                    self._ownership_connection = None
                    self._owned_processes = {}
                    raise
                heartbeat_writer.close()
                ownership_worker.close()
                last_progress = time.monotonic()
                worker_stalled = False
                while worker.is_alive():
                    _service_ownership_messages(
                        ownership_master,
                        owned_processes,
                        max_records=self.config.executor.workers,
                        worker_pid=worker.pid,
                    )
                    if self._stop.wait(0.2):
                        break
                    try:
                        while heartbeat_reader.poll():
                            _, health = heartbeat_reader.recv()
                            progress_limit = (
                                self.config.control.worker_progress_timeout_seconds
                            )
                            ready = bool(health.get("ready")) and all(
                                isinstance(health.get(name), (int, float))
                                and float(health[name]) <= progress_limit
                                for name in (
                                    "controller_progress_age_seconds",
                                    "notification_progress_age_seconds",
                                )
                            )
                            if ready:
                                last_progress = time.monotonic()
                    except (EOFError, OSError):
                        pass
                    if (
                        time.monotonic() - last_progress
                        > self.config.control.worker_progress_timeout_seconds
                    ):
                        worker_stalled = True
                        break
                if self._stop.is_set() or worker_stalled or worker.is_alive():
                    self._terminate_worker(ownership_master, owned_processes)
                else:
                    worker.join()
                    _service_ownership_messages(
                        ownership_master,
                        owned_processes,
                        max_records=self.config.executor.workers,
                        worker_pid=worker.pid,
                    )
                heartbeat_reader.close()
                # Ownership is registered synchronously and is independent of
                # health snapshots. Every worker exit drains that registry.
                self._finish_ownership_generation()
                if self._stop.is_set():
                    return 0
                now = time.monotonic()
                window = self.config.control.worker_restart_window_seconds
                failures = [stamp for stamp in failures if now - stamp <= window]
                failures.append(now)
                if len(failures) > self.config.control.worker_restart_limit:
                    return 70
                delay = min(30.0, 0.25 * (2 ** (len(failures) - 1)))
                self._stop.wait(delay)
            return 0
        finally:
            self._terminate_worker()
            self._finish_ownership_generation()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

    def _terminate_worker(
        self,
        ownership: Connection | None = None,
        owned_processes: dict[str, dict[str, object]] | None = None,
    ) -> None:
        if self._worker is None:
            return
        if ownership is None:
            ownership = self._ownership_connection
        if owned_processes is None:
            owned_processes = self._owned_processes
        if self._worker.is_alive():
            self._worker.terminate()
            deadline = (
                time.monotonic() + self.config.control.worker_drain_timeout_seconds
            )
            while self._worker.is_alive() and time.monotonic() < deadline:
                if ownership is not None and owned_processes is not None:
                    _service_ownership_messages(
                        ownership,
                        owned_processes,
                        max_records=self.config.executor.workers,
                        worker_pid=self._worker.pid,
                    )
                self._worker.join(timeout=min(0.05, deadline - time.monotonic()))
        if self._worker.is_alive():
            self._worker.kill()
            self._worker.join(timeout=2)

    def _finish_ownership_generation(self) -> None:
        connection = self._ownership_connection
        if connection is not None:
            _service_ownership_messages(
                connection,
                self._owned_processes,
                max_records=self.config.executor.workers,
                worker_pid=self._worker.pid if self._worker is not None else None,
            )
            connection.close()
        _cleanup_orphan_processes(list(self._owned_processes.values()))
        self._ownership_connection = None
        self._owned_processes = {}


def _start_parent_watch(parent_pid: int) -> None:
    def watch() -> None:
        while True:
            time.sleep(1)
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=watch, name="sharkrail-parent-watch", daemon=True).start()


def _start_worker_heartbeat(service: JobService, connection: Connection) -> None:
    def beat() -> None:
        while True:
            try:
                connection.send((time.monotonic(), service.health()))
            except (BrokenPipeError, EOFError, OSError):
                connection.close()
                return
            time.sleep(min(1.0, service.config.control.worker_heartbeat_seconds))

    threading.Thread(
        target=beat, name="sharkrail-worker-heartbeat", daemon=True
    ).start()


def _service_ownership_messages(
    connection: Connection,
    owned: dict[str, dict[str, object]],
    *,
    max_records: int,
    worker_pid: int | None = None,
) -> None:
    """Apply all pending register/unregister requests and acknowledge each one."""

    while True:
        try:
            if not connection.poll():
                return
            message: Any = connection.recv()
        except (BrokenPipeError, EOFError, OSError):
            return
        request_id = "invalid"
        accepted = False
        detail: str | None = None
        try:
            if not isinstance(message, tuple) or len(message) != 3:
                raise ValueError("invalid ownership request")
            operation, request_id, payload = message
            if not isinstance(request_id, str):
                request_id = "invalid"
                raise TypeError("invalid ownership request ID")
            if operation == "register":
                if not isinstance(payload, dict):
                    raise ValueError("ownership registration has no record")
                ownership_id = payload.get("ownership_id")
                if not isinstance(ownership_id, str) or not ownership_id:
                    raise ValueError("ownership registration has no ID")
                existing = owned.get(ownership_id)
                if existing is not None:
                    accepted = True
                else:
                    if len(owned) >= max_records:
                        raise ValueError("ownership registry capacity is full")
                    if not _registered_process_is_current(payload):
                        raise ValueError("registered process identity is not current")
                    record = payload
                    if os.name == "nt":
                        source_pid = payload.get("source_pid")
                        source_handle = payload.get("source_job_handle")
                        if (
                            payload.get("process_tree") != "job_object"
                            or not isinstance(source_pid, int)
                            or not isinstance(source_handle, int)
                            or (worker_pid is not None and source_pid != worker_pid)
                        ):
                            raise ValueError(
                                "Windows ownership requires the current Worker's "
                                "Job handle"
                            )
                        master_handle = duplicate_job_handle_from_process(
                            source_pid, source_handle
                        )
                        record = {**payload, "job_handle": master_handle}
                    owned[ownership_id] = record
                    accepted = True
            elif operation == "unregister":
                ownership_id = (
                    payload.get("ownership_id") if isinstance(payload, dict) else None
                )
                if not isinstance(ownership_id, str) or not ownership_id:
                    raise ValueError("ownership unregister has no ID")
                removed_record = owned.pop(ownership_id, None)
                if removed_record is not None:
                    _release_master_job(removed_record, terminate=False)
                accepted = True
            else:
                raise ValueError(f"unknown ownership operation: {operation}")
        except (OSError, RuntimeError, TypeError, ValueError) as err:
            detail = str(err)
            if (
                isinstance(message, tuple)
                and len(message) == 3
                and message[0] == "register"
                and isinstance(message[2], dict)
            ):
                _release_master_job(message[2], terminate=False)
        try:
            connection.send(("ack", request_id, accepted, detail))
        except (BrokenPipeError, EOFError, OSError):
            return


def _registered_process_is_current(process: dict[str, object]) -> bool:
    pid = process.get("pid")
    birth_identity = process.get("birth_identity")
    if (
        not isinstance(pid, int)
        or pid <= 0
        or not isinstance(birth_identity, str)
        or process_birth_identity(pid) != birth_identity
    ):
        return False
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        return True
    pgid = process.get("pgid")
    if process.get("process_tree") != "process_group" or not isinstance(pgid, int):
        return False
    try:
        return pgid > 0 and os.getpgid(pid) == pgid
    except OSError:
        return False


def _owned_process_group_is_current(process: dict[str, object]) -> bool:
    pid = process.get("pid")
    pgid = process.get("pgid")
    birth_identity = process.get("birth_identity")
    if (
        not isinstance(pid, int)
        or not isinstance(pgid, int)
        or pgid <= 0
        or not isinstance(birth_identity, str)
    ):
        return False
    current_identity = process_birth_identity(pid)
    if current_identity is not None:
        return current_identity == birth_identity
    try:
        # A descendant-only group keeps its PGID allocated after the original
        # leader exits. An active synchronous registration proves ownership;
        # killpg(0) distinguishes that state from an already-empty group.
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def _release_master_job(process: dict[str, object], *, terminate: bool) -> None:
    if os.name != "nt":
        return
    raw_handle = process.get("job_handle")
    if not isinstance(raw_handle, int):
        return
    job = WindowsJob.from_handle(raw_handle)
    try:
        if terminate:
            job.terminate()
            job.wait_empty(2.0)
    finally:
        job.close()
        process["job_handle"] = None


def _cleanup_orphan_processes(processes: list[dict[str, object]]) -> None:
    for process in processes:
        pid = process.get("pid")
        mechanism = process.get("process_tree")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                if isinstance(process.get("job_handle"), int):
                    _release_master_job(process, terminate=True)
                    continue
                if mechanism not in {"job_object", "taskkill_fallback"}:
                    continue
                if not _registered_process_is_current(process):
                    continue
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            else:
                pgid = process.get("pgid")
                if (
                    mechanism != "process_group"
                    or not isinstance(pgid, int)
                    or pgid <= 0
                    or not _owned_process_group_is_current(process)
                ):
                    continue
                os.killpg(pgid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
