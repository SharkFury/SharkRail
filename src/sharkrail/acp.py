"""Agent Client Protocol terminal adapter backed by SharkRail."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any

from .models import CommandMode
from .routing import direct_command
from .sessions import Session, SessionManager


class AcpTerminalAdapter:
    """Implement ACP v1 client-side terminal methods.

    The embedding ACP client routes ``terminal/*`` requests from an agent to
    :meth:`handle`. Terminal ownership is scoped to the ACP session id.
    """

    def __init__(self, manager: SessionManager | None = None) -> None:
        self.manager = manager or SessionManager()
        self._owners: dict[str, str] = {}

    async def handle(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise TypeError("ACP terminal params must be an object")
        if method == "terminal/create":
            return await self.create(params)
        session_id = _required_string(params, "sessionId")
        terminal_id = _required_string(params, "terminalId")
        session = self._owned_terminal(session_id, terminal_id)
        if method == "terminal/output":
            return self.output(session)
        if method == "terminal/wait_for_exit":
            result = await self.manager.wait(terminal_id)
            if result is None:  # An unbounded wait cannot normally return None.
                raise RuntimeError("terminal wait returned without an exit result")
            return _exit_status(result.exit_code)
        if method == "terminal/kill":
            await self.manager.cancel(terminal_id)
            return {}
        if method == "terminal/release":
            try:
                await self.manager.dispose(terminal_id)
            finally:
                self._owners.pop(terminal_id, None)
            return {}
        raise ValueError(f"unsupported ACP terminal method: {method}")

    async def create(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = _required_string(params, "sessionId")
        command = _required_string(params, "command")
        args = params.get("args", [])
        if not isinstance(args, list) or not all(
            isinstance(item, str) for item in args
        ):
            raise TypeError("args must be an array of strings")
        cwd = params.get("cwd")
        if cwd is not None and (
            not isinstance(cwd, str) or not Path(cwd).is_absolute()
        ):
            raise TypeError("cwd must be an absolute path")
        env_items = params.get("env", [])
        if not isinstance(env_items, list):
            raise TypeError("env must be an array")
        env: dict[str, str] = {}
        for item in env_items:
            if not isinstance(item, dict):
                raise TypeError("env entries must be objects")
            name = _required_string(item, "name")
            value = item.get("value")
            if not isinstance(value, str):
                raise TypeError("env values must be strings")
            if name in env:
                raise ValueError(f"duplicate environment variable: {name}")
            env[name] = value
        output_limit = params.get("outputByteLimit")
        if output_limit is not None and (
            isinstance(output_limit, bool)
            or not isinstance(output_limit, int)
            or output_limit < 0
        ):
            raise TypeError("outputByteLimit must be a non-negative integer")

        terminal = await self.manager.start(
            direct_command(
                command,
                tuple(args),
                cwd=cwd,
                env=env or None,
                mode=CommandMode.PTY,
            ),
            max_output_bytes=output_limit,
            output_retention="tail",
        )
        self._owners[terminal.id] = session_id
        return {"terminalId": terminal.id}

    @staticmethod
    def output(session: Session) -> dict[str, Any]:
        value: dict[str, Any] = {
            "output": _decode_tail(bytes(session.stdout)),
            "truncated": session.truncated_output_bytes > 0,
        }
        if session.result is not None:
            value["exitStatus"] = _exit_status(session.result.exit_code)
        return value

    def _owned_terminal(self, session_id: str, terminal_id: str) -> Session:
        if self._owners.get(terminal_id) != session_id:
            raise KeyError("terminal does not belong to this ACP session")
        return self.manager.get(terminal_id)


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value[key]
    if not isinstance(result, str) or not result:
        raise TypeError(f"{key} must be a non-empty string")
    return result


def _decode_tail(value: bytes) -> str:
    """Decode a retained suffix without starting inside a UTF-8 character."""
    for offset in range(min(4, len(value) + 1)):
        try:
            return value[offset:].decode("utf-8")
        except UnicodeDecodeError as err:
            if err.start != 0:
                return value[offset:].decode("utf-8", errors="replace")
    return value.decode("utf-8", errors="replace")


def _exit_status(exit_code: int) -> dict[str, int | str | None]:
    if exit_code >= 0:
        return {"exitCode": exit_code, "signal": None}
    number = -exit_code
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = f"SIG{number}" if os.name != "nt" else str(number)
    return {"exitCode": None, "signal": name}
