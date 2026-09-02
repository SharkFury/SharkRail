import pytest

from sharkrail.lifecycle import InvalidTransition, SessionLifecycle, SessionState


def test_lifecycle_success_path():
    session = SessionLifecycle()
    assert session.state == SessionState.PENDING
    session.start()
    assert session.state == SessionState.RUNNING
    session.complete()
    assert session.state == SessionState.COMPLETED


def test_lifecycle_cancel_only_from_running():
    session = SessionLifecycle()
    session.start()
    session.cancel()
    assert session.state == SessionState.CANCELED


def test_lifecycle_invalid_transition_raises():
    session = SessionLifecycle()
    session.start()
    session.complete()
    with pytest.raises(InvalidTransition):
        session.start()


def test_lifecycle_full_runtime_path():
    session = SessionLifecycle()
    session.accept()
    session.begin_start()
    session.start()
    session.begin_exit()
    session.begin_drain()
    session.complete()
    session.dispose()

    assert session.history == [
        SessionState.CREATED,
        SessionState.ACCEPTED,
        SessionState.STARTING,
        SessionState.RUNNING,
        SessionState.EXITING,
        SessionState.DRAINING,
        SessionState.COMPLETED,
        SessionState.DISPOSED,
    ]


def test_lifecycle_cancellation_completes_after_drain():
    session = SessionLifecycle()
    session.start()
    session.begin_cancel()
    session.begin_drain()
    session.complete()
    assert session.state == SessionState.COMPLETED
