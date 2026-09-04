"""Line-delimited JSON-RPC 2.0 adapter for the SharkRail runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol, TextIO, cast

from . import __version__
from .backends import CancellationPolicy
from .capabilities import Capability, collect
from .errors import ErrorCode, ErrorStage, ExecutionError, SharkRailError
from .executor import CommandResult, LifecycleEvent
from .models import CommandMode, CommandSpec, ResourceLimits
from .routing import Shell, Target, WslOptions, direct_command, shell_command
from .schema import protocol_schema
from .sessions import Session, SessionManager

MAX_REQUEST_BYTES = 1024 * 1024
MAX_PENDING_REQUESTS = 256


class StdioRuntime(Protocol):
    manager: SessionManager

    async def dispatch(self, request: object) -> Optional[dict[str, Any]]: ...

    def _error_response(
        self,
        request_id: object,
        code: int,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class JsonRpcError(Exception):
    code: int
    message: str
    data: Optional[dict[str, Any]] = None


class JsonRpcRuntime:
    def __init__(self, manager: Optional[SessionManager] = None) -> None:
        self.manager = manager or SessionManager()
        self._rpc_requests = 0
        self._rpc_errors = 0
        self._rpc_duration_ms = 0.0

    async def dispatch(self, request: object) -> Optional[dict[str, Any]]:
        started = time.monotonic()
        self._rpc_requests += 1
        request_id: object = None
        is_notification = isinstance(request, dict) and "id" not in request
        try:
            if not isinstance(request, dict):
                raise JsonRpcError(-32600, "Invalid Request")
            candidate_id = request.get("id")
            if "id" in request and (
                isinstance(candidate_id, bool)
                or not isinstance(candidate_id, (str, int, type(None)))
            ):
                raise JsonRpcError(-32600, "Invalid Request")
            request_id = candidate_id
            if request.get("jsonrpc") != "2.0" or not isinstance(
                request.get("method"), str
            ):
                raise JsonRpcError(-32600, "Invalid Request")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise JsonRpcError(-32602, "Invalid params")
            result = await self._call(request["method"], params, request_id=request_id)
            if "id" not in request:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except JsonRpcError as err:
            self._rpc_errors += 1
            if is_notification:
                return None
            return self._error_response(request_id, err.code, err.message, err.data)
        except SharkRailError as err:
            self._rpc_errors += 1
            if is_notification:
                return None
            return self._error_response(
                request_id, -32000, err.error.message, err.error.to_dict()
            )
        except (TypeError, ValueError, KeyError) as err:
            self._rpc_errors += 1
            if is_notification:
                return None
            error = ExecutionError(
                code=ErrorCode.INVALID_REQUEST,
                stage=ErrorStage.VALIDATE,
                message=str(err),
            )
            return self._error_response(
                request_id, -32602, "Invalid params", error.to_dict()
            )
        except Exception as err:  # noqa: BLE001  # pragma: no cover - protocol boundary
            self._rpc_errors += 1
            if is_notification:
                return None
            error = ExecutionError(
                code=ErrorCode.INTERNAL_ERROR,
                stage=ErrorStage.RUN,
                message=str(err),
            )
            return self._error_response(
                request_id, -32603, "Internal error", error.to_dict()
            )
        finally:
            self._rpc_duration_ms += (time.monotonic() - started) * 1000

    async def _call(
        self,
        method: str,
        params: dict[str, Any],
        request_id: object = None,
    ) -> object:
        if method == "runtime.hello":
            return {
                "runtime": "SharkRail",
                "runtime_version": __version__,
                "protocol_version": "1.0.0",
            }
        if method == "runtime.capabilities":
            return _capability_dict(collect())
        if method == "runtime.schema":
            return protocol_schema()
        if method == "runtime.stats":
            return {
                **self.manager.stats(),
                "rpc": {
                    "requests": self._rpc_requests,
                    "errors": self._rpc_errors,
                    "average_duration_ms": round(
                        self._rpc_duration_ms / max(1, self._rpc_requests),
                        3,
                    ),
                },
            }
        if method == "runtime.health":
            capability = collect()
            ready = "pipe" in capability.modes
            sessions = cast(dict[str, object], self.manager.stats()["sessions"])
            return {
                "status": "degraded" if capability.degraded_reasons else "ok",
                "live": True,
                "ready": ready,
                "degraded_reasons": capability.degraded_reasons,
                "active_sessions": sessions["active"],
            }
        if method == "session.start":
            trace_id = params.get("trace_id")
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id):
                raise TypeError("trace_id must be a non-empty string")
            session = await self.manager.start(
                _parse_spec(params),
                timeout_ms=_optional_int(params, "timeout_ms"),
                idle_timeout_ms=_optional_int(params, "idle_timeout_ms"),
                max_output_bytes=_optional_int(params, "max_output_bytes"),
                trace_id=trace_id,
                request_id=None if request_id is None else str(request_id),
            )
            return _session_dict(session)
        if method == "session.list":
            return {"sessions": self.manager.list_sessions()}
        if method == "session.inspect":
            return self.manager.inspect(_required_str(params, "session_id"))
        if method == "session.get":
            return _session_dict(self.manager.get(_required_str(params, "session_id")))
        if method in {"session.subscribe", "session.events"}:
            session_id = _required_str(params, "session_id")
            cursor = int(params.get("cursor", 0))
            events, next_cursor, has_more = await self.manager.event_page(
                session_id,
                cursor=cursor,
                wait_ms=int(params.get("wait_ms", 0)),
                limit=int(params.get("limit", 100)),
            )
            return {
                "events": [_event_dict(event) for event in events],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
        if method == "session.write":
            await self.manager.write(
                _required_str(params, "session_id"), _parse_input(params)
            )
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
                    kill_tree_grace_ms=int(params.get("kill_tree_grace_ms", 2000)),
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
    runtime: Optional[StdioRuntime] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    max_request_bytes: int = MAX_REQUEST_BYTES,
    shutdown_timeout_ms: int = 5000,
    max_pending_requests: int = MAX_PENDING_REQUESTS,
) -> None:
    """Serve concurrent newline-delimited JSON-RPC requests until stdin EOF."""
    runtime = runtime or JsonRpcRuntime()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    write_lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    async def write_response(response: dict[str, Any]) -> None:
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        async with write_lock:
            stdout.write(encoded + "\n")
            stdout.flush()

    async def respond(line: str) -> None:
        response: Optional[dict[str, Any]]
        if len(line.encode("utf-8")) > max_request_bytes:
            error = ExecutionError(
                code=ErrorCode.RESOURCE_LIMITED,
                stage=ErrorStage.VALIDATE,
                message=f"request exceeds {max_request_bytes} byte limit",
            )
            response = runtime._error_response(
                None, -32000, error.message, error.to_dict()
            )
            await write_response(response)
            return
        try:
            request = json.loads(line)
            response = await runtime.dispatch(request)
        except json.JSONDecodeError as err:
            response = runtime._error_response(
                None, -32700, "Parse error", {"message": str(err)}
            )
        if response is not None:
            await write_response(response)

    while True:
        line, oversized = await asyncio.to_thread(
            _read_bounded_line,
            stdin,
            max_request_bytes,
        )
        if oversized:
            error = ExecutionError(
                code=ErrorCode.RESOURCE_LIMITED,
                stage=ErrorStage.VALIDATE,
                message=f"request exceeds {max_request_bytes} byte limit",
            )
            await write_response(
                runtime._error_response(None, -32000, error.message, error.to_dict())
            )
            if line == "":
                break
            continue
        if line == "":
            break
        if len(pending) >= max_pending_requests:
            error = ExecutionError(
                code=ErrorCode.RESOURCE_LIMITED,
                stage=ErrorStage.RUN,
                message=f"pending request limit reached ({max_pending_requests})",
                retryable=True,
            )
            await write_response(
                runtime._error_response(None, -32000, error.message, error.to_dict())
            )
            continue
        task = asyncio.create_task(respond(line))
        pending.add(task)
        task.add_done_callback(pending.discard)
    # Let requests accepted immediately before EOF register their sessions,
    # then stop processes before awaiting requests such as session.wait.
    await asyncio.sleep(0)
    await runtime.manager.shutdown()
    if pending:
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                shutdown_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    # A session.start request may have completed after the first snapshot.
    await runtime.manager.shutdown()


def _read_bounded_line(source: TextIO, max_bytes: int) -> tuple[str, bool]:
    """Read one line while bounding retained memory before JSON parsing."""
    if max_bytes <= 0:
        raise ValueError("max_request_bytes must be positive")
    retained: list[str] = []
    retained_bytes = 0
    oversized = False
    while True:
        chunk = source.readline(max_bytes + 1)
        if chunk == "":
            return "" if not retained else "".join(retained), oversized
        encoded_size = len(chunk.encode("utf-8"))
        if not oversized and retained_bytes + encoded_size <= max_bytes:
            retained.append(chunk)
            retained_bytes += encoded_size
        else:
            oversized = True
        if chunk.endswith("\n"):
            line = "".join(retained)
            return (line or "\n") if oversized else line, oversized


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
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        )
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
        "inherit_env": spec.get("inherit_env", True),
        "mode": CommandMode(spec.get("mode", "pipe")),
        "target": target,
        "wsl": wsl,
        "resources": _parse_resources(spec),
    }
    if "shell" in spec:
        return shell_command(
            Shell(spec["shell"]),
            _required_str(spec, "script"),
            **common,
        )
    return direct_command(_required_str(spec, "executable"), tuple(argv), **common)


def _parse_resources(spec: dict[str, Any]) -> ResourceLimits:
    resources = spec.get("resources", {})
    if not isinstance(resources, dict):
        raise TypeError("resources must be an object")
    return ResourceLimits(
        memory_bytes=_optional_int(resources, "memory_bytes"),
        cpu_time_seconds=_optional_int(resources, "cpu_time_seconds"),
        process_count=_optional_int(resources, "process_count"),
    )


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
        "process_tree": session.handle.process_tree,
        "degraded_reasons": session.handle.degraded_reasons,
        "trace_id": session.trace_id,
        "request_id": session.request_id,
        "created_at": session.created_at,
        "first_cursor": session.first_event_seq,
        "next_cursor": session.next_event_seq,
    }


def _event_dict(event: LifecycleEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "kind": event.kind.value,
        "payload": event.payload,
        "timestamp": event.timestamp,
        "monotonic_ns": event.monotonic_ns,
        "trace_id": event.trace_id,
    }


def _result_dict(result: CommandResult) -> dict[str, Any]:
    return {
        "exit_code": result.exit_code,
        "reason": result.reason.value,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_base64": base64.b64encode(result.stdout_bytes).decode("ascii"),
        "stderr_base64": base64.b64encode(result.stderr_bytes).decode("ascii"),
        "output_truncated": result.output_truncated,
        "retained_output_bytes": result.retained_output_bytes,
        "truncated_output_bytes": result.truncated_output_bytes,
        "decoding_errors": result.decoding_errors,
        "error": result.error.to_dict() if result.error else None,
        "duration_ms": result.duration_ms,
        "drain_duration_ms": result.drain_duration_ms,
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
        "degraded_reasons": capability.degraded_reasons,
        "process_tree_fallbacks": capability.process_tree_fallbacks,
        "resource_limits": capability.resource_limits,
        "verification": capability.verification,
    }
