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
from typing import Callable, Optional

from ..observability.telemetry import configure_logging
from ..runtime.policy import ExecutionPolicy
from .config import ServiceConfig, load_config
from .http import serve_http
from .server import JobService


def run_worker(
    config_path: Optional[str],
    parent_pid: Optional[int] = None,
    heartbeat: Optional[Connection] = None,
    execution_policy: Optional[ExecutionPolicy] = None,
) -> None:
    config = (
        load_config(Path(config_path), require_explicit=True)
        if config_path
        else load_config()
    )
    configure_logging(config.logging.level)
    service = JobService(config, execution_policy=execution_policy)
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


class ControlMaster:
    """Restart a failed Worker with bounded exponential backoff."""

    def __init__(
        self,
        config: ServiceConfig,
        config_path: Optional[Path],
        *,
        worker_target: Callable[
            [
                Optional[str],
                Optional[int],
                Optional[Connection],
                Optional[ExecutionPolicy],
            ],
            None,
        ] = run_worker,
        execution_policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self._worker_target = worker_target
        self._execution_policy = execution_policy
        self._stop = threading.Event()
        self._worker: Optional[BaseProcess] = None

    def stop(self, *_args: object) -> None:
        self._stop.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.terminate()

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
                worker = context.Process(
                    target=self._worker_target,
                    args=(
                        str(self.config_path) if self.config_path else None,
                        os.getpid(),
                        heartbeat_writer,
                        self._execution_policy,
                    ),
                    name="sharkrail-control-worker",
                )
                self._worker = worker
                worker.start()
                heartbeat_writer.close()
                last_progress = time.monotonic()
                active_processes: list[dict[str, object]] = []
                while worker.is_alive() and not self._stop.wait(0.2):
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
                            reported = health.get("active_processes", [])
                            if isinstance(reported, list):
                                active_processes = reported
                            if ready:
                                last_progress = time.monotonic()
                    except (EOFError, OSError):
                        pass
                    if (
                        time.monotonic() - last_progress
                        > self.config.control.worker_progress_timeout_seconds
                    ):
                        worker.terminate()
                        break
                heartbeat_reader.close()
                worker.join(timeout=1)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=2)
                if not self._stop.is_set() and worker.exitcode not in {0, None}:
                    _cleanup_orphan_processes(active_processes)
                if self._stop.is_set():
                    self._terminate_worker()
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
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

    def _terminate_worker(self) -> None:
        if self._worker is None:
            return
        if self._worker.is_alive():
            self._worker.terminate()
            self._worker.join(timeout=self.config.control.worker_drain_timeout_seconds)
        if self._worker.is_alive():
            self._worker.kill()
            self._worker.join(timeout=2)


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


def _cleanup_orphan_processes(processes: list[dict[str, object]]) -> None:
    for process in processes:
        pid = process.get("pid")
        mechanism = process.get("process_tree")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                if mechanism not in {"job_object", "taskkill_fallback"}:
                    continue
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            else:
                if mechanism != "process_group" or os.getpgid(pid) != pid:
                    continue
                os.killpg(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
