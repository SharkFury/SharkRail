import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sharkrail.service.config import (
    CallbackEndpoint,
    ControlSettings,
    ExecutorSettings,
    JobStoreSettings,
    NotificationSettings,
    OutputStoreSettings,
    ServerSettings,
    ServiceConfig,
)
from sharkrail.service.http import JobHTTPServer
from sharkrail.service.master import ControlMaster
from sharkrail.service.models import JobPhase
from sharkrail.service.server import JobService


def _exiting_worker(_config_path, _parent_pid, heartbeat):
    if heartbeat is not None:
        heartbeat.send((time.monotonic(), {"ready": True, "active_processes": []}))
        heartbeat.close()


def _config(tmp_path: Path, **changes):
    values = {
        "job_store": JobStoreSettings(url="sqlite:///:memory:"),
        "output_store": OutputStoreSettings(url="file://./output"),
        "executor": ExecutorSettings(workers=2, heartbeat_seconds=1, lease_seconds=10),
    }
    values.update(changes)
    return ServiceConfig(**values)


def _terminal(service: JobService, job_id: str, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get(job_id)
        if job.phase.terminal:
            return job
        time.sleep(0.02)
    raise AssertionError("Job did not reach a terminal state")


def test_service_executes_and_retains_bounded_output(tmp_path):
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    service.start()
    try:
        job, created = service.submit(
            "tenant",
            "key",
            {"command": [sys.executable, "-c", "print('hello')"]},
        )
        result = _terminal(service, job.id)
        assert created is True
        assert result.phase == JobPhase.SUCCEEDED
        assert service.read_output(job.id, "stdout") == b"hello\n"
        assert service.health()["reason"] == "DEGRADED_VOLATILE_STORE"
    finally:
        service.close()


def test_service_timeout_and_cancel(tmp_path):
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    service.start()
    try:
        timed, _ = service.submit(
            "tenant",
            "timeout",
            {
                "command": [sys.executable, "-c", "import time; time.sleep(5)"],
                "timeout_seconds": 0.05,
            },
        )
        assert _terminal(service, timed.id).phase == JobPhase.TIMED_OUT

        running, _ = service.submit(
            "tenant",
            "cancel",
            {"command": [sys.executable, "-c", "import time; time.sleep(5)"]},
        )
        deadline = time.monotonic() + 5
        while service.get(running.id).phase != JobPhase.RUNNING:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        service.cancel(running.id, "tenant")
        assert _terminal(service, running.id).phase == JobPhase.CANCELED
    finally:
        service.close()


def test_http_submit_idempotency_result_and_output(tmp_path):
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    server = JobHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    body = json.dumps(
        {"command": [sys.executable, "-c", "print('from-http')"]}
    ).encode()
    request = urllib.request.Request(
        base + "/v1/jobs",
        body,
        {"Content-Type": "application/json", "Idempotency-Key": "same"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            submitted = json.load(response)
            assert response.status == 202
            assert response.headers["X-SharkRail-Durability"] == "volatile"
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.status == 200
            assert json.load(response)["job_id"] == submitted["job_id"]
        _terminal(service, submitted["job_id"])
        with urllib.request.urlopen(
            base + submitted["result_url"], timeout=3
        ) as response:
            assert json.load(response)["phase"] == "succeeded"
        with urllib.request.urlopen(
            base + f"/v1/jobs/{submitted['job_id']}/output?stream=stdout", timeout=3
        ) as response:
            assert response.read() == b"from-http\n"
        with urllib.request.urlopen(base + "/health/state", timeout=3) as response:
            assert json.load(response)["degraded"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_http_bearer_authentication(tmp_path):
    service = JobService(
        _config(tmp_path, server=ServerSettings(auth_token="test-token")),
        state_dir=tmp_path,
    )
    server = JobHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/health/ready"
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(endpoint, timeout=3)
        assert denied.value.code == 401
        request = urllib.request.Request(
            endpoint, headers={"Authorization": "Bearer test-token"}
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            assert json.load(response)["ready"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_registered_callback_is_signed_and_delivered(tmp_path):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received.append((dict(self.headers), self.rfile.read(length)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    callback_server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    callback_thread = threading.Thread(
        target=callback_server.serve_forever, daemon=True
    )
    callback_thread.start()
    endpoint = CallbackEndpoint(
        url=f"http://127.0.0.1:{callback_server.server_port}/done", secret="secret"
    )
    service = JobService(
        _config(
            tmp_path,
            callback_endpoints={"receiver": endpoint},
            notifications=NotificationSettings(
                max_concurrent_deliveries=1,
                request_timeout_seconds=2,
                max_attempts=2,
            ),
        ),
        state_dir=tmp_path,
    )
    service.start()
    try:
        job, _ = service.submit(
            "tenant",
            "callback",
            {
                "command": [sys.executable, "-c", "pass"],
                "callback_endpoint_id": "receiver",
            },
        )
        _terminal(service, job.id)
        deadline = time.monotonic() + 5
        while not received:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        headers, body = received[0]
        lowered = {name.lower(): value for name, value in headers.items()}
        timestamp = lowered["x-sharkrail-timestamp"]
        expected = hmac.new(
            b"secret", timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert lowered["x-sharkrail-signature"] == f"sha256={expected}"
    finally:
        service.close()
        callback_server.shutdown()
        callback_server.server_close()
        callback_thread.join(timeout=3)


def test_master_bounds_repeated_worker_crashes(tmp_path):
    control = replace(
        ControlSettings(),
        worker_restart_limit=1,
        worker_restart_window_seconds=10,
        worker_progress_timeout_seconds=2,
    )
    master = ControlMaster(
        _config(tmp_path, control=control),
        None,
        worker_target=_exiting_worker,
    )
    assert master.run() == 70


def test_master_worker_serves_and_shuts_down(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config_path = tmp_path / "service.toml"
    config_path.write_text(
        "\n".join(
            (
                "[server]",
                f'listen = "127.0.0.1:{port}"',
                "[job_store]",
                'url = "sqlite:///:memory:"',
                "[control]",
                "api_workers = 2",
                "controller_workers = 1",
                "notification_workers = 1",
                "worker_heartbeat_seconds = 1",
                "worker_progress_timeout_seconds = 5",
                "worker_drain_timeout_seconds = 2",
                "worker_restart_limit = 2",
                "worker_restart_window_seconds = 10",
                "[executor]",
                "workers = 1",
                "heartbeat_seconds = 1",
                "lease_seconds = 5",
            )
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        config_path.chmod(0o600)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sharkrail",
            "server",
            "--config",
            str(config_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(
                    base + "/health/ready", timeout=0.5
                ) as response:
                    assert json.load(response)["ready"] is True
                    break
            except (OSError, urllib.error.URLError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    raise AssertionError(
                        f"Master/Worker failed to become ready: {stdout}\n{stderr}"
                    )
                time.sleep(0.05)
    finally:
        process.terminate()
        process.wait(timeout=10)
    assert process.returncode == 0
