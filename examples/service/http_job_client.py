"""Submit through HTTP, disconnect, and fetch the terminal result later."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from sharkrail.runtime.policy import ExecutionPolicy
from sharkrail.service.config import ServiceConfig
from sharkrail.service.http import JobHTTPServer
from sharkrail.service.server import JobService

with tempfile.TemporaryDirectory(prefix="sharkrail-http-example-") as directory:
    policy = ExecutionPolicy(
        allowed_executables=frozenset({sys.executable}),
        allow_parent_environment=False,
        require_timeout=True,
    )
    service = JobService(
        ServiceConfig(), state_dir=Path(directory), execution_policy=policy
    )
    server = JobHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    request = urllib.request.Request(
        endpoint + "/v1/jobs",
        json.dumps(
            {"command": [sys.executable, "-c", "print('finished later')"]}
        ).encode("utf-8"),
        {"Content-Type": "application/json", "Idempotency-Key": "http-example"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            accepted = json.load(response)

        # The submission connection is already closed. A real client can return
        # hours later with the same status URL.
        while True:
            with urllib.request.urlopen(
                endpoint + accepted["status_url"], timeout=3
            ) as response:
                current = json.load(response)
            if current["status"] in {
                "succeeded",
                "failed",
                "timed_out",
                "canceled",
                "executor_lost",
            }:
                break
            time.sleep(0.02)

        with urllib.request.urlopen(
            endpoint + accepted["result_url"], timeout=3
        ) as response:
            result = json.load(response)
        print(
            json.dumps(
                {
                    "job_id": result["job_id"],
                    "status": result["status"],
                    "durability": result["durability"],
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
