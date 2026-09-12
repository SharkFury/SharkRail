from types import SimpleNamespace

import pytest

from sharkrail.service import ownership as ownership_module
from sharkrail.service.ownership import ProcessOwnershipClient


class AckConnection:
    def __init__(self, *, accepted=True, detail=None):
        self.accepted = accepted
        self.detail = detail
        self.sent = []
        self.closed = False
        self.poll_enabled = True
        self.unrelated_response = False

    def send(self, message):
        self.sent.append(message)

    def poll(self, _timeout=None):
        return self.poll_enabled

    def recv(self):
        request_id = self.sent[-1][1]
        if self.unrelated_response:
            self.unrelated_response = False
            return ("ack", "another-request", True, None)
        return ("ack", request_id, self.accepted, self.detail)

    def close(self):
        self.closed = True


def _handle(*, birth_identity="posix:123"):
    return SimpleNamespace(
        pid=42,
        process_tree="process_group",
        birth_identity=birth_identity,
        _ownership_id=None,
    )


def test_ownership_client_registers_once_unregisters_and_closes_idempotently():
    connection = AckConnection()
    client = ProcessOwnershipClient(connection, timeout_seconds=1)
    handle = _handle()

    client.register(handle)
    ownership_id = handle._ownership_id
    client.register(handle)

    assert ownership_id is not None
    assert len(connection.sent) == 1
    operation, _request_id, record = connection.sent[0]
    assert operation == "register"
    assert record["ownership_id"] == ownership_id
    assert record["pid"] == 42
    assert record["pgid"] == 42

    client.unregister(handle)
    client.unregister(handle)
    assert handle._ownership_id is None
    assert [message[0] for message in connection.sent] == ["register", "unregister"]

    client.close()
    client.close()
    assert connection.closed is True


def test_ownership_client_recovers_registration_timeout_during_cleanup():
    connection = AckConnection()
    connection.poll_enabled = False
    client = ProcessOwnershipClient(connection, timeout_seconds=1)
    handle = _handle()

    with pytest.raises(TimeoutError, match="did not acknowledge"):
        client.register(handle)

    # The ID is deliberately retained because the registration outcome is
    # ambiguous. Cleanup can safely issue a matching unregister request.
    assert handle._ownership_id is not None
    connection.poll_enabled = True
    client.unregister(handle)
    assert handle._ownership_id is None


def test_ownership_client_surfaces_master_rejection_and_ignores_other_responses():
    connection = AckConnection(accepted=False, detail="registry full")
    connection.unrelated_response = True
    client = ProcessOwnershipClient(connection, timeout_seconds=1)

    with pytest.raises(RuntimeError, match="registry full"):
        client.register(_handle())


def test_ownership_client_rejects_missing_identity_windows_owner_and_closed_channel(
    monkeypatch,
):
    connection = AckConnection()
    client = ProcessOwnershipClient(connection, timeout_seconds=1)

    with pytest.raises(RuntimeError, match="birth identity"):
        client.register(_handle(birth_identity=None))

    monkeypatch.setattr(
        ownership_module,
        "os",
        SimpleNamespace(name="nt", getpid=lambda: 7),
    )
    with pytest.raises(RuntimeError, match="stable Job owner"):
        client.register(_handle())

    client.close()
    with pytest.raises(RuntimeError, match="channel is closed"):
        client._request("unregister", "owner", None)
