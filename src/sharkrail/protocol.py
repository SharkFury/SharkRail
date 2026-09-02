"""Line-delimited JSON-RPC 2.0 adapter for the SharkRail runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from dataclasses import dataclass
from typing import Any, Optional, TextIO

from . import __version__
from .backends import CancellationPolicy
from .capabilities import Capability, collect
from .errors import ErrorCode, ErrorStage, ExecutionError, SharkRailError
from .models import CommandMode, CommandSpec
from .routing import Shell, Target, WslOptions, direct_command, shell_command
from .sessions import Session, SessionManager


@dataclass(frozen=True)
class JsonRpcError(Exception):
    code: int
    message: str
    data: Optional[dict[str, Any]] = None


class JsonRpcRuntime:
    def __init__(self, manager: Optional[SessionManager] = None) -> None:
        self.manager = manager or SessionManager()

    async def dispatch(self, request: object) -> Optional[dict[str, Any]]:
        request_id: object = None
        try:
            if not isinstance(request, dict):
                raise JsonRpcError(-32600, "Invalid Request")
            request_id = request.get("id")
            if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                raise JsonRpcError(-32600, "Invalid Request")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise JsonRpcError(-32602, "Invalid params")
            result = await self._call(request["method"], params)
            if "id" not in request:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except JsonRpcError as err:
            return self._error_response(request_id, err.code, err.message, err.data)
        except SharkRailError as err:
            return self._error_response(-1 if request_id is None else request_id, -32000, err.error.message, err.error.to_dict())
        except (TypeError, ValueError, KeyError) as err:
            error = ExecutionError(
                code=ErrorCode.INVALID_REQUEST,
                stage=ErrorStage.VALIDATE,
                message=str(err),
            )
            return self._error_response(request_id, -32602, "Invalid params", error.to_dict())
        except Exception as err:  # noqa: BLE001  # pragma: no cover - protocol boundary
            error = ExecutionError(
                code=ErrorCode.INTERNAL_ERROR,
                stage=ErrorStage.RUN,
                message=str(err),
            )
            return self._error_response(request_id, -32603, "Internal error", error.to_dict())

    async def _call(self, method: str, params: dict[str, Any]) -> object:
        if method == "runtime.hello":
            return {
                "runtime": "SharkRail",
                "runtime_version": __version__,
                "protocol_version": "1.0.0",
            }
        if method == "runtime.capabilities":
            return _capability_dict(collect())
        if method == "session.start":
            session = await self.manager.start(
                _parse_spec(params),
                timeout_ms=_optional_int(params, "timeout_ms"),
                max_output_bytes=_optional_int(params, "max_output_bytes"),
            )
            return _session_dict(session)
        if method == "session.get":
            return _session_dict(self.manager.get(_required_str(params, "session_id")))
        if method in {"session.subscribe", "session.events"}:
            session_id = _required_str(params, "session_id")
            cursor = int(params.get("cursor", 0))
            events = await self.manager.events_after(
                session_id,
                cursor=cursor,
                wait_ms=int(params.get("wait_ms", 0)),
            )
            return {
                "events": [_event_dict(event) for event in events],
                "next_cursor": cursor + len(events),
            }
        if method == "session.write":
            await self.manager.write(_required_str(params, "session_id"), _parse_input(params))
            return {"accepted": True}
        if method == "session.close_stdin":
            await self.manager.close_stdin(_required_str(params, "session_id"))
            return {"closed": True}
        if method == "session.resize":
            await self.manager.resize(
                _required_str(params, "session_id"),
                int(params["cols"]),
                int(params["rows"]),
            )
            return {"resized": True}
        if method == "session.interrupt":
            await self.manager.interrupt(_required_str(params, "session_id"))
            return {"interrupted": True}
        if method == "session.cancel":
            steps = await self.manager.cancel(
                _required_str(params, "session_id"),
                CancellationPolicy(
                    interrupt_grace_ms=int(params.get("interrupt_grace_ms", 1000)),
                    terminate_grace_ms=int(params.get("terminate_grace_ms", 1000)),
                    skip_interrupt=bool(params.get("force", False)),
                ),
            )
            return {"steps": steps}
        if method == "session.wait":
            result = await self.manager.wait(
                _required_str(params, "session_id"),
                timeout_ms=_optional_int(params, "wait_timeout_ms"),
            )
            return None if result is None else _result_dict(result)
        if method == "session.dispose":
            await self.manager.dispose(_required_str(params, "session_id"))
            return {"disposed": True}
        raise JsonRpcError(-32601, "Method not found", {"method": method})

    @staticmethod
    def _error_response(
        request_id: object,
        code: int,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


async def serve_stdio(
    runtime: Optional[JsonRpcRuntime] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> None:
    """Serve concurrent newline-delimited JSON-RPC requests until stdin EOF."""
    runtime = runtime or JsonRpcRuntime()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    write_lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    async def respond(line: str) -> None:
        try:
            request = json.loads(line)
            response = await runtime.dispatch(request)
        except json.JSONDecodeError as err:
            response = runtime._error_response(None, -32700, "Parse error", {"message": str(err)})
        if response is not None:
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            async with write_lock:
                stdout.write(encoded + "\n")
                stdout.flush()

    while True:
        line = await asyncio.to_thread(stdin.readline)
        if line == "":
            break
        task = asyncio.create_task(respond(line))
        pending.add(task)
        task.add_done_callback(pending.discard)
    if pending:
        await asyncio.gather(*pending)


def _parse_spec(params: dict[str, Any]) -> CommandSpec:
    spec = params.get("spec", params)
    if not isinstance(spec, dict):
        raise TypeError("spec must be an object")
    argv = spec.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        raise TypeError("argv must be an array of strings")
    env = spec.get("env")
    if env is not None and (
        not isinstance(env, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items())
    ):
        raise TypeError("env must be an object containing string values")
    target = Target(spec.get("target", "native"))
    wsl_data = spec.get("wsl", {})
    if not isinstance(wsl_data, dict):
        raise TypeError("wsl must be an object")
    wsl = WslOptions(
        distribution=wsl_data.get("distribution"),
        user=wsl_data.get("user"),
        cwd=wsl_data.get("cwd"),
    )
    common = {
        "cwd": spec.get("cwd"),
        "env": env,
        "mode": CommandMode(spec.get("mode", "pipe")),
        "target": target,
        "wsl": wsl,
    }
    if "shell" in spec:
        return shell_command(
            Shell(spec["shell"]),
            _required_str(spec, "script"),
            **common,
        )
    return direct_command(_required_str(spec, "executable"), tuple(argv), **common)


def _parse_input(params: dict[str, Any]) -> bytes:
    if "data_base64" in params:
        return base64.b64decode(_required_str(params, "data_base64"), validate=True)
    if "text" in params:
        return _required_str(params, "text").encode("utf-8")
    raise KeyError("text or data_base64 is required")


def _required_str(params: dict[str, Any], key: str) -> str:
    value = params[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _optional_int(params: dict[str, Any], key: str) -> Optional[int]:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _session_dict(session: Session) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "state": session.state.value,
        "pid": session.handle.pid,
        "mode": session.spec.mode.value,
        "next_cursor": len(session.events),
    }


def _event_dict(event: object) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "kind": event.kind.value,
        "payload": event.payload,
    }


def _result_dict(result: object) -> dict[str, Any]:
    return {
        "exit_code": result.exit_code,
        "reason": result.reason.value,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "output_truncated": result.output_truncated,
        "retained_output_bytes": result.retained_output_bytes,
        "truncated_output_bytes": result.truncated_output_bytes,
        "decoding_errors": result.decoding_errors,
        "error": result.error.to_dict() if result.error else None,
    }


def _capability_dict(capability: Capability) -> dict[str, Any]:
    return {
        "contract_version": capability.contract_version,
        "platform": capability.platform_name,
        "modes": capability.modes,
        "process_tree": capability.process_tree,
        "supports_timeout": capability.supports_timeout,
        "max_output_bytes": capability.max_output_bytes,
        "features": capability.features,
        "targets": capability.targets,
        "shells": capability.shells,
    }
