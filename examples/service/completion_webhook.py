"""Receive and verify a signed, deduplicatable completion webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import socketserver
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sharkrail.runtime.policy import ExecutionPolicy
from sharkrail.service.config import CallbackEndpoint, ServiceConfig
from sharkrail.service.server import JobService

received: list[tuple[dict[str, str], bytes]] = []


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        headers = {name.lower(): value for name, value in self.headers.items()}
        received.append((headers, self.rfile.read(length)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class LoopbackHTTPServer(ThreadingHTTPServer):
    """Bind locally without HTTPServer's potentially blocking reverse lookup."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


callback = LoopbackHTTPServer(("127.0.0.1", 0), Receiver)
callback_thread = threading.Thread(target=callback.serve_forever, daemon=True)
callback_thread.start()

with tempfile.TemporaryDirectory(prefix="sharkrail-callback-example-") as directory:
    config = ServiceConfig(
        callback_endpoints={
            "build": CallbackEndpoint(
                url=f"http://127.0.0.1:{callback.server_port}/completed",
                tenant_id="example",
                secret="example-secret",
                # This example intentionally targets its own local test receiver.
                allow_private_networks=True,
            )
        }
    )
    policy = ExecutionPolicy(
        allowed_executables=frozenset({sys.executable}),
        allow_parent_environment=False,
        require_timeout=True,
    )
    service = JobService(config, state_dir=Path(directory), execution_policy=policy)
    service.start()
    try:
        job, _ = service.submit(
            "example",
            "callback-example",
            {
                "command": [sys.executable, "-c", "pass"],
                "callback_endpoint_id": "build",
            },
        )
        deadline = time.monotonic() + 5
        while not received:
            if time.monotonic() >= deadline:
                raise RuntimeError("completion callback was not delivered")
            time.sleep(0.02)

        headers, body = received[0]
        timestamp = headers["x-sharkrail-timestamp"]
        digest = hmac.new(
            b"example-secret",
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        print(
            json.dumps(
                {
                    "job_id": job.id,
                    "event_id": headers["x-sharkrail-event-id"],
                    "signature_valid": hmac.compare_digest(
                        headers["x-sharkrail-signature"], f"sha256={digest}"
                    ),
                }
            )
        )
    finally:
        service.close()
        callback.shutdown()
        callback.server_close()
        callback_thread.join(timeout=3)
