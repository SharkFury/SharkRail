"""Synchronous Worker-to-Master process ownership registration."""

from __future__ import annotations

import os
import threading
import time
from multiprocessing.connection import Connection
from typing import Any
from uuid import uuid4

from ..runtime.backends import ProcessHandle, WindowsPtyProcessHandle


class ProcessOwnershipClient:
    """Register process trees with the Master and wait for an explicit ACK."""

    def __init__(
        self,
        connection: Connection,
        *,
        timeout_seconds: float,
    ) -> None:
        self._connection = connection
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._closed = False

    def register(self, handle: ProcessHandle) -> None:
        if handle._ownership_id is not None:
            return
        if handle.birth_identity is None:
            raise RuntimeError("process birth identity is unavailable")
        ownership_id = f"owner_{uuid4().hex}"
        source_job_handle: int | None = None
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            job = (
                handle.broker_job
                if isinstance(handle, WindowsPtyProcessHandle)
                else getattr(handle, "job", None)
            )
            if job is None:
                raise RuntimeError("Windows process tree has no stable Job owner")
            source_job_handle = job.handle_value
        record: dict[str, object] = {
            "ownership_id": ownership_id,
            "pid": handle.pid,
            "pgid": handle.pid if handle.process_tree == "process_group" else None,
            "process_tree": handle.process_tree,
            "birth_identity": handle.birth_identity,
            "source_pid": os.getpid(),
            "source_job_handle": source_job_handle,
        }
        handle._ownership_id = ownership_id
        self._request("register", ownership_id, record)

    def unregister(self, handle: ProcessHandle) -> None:
        ownership_id = handle._ownership_id
        if ownership_id is None:
            return
        self._request("unregister", ownership_id, {"ownership_id": ownership_id})
        handle._ownership_id = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _request(
        self, operation: str, ownership_id: str, payload: dict[str, object] | None
    ) -> None:
        request_id = f"request_{uuid4().hex}"
        deadline = time.monotonic() + self._timeout_seconds
        with self._lock:
            if self._closed:
                raise RuntimeError("Master ownership channel is closed")
            self._connection.send((operation, request_id, payload))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._connection.poll(remaining):
                    raise TimeoutError(
                        f"Master did not acknowledge process {operation}"
                    )
                response: Any = self._connection.recv()
                if (
                    isinstance(response, tuple)
                    and len(response) >= 3
                    and response[0] == "ack"
                    and response[1] == request_id
                ):
                    if response[2] is True:
                        return
                    detail = response[3] if len(response) > 3 else "request rejected"
                    raise RuntimeError(f"Master process {operation} failed: {detail}")
