"""Persist a completed Job and inspect it after a Worker restart."""

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

with tempfile.TemporaryDirectory(prefix="sharkrail-durable-example-") as directory:
    state_dir = Path(directory)
    config = ServiceConfig(
        job_store=JobStoreSettings(url="sqlite:///jobs.db"),
        output_store=OutputStoreSettings(url="file://./output"),
        executor=ExecutorSettings(workers=1, heartbeat_seconds=1, lease_seconds=10),
    )
    first = JobService(config, state_dir=state_dir)
    first.start()
    job, _ = first.submit(
        "example",
        "durable-example",
        {"command": [sys.executable, "-c", "print('survives restart')"]},
    )
    while not first.get(job.id).phase.terminal:
        time.sleep(0.02)
    first.close()

    second = JobService(config, state_dir=state_dir)
    second.start()
    try:
        restored = second.get(job.id)
        print(
            json.dumps(
                {
                    "job_id": restored.id,
                    "status": restored.phase.value,
                    "durability": restored.durability,
                    "stdout": second.read_output(job.id, "stdout").decode().strip(),
                }
            )
        )
    finally:
        second.close()
