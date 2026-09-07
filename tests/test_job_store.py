import time
from pathlib import Path

import pytest

from sharkrail.service.models import JobPhase, JobSpec
from sharkrail.service.store import (
    AdmissionLimited,
    IdempotencyConflict,
    InstanceAlreadyRunning,
    SqliteJobStore,
    StoreError,
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
