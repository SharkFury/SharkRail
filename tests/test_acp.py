import asyncio
import os
import sys

import pytest

from sharkrail.acp import AcpTerminalAdapter, _decode_tail, _exit_status


def test_acp_terminal_create_output_wait_and_release():
    async def _run() -> None:
        adapter = AcpTerminalAdapter()
        created = await adapter.handle(
            "terminal/create",
            {
                "sessionId": "acp-session",
                "command": sys.executable,
                "args": ["-c", "print('012345')"],
                "cwd": os.getcwd(),
                "outputByteLimit": 4,
            },
        )
        terminal_id = created["terminalId"]
        status = await adapter.handle(
            "terminal/wait_for_exit",
            {"sessionId": "acp-session", "terminalId": terminal_id},
        )
        output = await adapter.handle(
            "terminal/output",
            {"sessionId": "acp-session", "terminalId": terminal_id},
        )

        assert status == {"exitCode": 0, "signal": None}
        assert output["output"].replace("\r\n", "\n").endswith("45\n")
        assert output["truncated"] is True
        assert output["exitStatus"] == status
        assert len(output["output"].encode()) <= 4

        assert (
            await adapter.handle(
                "terminal/release",
                {"sessionId": "acp-session", "terminalId": terminal_id},
            )
            == {}
        )
        assert adapter.manager.session_count == 0

    asyncio.run(_run())


def test_acp_terminal_ownership_isolated():
    async def _run() -> None:
        adapter = AcpTerminalAdapter()
        created = await adapter.handle(
            "terminal/create",
            {
                "sessionId": "owner",
                "command": sys.executable,
                "args": ["-c", "import time; time.sleep(10)"],
            },
        )
        terminal_id = created["terminalId"]
        with pytest.raises(KeyError):
            await adapter.handle(
                "terminal/output",
                {"sessionId": "other", "terminalId": terminal_id},
            )
        await adapter.handle(
            "terminal/kill",
            {"sessionId": "owner", "terminalId": terminal_id},
        )
        await adapter.handle(
            "terminal/release",
            {"sessionId": "owner", "terminalId": terminal_id},
        )

    asyncio.run(_run())


def test_acp_validates_create_request():
    async def _run() -> None:
        adapter = AcpTerminalAdapter()
        with pytest.raises(TypeError, match="absolute"):
            await adapter.create(
                {"sessionId": "s", "command": sys.executable, "cwd": "relative"}
            )
        with pytest.raises(ValueError, match="duplicate"):
            await adapter.create(
                {
                    "sessionId": "s",
                    "command": sys.executable,
                    "env": [
                        {"name": "A", "value": "1"},
                        {"name": "A", "value": "2"},
                    ],
                }
            )

    asyncio.run(_run())


def test_acp_utf8_tail_boundary_and_signal_mapping():
    assert _decode_tail("鲨鱼".encode()[1:]) == "鱼"
    assert _exit_status(7) == {"exitCode": 7, "signal": None}
    if os.name != "nt":
        assert _exit_status(-15) == {"exitCode": None, "signal": "SIGTERM"}
