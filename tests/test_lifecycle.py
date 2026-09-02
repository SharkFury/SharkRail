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

