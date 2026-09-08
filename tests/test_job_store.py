from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

import pytest

from sharkrail.service.models import (
    DEFAULT_JOB_TIMEOUT_SECONDS,
    MAX_JOB_OUTPUT_BYTES,
    MAX_JOB_TIMEOUT_SECONDS,
    JobPhase,
    JobSpec,
)
from sharkrail.service.output import FileOutputStore
from sharkrail.service.store import (
    AdmissionLimited,
    IdempotencyConflict,
    InstanceAlreadyRunning,
    JobNotFound,
    SqliteJobStore,
    StoreError,
)


def _finish(
    store: SqliteJobStore,
    job_id: str,
    attempt_id: str,
    *,
    phase: JobPhase = JobPhase.SUCCEEDED,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
):
    return store.finish(
        job_id,
        attempt_id,
        "worker",
        phase=phase,
        exit_code=0,
        reason="success",
        error=None,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_bytes=0,
        stderr_bytes=0,
        output_truncated=False,
    )


def test_submit_is_idempotent_and_conflicts_on_different_content():
    store = SqliteJobStore()
    try:
        first, created = store.submit("tenant", "key", JobSpec(("echo", "one")))
        duplicate, duplicate_created = store.submit(
            "tenant", "key", JobSpec(("echo", "one"))
        )
        assert created is True
        assert duplicate_created is False
        assert duplicate.id == first.id
        with pytest.raises(IdempotencyConflict):
            store.submit("tenant", "key", JobSpec(("echo", "two")))
    finally:
        store.close()


def test_admission_is_bounded():
    store = SqliteJobStore(max_jobs=1)
    try:
        store.submit("tenant", "one", JobSpec(("echo",)))
        with pytest.raises(AdmissionLimited):
            store.submit("tenant", "two", JobSpec(("echo",)))
    finally:
        store.close()


