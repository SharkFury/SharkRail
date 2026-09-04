"""Model Context Protocol adapter backed by SharkRail sessions."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from . import __version__
from .capabilities import collect
from .errors import SharkRailError
from .models import CommandMode, CommandSpec
from .protocol import _capability_dict, _event_dict, _result_dict
from .routing import direct_command
from .sessions import SessionManager

MCP_PROTOCOL_VERSION = "2025-11-25"


@dataclass(frozen=True)
class McpError(Exception):
    code: int
    message: str
    data: dict[str, Any] | None = None


class McpRuntime:
    """Small dependency-free MCP stdio server for command/session tools."""

    def __init__(self, manager: SessionManager | None = None) -> None:
        self.manager = manager or SessionManager()
        self._initialize_replied = False
        self._initialized = False

    async def dispatch(self, request: object) -> dict[str, Any] | None:
        request_id: object = None
        notification = isinstance(request, dict) and "id" not in request
        try:
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise McpError(-32600, "Invalid Request")
            method = request.get("method")
            if not isinstance(method, str):
                raise McpError(-32600, "Invalid Request")
            request_id = request.get("id")
            if "id" in request and (
                isinstance(request_id, bool)
                or not isinstance(request_id, (str, int, type(None)))
            ):
                raise McpError(-32600, "Invalid Request")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise McpError(-32602, "Invalid params")
            result = await self._call(method, params)
            if notification:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except McpError as err:
            if notification:
                return None
            return self._error_response(request_id, err.code, err.message, err.data)
        except SharkRailError as err:
            if notification:
                return None
            return self._error_response(
                request_id,
                -32000,
                err.error.message,
                err.error.to_dict(),
            )
        except (KeyError, TypeError, ValueError) as err:
            if notification:
                return None
            return self._error_response(
                request_id, -32602, "Invalid params", {"message": str(err)}
            )

    async def _call(self, method: str, params: dict[str, Any]) -> object:
        if method == "initialize":
            if not isinstance(params.get("protocolVersion"), str):
                raise TypeError("protocolVersion must be a string")
            if not isinstance(params.get("capabilities"), dict):
                raise TypeError("capabilities must be an object")
            if not isinstance(params.get("clientInfo"), dict):
                raise TypeError("clientInfo must be an object")
            self._initialize_replied = True
            return {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "sharkrail",
                    "title": "SharkRail",
                    "version": __version__,
                    "description": "Predictable local execution for AI agents",
                    "websiteUrl": "https://github.com/SharkFury/SharkRail",
                },
                "instructions": (
                    "Use direct argv by default. Use PTY only for programs that require "
                    "a terminal. The host execution policy is authoritative."
                ),
            }
        if method == "ping":
            return {}
        if method == "notifications/initialized":
            if not self._initialize_replied:
                raise McpError(-32002, "Initialize request has not completed")
            self._initialized = True
            return None
        if not self._initialized:
            raise McpError(-32002, "Server is not initialized")
        if method == "tools/list":
            return {"tools": _tool_definitions()}
        if method == "tools/call":
            name = _required_string(params, "name")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
            try:
                _validate_tool_arguments(name, arguments)
                return await self._call_tool(name, arguments)
            except SharkRailError as err:
                return _tool_result({"error": err.error.to_dict()}, is_error=True)
            except (KeyError, TypeError, ValueError) as err:
                return _tool_result(
                    {
                        "error": {
                            "code": "INVALID_TOOL_ARGUMENTS",
                            "message": str(err),
                        }
                    },
                    is_error=True,
                )
        raise McpError(-32601, "Method not found", {"method": method})

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "sharkrail_capabilities":
            return _tool_result(_capability_dict(collect()))
        if name == "sharkrail_run":
            session = await self.manager.start(
                _command_spec(arguments),
                timeout_ms=_optional_integer(arguments, "timeoutMs"),
                idle_timeout_ms=_optional_integer(arguments, "idleTimeoutMs"),
                max_output_bytes=_optional_integer(arguments, "maxOutputBytes"),
            )
            try:
                result = await self.manager.wait(session.id)
                if result is None:
                    raise RuntimeError(
                        "unbounded command wait returned without a result"
                    )
                payload = _result_dict(result)
            finally:
                await self.manager.dispose(session.id)
            return _tool_result(payload, is_error=result.exit_code != 0)
        if name == "sharkrail_session_start":
            session = await self.manager.start(
                _command_spec(arguments),
                timeout_ms=_optional_integer(arguments, "timeoutMs"),
                idle_timeout_ms=_optional_integer(arguments, "idleTimeoutMs"),
                max_output_bytes=_optional_integer(arguments, "maxOutputBytes"),
            )
            return _tool_result(
                {
                    "sessionId": session.id,
                    "pid": session.handle.pid,
                    "mode": session.spec.mode.value,
                    "processTree": session.handle.process_tree,
                    "nextCursor": session.next_event_seq,
                }
            )
        if name == "sharkrail_session_read":
            session_id = _required_string(arguments, "sessionId")
            events, next_cursor, has_more = await self.manager.event_page(
                session_id,
                cursor=_integer(arguments, "cursor", 0),
                wait_ms=_integer(arguments, "waitMs", 0),
                limit=_integer(arguments, "limit", 100),
            )
            return _tool_result(
                {
                    "events": [_event_dict(event) for event in events],
                    "nextCursor": next_cursor,
                    "hasMore": has_more,
                }
            )
        if name == "sharkrail_session_write":
            data = _input_bytes(arguments)
            await self.manager.write(_required_string(arguments, "sessionId"), data)
            return _tool_result({"acceptedBytes": len(data)})
        if name == "sharkrail_session_wait":
            result = await self.manager.wait(
                _required_string(arguments, "sessionId"),
                timeout_ms=_optional_integer(arguments, "waitTimeoutMs"),
            )
            return _tool_result(
                {"result": None if result is None else _result_dict(result)}
            )
        if name == "sharkrail_session_cancel":
            steps = await self.manager.cancel(_required_string(arguments, "sessionId"))
            return _tool_result({"steps": steps})
        if name == "sharkrail_session_dispose":
            await self.manager.dispose(_required_string(arguments, "sessionId"))
            return _tool_result({"disposed": True})
        raise McpError(-32602, "Unknown tool", {"name": name})

    @staticmethod
    def _error_response(
        request_id: object,
        code: int,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _command_spec(arguments: dict[str, Any]) -> CommandSpec:
    argv = arguments.get("args", [])
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise TypeError("args must be an array of strings")
    env = arguments.get("env")
    if env is not None and (
        not isinstance(env, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        )
    ):
        raise TypeError("env must be an object containing string values")
    inherit_env = arguments.get("inheritEnv", True)
    if not isinstance(inherit_env, bool):
        raise TypeError("inheritEnv must be a boolean")
    cwd = arguments.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise TypeError("cwd must be a string")
    return direct_command(
        _required_string(arguments, "executable"),
        tuple(argv),
        cwd=cwd,
        env=env,
        inherit_env=inherit_env,
        mode=CommandMode(arguments.get("mode", "pipe")),
    )


def _input_bytes(arguments: dict[str, Any]) -> bytes:
    has_base64 = "dataBase64" in arguments
    has_text = "text" in arguments
    if has_base64 == has_text:
        raise TypeError("exactly one of text or dataBase64 is required")
    if has_base64:
        return base64.b64decode(
            _required_string(arguments, "dataBase64"), validate=True
        )
    return _required_string(arguments, "text").encode("utf-8")


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value[key]
    if not isinstance(result, str) or not result:
        raise TypeError(f"{key} must be a non-empty string")
    return result


def _optional_integer(value: dict[str, Any], key: str) -> int | None:
    result = value.get(key)
    if result is None:
        return None
    if isinstance(result, bool) or not isinstance(result, int):
        raise TypeError(f"{key} must be an integer")
    return result


def _integer(value: dict[str, Any], key: str, default: int) -> int:
    result = value.get(key, default)
    if isinstance(result, bool) or not isinstance(result, int):
        raise TypeError(f"{key} must be an integer")
    return result


def _tool_result(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": value,
        "isError": is_error,
    }


def _tool_definitions() -> list[dict[str, Any]]:
    empty = {"type": "object", "additionalProperties": False}
    command = {
        "type": "object",
        "properties": {
            "executable": {"type": "string", "minLength": 1},
            "args": {"type": "array", "items": {"type": "string"}},
            "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "inheritEnv": {"type": "boolean", "default": True},
            "mode": {"enum": ["pipe", "pty"], "default": "pipe"},
            "timeoutMs": {"type": "integer", "minimum": 0},
            "idleTimeoutMs": {"type": "integer", "minimum": 1},
            "maxOutputBytes": {"type": "integer", "minimum": 0},
        },
        "required": ["executable"],
        "additionalProperties": False,
    }
    session = {
        "type": "object",
        "properties": {"sessionId": {"type": "string", "minLength": 1}},
        "required": ["sessionId"],
        "additionalProperties": False,
    }
    return [
        {
            "name": "sharkrail_capabilities",
            "title": "Inspect SharkRail capabilities",
            "description": "Return runtime-probed execution capabilities and degradation.",
            "inputSchema": empty,
        },
        {
            "name": "sharkrail_run",
            "title": "Run a command",
            "description": "Run direct argv to completion under SharkRail supervision.",
            "inputSchema": command,
        },
        {
            "name": "sharkrail_session_start",
            "title": "Start a command session",
            "description": "Start a persistent pipe or terminal session.",
            "inputSchema": command,
        },
        {
            "name": "sharkrail_session_read",
            "title": "Read session events",
            "description": "Read ordered output and lifecycle events from a cursor.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sessionId": {"type": "string"},
                    "cursor": {"type": "integer", "minimum": 0},
                    "waitMs": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["sessionId"],
                "oneOf": [{"required": ["text"]}, {"required": ["dataBase64"]}],
                "additionalProperties": False,
            },
        },
        {
            "name": "sharkrail_session_write",
            "title": "Write session input",
            "description": "Write UTF-8 text or Base64 bytes to a session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sessionId": {"type": "string"},
                    "text": {"type": "string"},
                    "dataBase64": {"type": "string"},
                },
                "required": ["sessionId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "sharkrail_session_wait",
            "title": "Wait for a session",
            "description": "Wait for completion without changing the running command.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sessionId": {"type": "string"},
                    "waitTimeoutMs": {"type": "integer", "minimum": 0},
                },
                "required": ["sessionId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "sharkrail_session_cancel",
            "title": "Cancel a session",
            "description": "Cancel with bounded interrupt-to-kill escalation.",
            "inputSchema": session,
        },
        {
            "name": "sharkrail_session_dispose",
            "title": "Dispose a session",
            "description": "Release the session and all owned process resources.",
            "inputSchema": session,
        },
    ]


def _validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    definitions = {tool["name"]: tool for tool in _tool_definitions()}
    try:
        schema = definitions[name]["inputSchema"]
    except KeyError as err:
        raise ValueError(f"unknown tool: {name}") from err
    _validate_schema(arguments, schema, path="arguments")


def _validate_schema(value: Any, schema: dict[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise TypeError(f"{path} must be an object")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], path=f"{path}.{key}")
            elif additional is False:
                raise ValueError(f"{path}.{key} is not allowed")
            elif isinstance(additional, dict):
                _validate_schema(item, additional, path=f"{path}.{key}")
        alternatives = schema.get("oneOf")
        if alternatives is not None:
            matches = sum(
                all(key in value for key in option.get("required", []))
                for option in alternatives
            )
            if matches != 1:
                raise ValueError(f"{path} must match exactly one input form")
    elif expected == "array":
        if not isinstance(value, list):
            raise TypeError(f"{path} must be an array")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_schema(item, item_schema, path=f"{path}[{index}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise TypeError(f"{path} must be a string")
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path} is shorter than the minimum length")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{path} must be an integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below the minimum")
    elif expected == "boolean" and not isinstance(value, bool):
        raise TypeError(f"{path} must be a boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
