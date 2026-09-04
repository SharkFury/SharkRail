import asyncio
import io
import json
import sys

from sharkrail.mcp import MCP_PROTOCOL_VERSION, McpRuntime
from sharkrail.policy import ExecutionPolicy
from sharkrail.protocol import serve_stdio
from sharkrail.sessions import SessionManager


def _request(method: str, params: dict[str, object], request_id: object = 1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


async def _initialize(runtime: McpRuntime) -> None:
    response = await runtime.dispatch(
        _request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tests", "version": "1"},
            },
        )
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    notification = await runtime.dispatch(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert notification is None


def test_mcp_requires_initialization_and_lists_tools():
    async def _run() -> None:
        runtime = McpRuntime()
        early = await runtime.dispatch(_request("tools/list", {}))
        assert early is not None and early["error"]["code"] == -32002

        await _initialize(runtime)
        response = await runtime.dispatch(_request("tools/list", {}, "tools"))
        assert response is not None
        tools = response["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert "sharkrail_run" in names
        assert "sharkrail_session_start" in names
        assert all(tool["inputSchema"]["type"] == "object" for tool in tools)

    asyncio.run(_run())


def test_mcp_rejects_invalid_request_ids():
    async def _run() -> None:
        response = await McpRuntime().dispatch(_request("ping", {}, True))
        assert response is not None and response["error"]["code"] == -32600

    asyncio.run(_run())


def test_mcp_run_returns_structured_output_and_disposes_session():
    async def _run() -> None:
        runtime = McpRuntime()
        await _initialize(runtime)
        response = await runtime.dispatch(
            _request(
                "tools/call",
                {
                    "name": "sharkrail_run",
                    "arguments": {
                        "executable": sys.executable,
                        "args": ["-c", "print('mcp-ok')"],
                    },
                },
            )
        )
        assert response is not None
        result = response["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["stdout"] == "mcp-ok\n"
        assert runtime.manager.session_count == 0

    asyncio.run(_run())


def test_mcp_persistent_session_lifecycle():
    async def _run() -> None:
        runtime = McpRuntime()
        await _initialize(runtime)
        started = await runtime.dispatch(
            _request(
                "tools/call",
                {
                    "name": "sharkrail_session_start",
                    "arguments": {
                        "executable": sys.executable,
                        "args": ["-c", "print(input().upper())"],
                    },
                },
            )
        )
        assert started is not None
        session_id = started["result"]["structuredContent"]["sessionId"]
        written = await runtime.dispatch(
            _request(
                "tools/call",
                {
                    "name": "sharkrail_session_write",
                    "arguments": {"sessionId": session_id, "text": "hello\n"},
                },
                2,
            )
        )
        assert written is not None
        assert written["result"]["structuredContent"]["acceptedBytes"] == 6
        waited = await runtime.dispatch(
            _request(
                "tools/call",
                {
                    "name": "sharkrail_session_wait",
                    "arguments": {"sessionId": session_id, "waitTimeoutMs": 2000},
                },
                3,
            )
        )
        assert waited is not None
        assert waited["result"]["structuredContent"]["result"]["stdout"] == "HELLO\n"
        await runtime.dispatch(
            _request(
                "tools/call",
                {
                    "name": "sharkrail_session_dispose",
                    "arguments": {"sessionId": session_id},
                },
                4,
            )
        )
        assert runtime.manager.session_count == 0

    asyncio.run(_run())


def test_mcp_reports_host_policy_denial_as_tool_error():
    async def _run() -> None:
        manager = SessionManager(
            policy=ExecutionPolicy(allowed_executables=frozenset({"never-allowed"}))
        )
        runtime = McpRuntime(manager)
        await _initialize(runtime)
        response = await runtime.dispatch(
            _request(
                "tools/call",
                {
                    "name": "sharkrail_run",
                    "arguments": {"executable": sys.executable},
                },
            )
        )
        assert response is not None
        result = response["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "POLICY_DENIED"

    asyncio.run(_run())


def test_mcp_stdio_handshake_and_tool_listing():
    async def _run() -> None:
        messages = [
            _request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "stdio-test", "version": "1"},
                },
                1,
            ),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request("tools/list", {}, 2),
        ]
        source = io.StringIO("".join(json.dumps(item) + "\n" for item in messages))
        destination = io.StringIO()
        await serve_stdio(runtime=McpRuntime(), stdin=source, stdout=destination)
        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        assert [response["id"] for response in responses] == [1, 2]
        assert responses[1]["result"]["tools"]

    asyncio.run(_run())
