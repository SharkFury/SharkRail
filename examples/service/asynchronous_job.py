"""Submit a Job, disconnect from execution details, and inspect the result."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from sharkrail.service.config import (
    ExecutorSettings,
    JobStoreSettings,
    OutputStoreSettings,
    ServiceConfig,
)
from sharkrail.service.server import JobService

with tempfile.TemporaryDirectory(prefix="sharkrail-example-") as directory:
    config = ServiceConfig(
        job_store=JobStoreSettings(url="sqlite:///:memory:"),
        output_store=OutputStoreSettings(url="file://./output"),
        executor=ExecutorSettings(workers=1, heartbeat_seconds=1, lease_seconds=10),
    )
    service = JobService(config, state_dir=Path(directory))
    service.start()
    try:
        job, _ = service.submit(
            "example",
            "example-idempotency-key",
            {"command": [sys.executable, "-c", "print('asynchronous result')"]},
        )
        while not service.get(job.id).phase.terminal:
            time.sleep(0.02)
        completed = service.get(job.id)
        print(
            json.dumps(
                {
                    "job_id": completed.id,
                    "phase": completed.phase.value,
                    "durability": completed.durability,
                    "stdout": service.read_output(job.id, "stdout").decode(),
                }
            )
        )
    finally:
        service.close()
