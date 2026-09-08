import asyncio
import hashlib
import hmac
import http.client
import json
import os
import socket
import socketserver
import ssl
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

from sharkrail.runtime.sessions import SessionManager
from sharkrail.service.config import (
    CallbackEndpoint,
    ConfigError,
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
from sharkrail.service.models import JobPhase, OutboxRecord
from sharkrail.service.server import JobService, _post_callback
from sharkrail.service.store import StoreError
from sharkrail.service.windows_security import secure_private_path


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """Test callback server that never performs reverse DNS during bind."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _exiting_worker(_config_path, _parent_pid, heartbeat, _execution_policy):
    if heartbeat is not None:
        heartbeat.send((time.monotonic(), {"ready": True, "active_processes": []}))
        heartbeat.close()


def _config(tmp_path: Path, **changes):
    policy_path = tmp_path / "job-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "allowed_executables": [sys.executable, "echo"],
                "allow_parent_environment": False,
                "require_timeout": True,
            }
        ),
        encoding="utf-8",
    )
    secure_private_path(policy_path, directory=False)
    values = {
        "job_store": JobStoreSettings(url="sqlite:///:memory:"),
        "output_store": OutputStoreSettings(url="file://./output"),
        "executor": ExecutorSettings(
            workers=2,
            heartbeat_seconds=1,
            lease_seconds=10,
            policy_file=str(policy_path),
        ),
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


def test_direct_service_construction_validates_configuration(tmp_path):
    with pytest.raises(ConfigError, match="tenant_id"):
        JobService(
            _config(
                tmp_path,
                callback_endpoints={
                    "invalid": CallbackEndpoint(
                        url="https://example.com/callback", tenant_id=""
                    )
                },
            ),
            state_dir=tmp_path,
        )


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
        assert service.read_output(job.id, "stdout") == f"hello{os.linesep}".encode()
        assert service.health()["reason"] == "DEGRADED_VOLATILE_STORE"
    finally:
        service.close()


def test_service_without_host_policy_denies_every_command(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        executor=ExecutorSettings(workers=1, heartbeat_seconds=1, lease_seconds=1),
    )
    service = JobService(config, state_dir=tmp_path)
    service.start()
    try:
        job, _ = service.submit(
            "tenant",
            "deny-all",
            {"command": [sys.executable, "-c", "print('must-not-run')"]},
        )
        result = _terminal(service, job.id)
        assert result.phase == JobPhase.FAILED
        assert result.error is not None
        assert "execution denied by policy" in result.error["message"]
        assert service.read_output(job.id, "stdout") == b""
    finally:
        service.close()


def test_service_jobs_never_inherit_service_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARKRAIL_SERVICE_SECRET", "must-not-leak")
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    service.start()
    try:
        job, _ = service.submit(
            "tenant",
            "clean-environment",
            {
                "command": [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('SHARKRAIL_SERVICE_SECRET', 'missing'))",
                ]
            },
        )
        result = _terminal(service, job.id)
        assert result.phase == JobPhase.SUCCEEDED
        assert service.read_output(job.id, "stdout").strip() == b"missing"
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


def test_cancel_during_session_registration_cannot_be_lost(monkeypatch, tmp_path):
    entered_start = threading.Event()
    allow_start = threading.Event()
    original_start = SessionManager.start

    async def paused_start(manager, *args, **kwargs):
        entered_start.set()
        while not allow_start.is_set():
            await asyncio.sleep(0.005)
        return await original_start(manager, *args, **kwargs)

    monkeypatch.setattr(SessionManager, "start", paused_start)
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    service.start()
    try:
        job, _ = service.submit(
            "tenant",
            "registration-race",
            {"command": [sys.executable, "-c", "import time; time.sleep(30)"]},
        )
        assert entered_start.wait(3)
        requested = service.cancel(job.id, "tenant")
        assert requested.spec.desired_state == "cancelled"
        assert requested.observed_generation < requested.generation
        allow_start.set()

        completed = _terminal(service, job.id)
        assert completed.phase == JobPhase.CANCELED
        assert completed.observed_generation == completed.generation
    finally:
        allow_start.set()
        service.close()


def test_service_shutdown_cancels_job_stuck_in_session_start(monkeypatch, tmp_path):
    entered = threading.Event()

    async def stuck_start(_manager, *_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(SessionManager, "start", stuck_start)
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    service.start()
    service.submit(
        "tenant",
        "stuck-start",
        {"command": [sys.executable, "-c", "pass"]},
    )
    assert entered.wait(3)

    started = time.monotonic()
    service.close()
    assert time.monotonic() - started < 2


def test_terminal_persistence_failure_is_recovered_after_lease_expiry(
    monkeypatch, tmp_path
):
    config = _config(tmp_path)
    config = replace(
        config,
        executor=replace(config.executor, heartbeat_seconds=1, lease_seconds=1),
    )
    service = JobService(config, state_dir=tmp_path)
    original_finish = service.store.finish
    failures = 0

    def fail_both_finalization_paths(*args, **kwargs):
        nonlocal failures
        if failures < 6:
            failures += 1
            raise StoreError("simulated terminal write failure")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(service.store, "finish", fail_both_finalization_paths)
    service.start()
    try:
        job, _ = service.submit(
            "tenant",
            "finish-failure",
            {"command": [sys.executable, "-c", "print('uncommitted')"]},
        )
        result = _terminal(service, job.id, timeout=5)
        assert failures == 6
        assert result.phase == JobPhase.EXECUTOR_LOST
        assert result.reason == "executor_lease_expired"
        assert not (service.output.root / job.id).exists()
    finally:
        service.close()


def test_notification_deliveries_run_concurrently(tmp_path, monkeypatch):
    endpoint = CallbackEndpoint(url="https://example.com/completed", tenant_id="tenant")
    service = JobService(
        _config(
            tmp_path,
            callback_endpoints={"receiver": endpoint},
            control=ControlSettings(notification_workers=1),
            notifications=NotificationSettings(max_concurrent_deliveries=2),
        ),
        state_dir=tmp_path,
    )
    for index in range(2):
        job, _ = service.submit(
            "tenant",
            f"callback-{index}",
            {"command": ["echo"], "callback_endpoint_id": "receiver"},
        )
        claimed = service.store.claim_next(service.worker_id, 30)
        assert claimed is not None and claimed.attempt_id is not None
        service.store.finish(
            job.id,
            claimed.attempt_id,
            service.worker_id,
            phase=JobPhase.SUCCEEDED,
            exit_code=0,
            reason="success",
            error=None,
            stdout_path=None,
            stderr_path=None,
            stdout_bytes=0,
            stderr_bytes=0,
            output_truncated=False,
        )

    state_lock = threading.Lock()
    release = threading.Event()
    concurrent = threading.Event()
    active = 0

    def blocked_delivery(record):
        nonlocal active
        with state_lock:
            active += 1
            if active == 2:
                concurrent.set()
        release.wait(3)
        service.store.outbox_delivered(record.event_id, record.delivery_attempt_id)
        with state_lock:
            active -= 1

    monkeypatch.setattr(service, "_deliver", blocked_delivery)
    service.start()
    try:
        assert concurrent.wait(2), "callbacks were serialized"
    finally:
        release.set()
        service.close()


def test_callback_endpoint_is_scoped_to_submitting_tenant(tmp_path):
    endpoint = CallbackEndpoint(url="https://example.com/completed", tenant_id="owner")
    service = JobService(
        _config(tmp_path, callback_endpoints={"receiver": endpoint}),
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(ValueError, match="unknown callback endpoint_id"):
            service.submit(
                "attacker",
                "cross-tenant",
                {"command": ["echo"], "callback_endpoint_id": "receiver"},
            )
        accepted, created = service.submit(
            "owner",
            "owned",
            {"command": ["echo"], "callback_endpoint_id": "receiver"},
        )
        assert created is True
        assert accepted.tenant_id == "owner"
    finally:
        service.close()


def test_callback_delivery_rechecks_persisted_job_tenant(tmp_path, monkeypatch):
    endpoints = {
        "receiver": CallbackEndpoint(
            url="https://example.com/completed", tenant_id="owner"
        )
    }
    service = JobService(
        _config(tmp_path, callback_endpoints=endpoints), state_dir=tmp_path
    )
    try:
        job, _ = service.submit(
            "owner",
            "callback",
            {"command": ["echo"], "callback_endpoint_id": "receiver"},
        )
        claimed = service.store.claim_next(service.worker_id, 30)
        assert claimed is not None and claimed.attempt_id is not None
        service.store.finish(
            job.id,
            claimed.attempt_id,
            service.worker_id,
            phase=JobPhase.SUCCEEDED,
            exit_code=0,
            reason="success",
            error=None,
            stdout_path=None,
            stderr_path=None,
            stdout_bytes=0,
            stderr_bytes=0,
            output_truncated=False,
        )
        records = service.store.claim_outbox_due()
        assert len(records) == 1
        assert records[0].tenant_id == "owner"

        endpoints["receiver"] = CallbackEndpoint(
            url="https://example.com/completed", tenant_id="other"
        )

        def unexpected_delivery(**_kwargs):
            raise AssertionError("cross-tenant callback must not be sent")

        monkeypatch.setattr(
            "sharkrail.service.server._post_callback", unexpected_delivery
        )
        service._deliver(records[0])
        assert service.store.stats()["dead_callbacks"] == 1
    finally:
        service.close()


def test_callback_addresses_share_one_wall_clock_deadline(monkeypatch):
    connections = []

    class HangingSocket:
        def __init__(self):
            self.closed = threading.Event()

        def shutdown(self, _operation):
            self.closed.set()

    class HangingConnection:
        def __init__(self, _address, _port, *, timeout):
            self.timeout = timeout
            self.sock = HangingSocket()
            connections.append(self)

        def connect(self):
            return None

        def request(self, *_args, **_kwargs):
            return None

        def getresponse(self):
            assert self.sock.closed.wait(1)
            raise OSError("connection aborted")

        def close(self):
            self.sock.closed.set()

    monkeypatch.setattr(
        "sharkrail.service.server.http.client.HTTPConnection", HangingConnection
    )
    started = time.monotonic()
    with pytest.raises(OSError, match="deadline exceeded"):
        _post_callback(
            scheme="http",
            hostname="example.com",
            port=80,
            addresses=("192.0.2.1", "192.0.2.2"),
            target="/callback",
            body=b"{}",
            headers={},
            timeout=0.03,
        )
    assert time.monotonic() - started < 0.5
    assert len(connections) == 1


def test_callback_deadline_interrupts_tls_handshake(monkeypatch):
    class RawSocket:
        def close(self):
            return None

    class BlockingTLSSocket:
        def __init__(self):
            self.closed = threading.Event()
            self.timeout = None

        def settimeout(self, timeout):
            self.timeout = timeout

        def do_handshake(self):
            assert self.closed.wait(1)
            raise OSError("TLS handshake aborted")

        def shutdown(self, _operation):
            self.closed.set()

        def close(self):
            self.closed.set()

    tls_socket = BlockingTLSSocket()

    class Context:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, raw_socket, *, server_hostname, do_handshake_on_connect):
            assert isinstance(raw_socket, RawSocket)
            assert server_hostname == "example.com"
            assert do_handshake_on_connect is False
            return tls_socket

    monkeypatch.setattr("sharkrail.service.server.ssl.create_default_context", Context)
    monkeypatch.setattr(
        "sharkrail.service.server.socket.create_connection",
        lambda *_args, **_kwargs: RawSocket(),
    )

    started = time.monotonic()
    with pytest.raises(OSError, match="deadline exceeded"):
        _post_callback(
            scheme="https",
            hostname="example.com",
            port=443,
            addresses=("192.0.2.1",),
            target="/callback",
            body=b"{}",
            headers={},
            timeout=0.03,
        )

    assert time.monotonic() - started < 0.5
    assert tls_socket.timeout is not None
    assert tls_socket.timeout <= 0.03


def test_callback_dns_resolution_is_bounded_and_does_not_block_close(
    monkeypatch, tmp_path
):
    endpoint = CallbackEndpoint(url="https://example.com", tenant_id="tenant")
    service = JobService(
        _config(tmp_path, callback_endpoints={"receiver": endpoint}),
        state_dir=tmp_path,
    )
    resolver_started = threading.Event()
    release_resolver = threading.Event()

    def stuck_resolution(_endpoint):
        resolver_started.set()
        release_resolver.wait(2)
        return "example.com", 443, ("192.0.2.1",)

    monkeypatch.setattr(
        "sharkrail.service.server.validate_callback_destination", stuck_resolution
    )
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="resolution deadline exceeded"):
            service._resolve_callback_destination(endpoint, timeout=0.03)
        assert resolver_started.is_set()
        assert time.monotonic() - started < 0.5

        started = time.monotonic()
        service.close()
        assert time.monotonic() - started < 0.5
    finally:
        release_resolver.set()
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
            assert response.read() == f"from-http{os.linesep}".encode()
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
        denied.value.close()
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


def test_http_binds_tenant_identity_to_bearer_token(tmp_path):
    service = JobService(
        _config(
            tmp_path,
            server=ServerSettings(
                tenant_tokens={"alpha": "alpha-token", "beta": "beta-token"}
            ),
        ),
        state_dir=tmp_path,
    )
    server = JobHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    body = json.dumps({"command": [sys.executable, "-c", "pass"]}).encode()
    submit = urllib.request.Request(
        base + "/v1/jobs",
        body,
        {
            "Authorization": "Bearer alpha-token",
            "Content-Type": "application/json",
            "Idempotency-Key": "tenant-bound",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(submit, timeout=3) as response:
            job_id = json.load(response)["job_id"]

        forged = urllib.request.Request(
            base + f"/v1/jobs/{job_id}",
            headers={
                "Authorization": "Bearer alpha-token",
                "X-SharkRail-Tenant": "beta",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as mismatch:
            urllib.request.urlopen(forged, timeout=3)
        assert mismatch.value.code == 403
        mismatch.value.close()

        cross_tenant = urllib.request.Request(
            base + f"/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer beta-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as hidden:
            urllib.request.urlopen(cross_tenant, timeout=3)
        assert hidden.value.code == 404
        hidden.value.close()

        cross_tenant_cancel = urllib.request.Request(
            base + f"/v1/jobs/{job_id}/cancel",
            data=b"",
            headers={"Authorization": "Bearer beta-token"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as hidden_cancel:
            urllib.request.urlopen(cross_tenant_cancel, timeout=3)
        assert hidden_cancel.value.code == 404
        hidden_cancel.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_detailed_health_requires_distinct_admin_token(tmp_path):
    service = JobService(
        _config(
            tmp_path,
            server=ServerSettings(
                tenant_tokens={"tenant": "tenant-token"},
                admin_token="admin-token",
            ),
        ),
        state_dir=tmp_path,
    )
    server = JobHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        tenant_headers = {"Authorization": "Bearer tenant-token"}
        ready = urllib.request.Request(base + "/health/ready", headers=tenant_headers)
        with urllib.request.urlopen(ready, timeout=3) as response:
            payload = json.load(response)
        assert set(payload) == {"ready", "degraded", "reason"}

        state = urllib.request.Request(base + "/health/state", headers=tenant_headers)
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(state, timeout=3)
        assert denied.value.code == 401
        denied.value.close()

        admin_state = urllib.request.Request(
            base + "/health/state",
            headers={"Authorization": "Bearer admin-token"},
        )
        with urllib.request.urlopen(admin_state, timeout=3) as response:
            detailed = json.load(response)
        assert "active_processes" in detailed
        assert "store" in detailed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_non_ascii_authorization_is_rejected_without_handler_failure(tmp_path):
    service = JobService(
        _config(tmp_path, server=ServerSettings(auth_token="test-token")),
        state_dir=tmp_path,
    )
    server = JobHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3
        )
        connection.putrequest("GET", "/health/ready")
        connection.putheader("Authorization", "Bearer \xff")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()

        valid = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/health/ready",
            headers={"Authorization": "Bearer test-token"},
        )
        with urllib.request.urlopen(valid, timeout=3) as response:
            assert json.load(response)["ready"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_http_bind_avoids_reverse_dns(monkeypatch, tmp_path):
    def fail_reverse_dns(_host):
        raise AssertionError("reverse DNS must not run while binding")

    monkeypatch.setattr(socket, "getfqdn", fail_reverse_dns)
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    server = JobHTTPServer(("127.0.0.1", 0), service)
    try:
        assert server.server_name == "127.0.0.1"
    finally:
        server.server_close()
        service.close()


def test_http_direct_construction_rejects_non_loopback_bind(tmp_path):
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    try:
        with pytest.raises(ValueError, match="must bind to loopback"):
            JobHTTPServer(("0.0.0.0", 0), service)
    finally:
        service.close()


def test_http_supports_ipv6_loopback(tmp_path):
    if not socket.has_ipv6:
        pytest.skip("IPv6 is unavailable")
    service = JobService(_config(tmp_path), state_dir=tmp_path)
    try:
        server = JobHTTPServer(("::1", 0), service)
    except OSError:
        service.close()
        pytest.skip("IPv6 loopback is unavailable")
    try:
        assert server.address_family == socket.AF_INET6
        assert server.server_address[0] == "::1"
    finally:
        server.server_close()
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

    callback_server = _LoopbackHTTPServer(("127.0.0.1", 0), Receiver)
    callback_thread = threading.Thread(
        target=callback_server.serve_forever, daemon=True
    )
    callback_thread.start()
    endpoint = CallbackEndpoint(
        url=f"http://127.0.0.1:{callback_server.server_port}/done",
        tenant_id="tenant",
        secret="secret",
        allow_private_networks=True,
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


def test_callback_redirect_is_not_followed_or_given_signature(tmp_path):
    redirected_headers = []

    class RedirectTarget(BaseHTTPRequestHandler):
        def do_POST(self):
            redirected_headers.append(dict(self.headers))
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    target_server = _LoopbackHTTPServer(("127.0.0.1", 0), RedirectTarget)
    target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
    target_thread.start()

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(307)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{target_server.server_port}/stolen",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            return

    redirect_server = _LoopbackHTTPServer(("127.0.0.1", 0), Redirector)
    redirect_thread = threading.Thread(
        target=redirect_server.serve_forever, daemon=True
    )
    redirect_thread.start()
    endpoint = CallbackEndpoint(
        url=f"http://127.0.0.1:{redirect_server.server_port}/callback",
        tenant_id="tenant",
        secret="must-not-be-forwarded",
        allow_private_networks=True,
    )
    service = JobService(
        _config(tmp_path, callback_endpoints={"redirect": endpoint}),
        state_dir=tmp_path,
    )
    try:
        service._deliver(
            OutboxRecord(
                event_id="event",
                job_id="job",
                tenant_id="tenant",
                endpoint_id="redirect",
                payload={"event_id": "event"},
                attempts=0,
                next_attempt_at=time.time(),
            )
        )
        assert redirected_headers == []
    finally:
        service.close()
        redirect_server.shutdown()
        redirect_server.server_close()
        redirect_thread.join(timeout=3)
        target_server.shutdown()
        target_server.server_close()
        target_thread.join(timeout=3)


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
            except urllib.error.HTTPError as err:
                err.close()
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    raise AssertionError(
                        f"Master/Worker failed to become ready: {stdout}\n{stderr}"
                    )
                time.sleep(0.05)
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
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    # POSIX SIGTERM is handled gracefully by the master. Windows
    # Popen.terminate() is TerminateProcess(), so only assert that the process
    # was reaped after it had successfully served a readiness request.
    assert process.returncode is not None
