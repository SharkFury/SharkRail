"""Write a bounded local audit whose output payload is redacted by default."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from sharkrail import CommandRunner, CommandSpec, EventRecorder


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        recorder = EventRecorder(path, max_bytes=64 * 1024)
        result = await CommandRunner(event_recorder=recorder).run(
            CommandSpec(sys.executable, ("-c", "print('secret output')")),
            timeout_ms=5_000,
        )
        records = [json.loads(line) for line in path.read_text().splitlines()]
        output_record = next(record for record in records if record["kind"] == "stdout")
        print(
            json.dumps(
                {
                    "reason": result.reason.value,
                    "records": len(records),
                    "output_redacted": output_record["payload"]["output_redacted"],
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