def test_claim_fencing_terminal_result_and_outbox():
    store = SqliteJobStore()
    try:
        job, _ = store.submit(
            "tenant",
            "key",
            JobSpec(("echo",), callback_endpoint_id="receiver"),
        )
        claimed = store.claim_next("worker", 30)
        assert claimed is not None
        assert claimed.phase == JobPhase.ASSIGNED
        assert claimed.attempt_id is not None
        store.mark_running(job.id, claimed.attempt_id, "worker")
        with pytest.raises(StoreError):
            store.finish(
                job.id,
                claimed.attempt_id,
                "stale-worker",
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
        result = store.finish(
            job.id,
            claimed.attempt_id,
            "worker",
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
        assert result.phase == JobPhase.SUCCEEDED
        assert len(store.outbox_due()) == 1
        repeated = store.finish(
            job.id,
            claimed.attempt_id,
            "worker",
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
        assert repeated.revision == result.revision
        assert len(store.outbox_due()) == 1
    finally:
        store.close()


def test_file_store_survives_restart_and_marks_running_job_lost(tmp_path: Path):
    url = "sqlite:///jobs.db"
    first = SqliteJobStore(url, state_dir=tmp_path)
    job, _ = first.submit("tenant", "key", JobSpec(("echo",)))
    claimed = first.claim_next("old-worker", 1)
    assert claimed is not None and claimed.attempt_id is not None
    first.mark_running(job.id, claimed.attempt_id, "old-worker")
    epoch = first.store_epoch
    first.close()

    second = SqliteJobStore(url, state_dir=tmp_path)
    try:
        assert second.store_epoch == epoch
        assert second.recover_interrupted() == 1
        assert second.get(job.id).phase == JobPhase.EXECUTOR_LOST
    finally:
        second.close()


def test_file_store_rejects_second_local_instance(tmp_path: Path):
    first = SqliteJobStore("sqlite:///jobs.db", state_dir=tmp_path)
    try:
        with pytest.raises(InstanceAlreadyRunning):
            SqliteJobStore("sqlite:///jobs.db", state_dir=tmp_path)
    finally:
        first.close()


def test_cancel_queued_job_is_terminal():
    store = SqliteJobStore()
    try:
        job, _ = store.submit("tenant", "key", JobSpec(("echo",)))
        canceled = store.request_cancel(job.id, "tenant")
        assert canceled.phase == JobPhase.CANCELED
        assert canceled.spec.desired_state == "cancelled"
        assert canceled.completed_at is not None
        assert canceled.reason == "cancelled_before_start"
    finally:
        store.close()


def test_cancel_wins_over_concurrent_success_and_observes_generation():
    store = SqliteJobStore()
    try:
        job, _ = store.submit("tenant", "key", JobSpec(("echo",)))
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        store.mark_running(job.id, claimed.attempt_id, "worker")

        requested = store.request_cancel(job.id, "tenant")
        assert requested.generation == 2
        assert requested.observed_generation == 1
        repeated = store.request_cancel(job.id, "tenant")
        assert repeated.generation == requested.generation
        assert repeated.revision == requested.revision

        completed = _finish(store, job.id, claimed.attempt_id)
        assert completed.phase == JobPhase.CANCELED
        assert completed.spec.desired_state == "cancelled"
        assert completed.observed_generation == completed.generation
        assert completed.reason == "cancelled"
    finally:
        store.close()


def test_stale_callback_delivery_cannot_overwrite_new_claim():
    store = SqliteJobStore()
    try:
        job, _ = store.submit(
            "tenant",
            "callback-fencing",
            JobSpec(("echo",), callback_endpoint_id="receiver"),
        )
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        _finish(store, job.id, claimed.attempt_id)

        first = store.claim_outbox_due(lease_seconds=0.001)
        assert len(first) == 1
        time.sleep(0.005)
        second = store.claim_outbox_due()
        assert len(second) == 1
        assert first[0].delivery_attempt_id != second[0].delivery_attempt_id

        assert not store.outbox_delivered(
            first[0].event_id, first[0].delivery_attempt_id
        )
        assert store.stats()["pending_callbacks"] == 1
        assert store.outbox_delivered(second[0].event_id, second[0].delivery_attempt_id)
        assert store.stats()["pending_callbacks"] == 0
    finally:
        store.close()


def test_job_spec_applies_non_optional_bounded_resource_limits():
    defaulted = JobSpec.from_dict({"command": ["echo"]})
    assert defaulted.timeout_seconds == DEFAULT_JOB_TIMEOUT_SECONDS

    with pytest.raises(ValueError, match="unknown Job field"):
        JobSpec.from_dict({"command": ["echo"], "timeoutSeconds": 1})
    with pytest.raises(ValueError, match="max_output_bytes"):
        JobSpec.from_dict(
            {"command": ["echo"], "max_output_bytes": MAX_JOB_OUTPUT_BYTES + 1}
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        JobSpec.from_dict(
            {"command": ["echo"], "timeout_seconds": MAX_JOB_TIMEOUT_SECONDS + 1}
        )
    with pytest.raises(ValueError, match="unknown callback field"):
        JobSpec.from_dict(
            {"command": ["echo"], "callback": {"endpoint_id": "x", "url": "y"}}
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        JobSpec.from_dict(
            {
                "command": ["echo"],
                "callback_endpoint_id": "x",
                "callback": {"endpoint_id": "y"},
            }
        )
    with pytest.raises(ValueError, match="non-empty string"):
        JobSpec.from_dict({"command": ["echo"], "callback_endpoint_id": ""})


def test_volatile_ttl_removes_output_files_with_job(tmp_path: Path):
    store = SqliteJobStore(job_ttl_seconds=0)
    output = FileOutputStore("file://./output", state_dir=tmp_path)
    store.set_output_cleaner(output.delete_job)
    try:
        job, _ = store.submit("tenant", "old", JobSpec(("echo",)))
        stdout = Path(output.write(job.id, "stdout", b"out"))
        stderr = Path(output.write(job.id, "stderr", b"err"))
        output_dir = stdout.parent
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        _finish(
            store,
            job.id,
            claimed.attempt_id,
            stdout_path=str(stdout),
            stderr_path=str(stderr),
        )
        time.sleep(0.002)

        store.submit("tenant", "new", JobSpec(("echo",)))

        with pytest.raises(JobNotFound):
            store.get(job.id)
        assert not stdout.exists()
        assert not stderr.exists()
        assert not output_dir.exists()
    finally:
        store.close()
        output.close()


def test_ttl_output_cleanup_failure_keeps_record_for_retry(tmp_path: Path):
    store = SqliteJobStore(job_ttl_seconds=0)
    output = FileOutputStore("file://./output", state_dir=tmp_path)
    attempts = 0
    real_delete = output.delete_job

    def flaky_delete(job_id, stdout_path, stderr_path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient unlink failure")
        real_delete(job_id, stdout_path, stderr_path)

    store.set_output_cleaner(flaky_delete)
    try:
        job, _ = store.submit("tenant", "old", JobSpec(("echo",)))
        stdout = output.write(job.id, "stdout", b"out")
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        _finish(store, job.id, claimed.attempt_id, stdout_path=stdout)
        time.sleep(0.002)

        assert store.prune_expired() == 0
        assert store.get(job.id).id == job.id
        assert Path(stdout).exists()

        assert store.prune_expired() == 1
        with pytest.raises(JobNotFound):
            store.get(job.id)
        assert not Path(stdout).exists()
    finally:
        store.close()
        output.close()


def test_ttl_cleanup_refuses_path_outside_output_root(tmp_path: Path):
    store = SqliteJobStore(job_ttl_seconds=0)
    output = FileOutputStore("file://./output", state_dir=tmp_path)
    store.set_output_cleaner(output.delete_job)
    external = tmp_path / "must-survive.bin"
    external.write_bytes(b"secret")
    try:
        job, _ = store.submit("tenant", "old", JobSpec(("echo",)))
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        _finish(store, job.id, claimed.attempt_id, stdout_path=str(external))
        time.sleep(0.002)

        assert store.prune_expired() == 0
        assert external.read_bytes() == b"secret"
        assert store.get(job.id).id == job.id
    finally:
        store.close()
        output.close()


def test_ttl_cleanup_and_output_write_share_capacity_lock(monkeypatch, tmp_path: Path):
    store = SqliteJobStore(job_ttl_seconds=0)
    output = FileOutputStore("file://./output", state_dir=tmp_path)
    store.set_output_cleaner(output.delete_job)
    try:
        job, _ = store.submit("tenant", "old", JobSpec(("echo",)))
        stdout = output.write(job.id, "stdout", b"old")
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        _finish(store, job.id, claimed.attempt_id, stdout_path=stdout)
        time.sleep(0.002)

        write_entered = threading.Event()
        allow_write = threading.Event()
        prune_finished = threading.Event()
        errors = []
        original_size = output._size_locked

        def blocked_size():
            write_entered.set()
            assert allow_write.wait(2)
            return original_size()

        monkeypatch.setattr(output, "_size_locked", blocked_size)

        def write_output():
            try:
                output.write("new-job", "stdout", b"new")
            except Exception as err:  # noqa: BLE001 - test thread boundary
                errors.append(err)

        def prune_output():
            try:
                store.prune_expired()
            except Exception as err:  # noqa: BLE001 - test thread boundary
                errors.append(err)
            finally:
                prune_finished.set()

        writer = threading.Thread(target=write_output)
        pruner = threading.Thread(target=prune_output)
        writer.start()
        assert write_entered.wait(2)
        pruner.start()
        assert not prune_finished.wait(0.05)
        allow_write.set()
        writer.join(timeout=2)
        pruner.join(timeout=2)

        assert errors == []
        assert (output.root / "new-job" / "stdout.bin").read_bytes() == b"new"
        with pytest.raises(JobNotFound):
            store.get(job.id)
    finally:
        store.close()
        output.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_sqlite_database_and_sidecars_are_private_under_common_umask(tmp_path: Path):
    state_dir = tmp_path / "new-state"
    previous_umask = os.umask(0o022)
    store = None
    try:
        store = SqliteJobStore("sqlite:///jobs.db", state_dir=state_dir)
        store.submit("tenant", "key", JobSpec(("echo",)))
    finally:
        os.umask(previous_umask)
    assert store is not None
    try:
        database = state_dir / "jobs.db"
        sidecars = (Path(f"{database}-wal"), Path(f"{database}-shm"))
        assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert all(path.exists() for path in sidecars)
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in sidecars)
    finally:
        store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_existing_sqlite_sidecars_are_secured_before_connect(monkeypatch, tmp_path):
    database = tmp_path / "jobs.db"
    sidecars = (Path(f"{database}-wal"), Path(f"{database}-shm"))
    for path in (database, *sidecars):
        path.write_bytes(b"")
        path.chmod(0o644)

    original_connect = sqlite3.connect
    observed_modes = []

    def inspect_permissions(*args, **kwargs):
        observed_modes.extend(
            stat.S_IMODE(path.stat().st_mode) for path in (database, *sidecars)
        )
        for path in sidecars:
            path.unlink()
        return original_connect(*args, **kwargs)

    monkeypatch.setattr("sharkrail.service.store.sqlite3.connect", inspect_permissions)
    store = SqliteJobStore("sqlite:///jobs.db", state_dir=tmp_path)
    try:
        assert observed_modes == [0o600, 0o600, 0o600]
    finally:
        store.close()


def test_existing_outbox_schema_is_migrated_with_delivery_fencing(tmp_path):
    database = tmp_path / "jobs.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE callback_outbox (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                endpoint_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    store = SqliteJobStore("sqlite:///jobs.db", state_dir=tmp_path)
    try:
        job, _ = store.submit(
            "tenant",
            "callback",
            JobSpec(("echo",), callback_endpoint_id="receiver"),
        )
        claimed = store.claim_next("worker", 30)
        assert claimed is not None and claimed.attempt_id is not None
        _finish(store, job.id, claimed.attempt_id)
        delivery = store.claim_outbox_due()
        assert len(delivery) == 1
        assert delivery[0].delivery_attempt_id is not None
    finally:
        store.close()


def test_volatile_metadata_admission_is_bounded():
    store = SqliteJobStore(max_metadata_bytes=10)
    try:
        with pytest.raises(AdmissionLimited):
            store.submit("tenant", "key", JobSpec(("echo", "long-command")))
    finally:
        store.close()


def test_resource_identity_fields_are_bounded():
    store = SqliteJobStore()
    try:
        with pytest.raises(ValueError, match="tenant_id exceeds"):
            store.submit("t" * 257, "key", JobSpec(("echo",)))
        with pytest.raises(ValueError, match="idempotency_key exceeds"):
            store.submit("tenant", "k" * 257, JobSpec(("echo",)))
    finally:
        store.close()


def test_lease_renewal_is_fenced():
    store = SqliteJobStore()
    try:
        store.submit("tenant", "key", JobSpec(("echo",)))
        claimed = store.claim_next("worker", 1)
        assert claimed is not None and claimed.attempt_id is not None
        before = time.time()
        assert store.renew_lease(claimed.id, claimed.attempt_id, "worker", 30)
        assert store.get(claimed.id).lease_expires_at >= before + 29
        assert not store.renew_lease(claimed.id, claimed.attempt_id, "stale", 30)
    finally:
        store.close()
