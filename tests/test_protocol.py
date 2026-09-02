import asyncio
import base64
import io
import sys

from sharkrail.protocol import JsonRpcRuntime, serve_stdio


def request(method: str, params: dict[str, object], request_id: int = 1) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def test_protocol_hello_and_method_not_found():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        hello = await runtime.dispatch(request("runtime.hello", {}))
        missing = await runtime.dispatch(request("missing.method", {}))
        assert hello is not None and hello["result"]["runtime"] == "SharkRail"
        assert missing is not None and missing["error"]["code"] == -32601

    asyncio.run(_run())


def test_protocol_session_full_pipe_lifecycle():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        started = await runtime.dispatch(
            request(
                "session.start",
                {
                    "spec": {
                        "executable": sys.executable,
                        "argv": ["-c", "import sys; print(sys.stdin.readline().upper(), end='')"],
                    }
                },
            )
        )
        assert started is not None
        session_id = started["result"]["session_id"]
        encoded = base64.b64encode(b"hello\n").decode()
        await runtime.dispatch(request("session.write", {"session_id": session_id, "data_base64": encoded}, 2))
        await runtime.dispatch(request("session.close_stdin", {"session_id": session_id}, 3))
        waited = await runtime.dispatch(request("session.wait", {"session_id": session_id}, 4))
        events = await runtime.dispatch(request("session.subscribe", {"session_id": session_id}, 5))

        assert waited is not None and waited["result"]["stdout"] == "HELLO\n"
        assert events is not None and events["result"]["next_cursor"] > 0
        assert events["result"]["events"][-1]["kind"] == "session.completed"

    asyncio.run(_run())


def test_protocol_returns_structured_errors():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        invalid = await runtime.dispatch(request("session.start", {"spec": {"argv": []}}))
        missing = await runtime.dispatch(
            request("session.start", {"spec": {"executable": "__missing__", "argv": []}}, 2)
        )
        assert invalid is not None and invalid["error"]["code"] == -32602
        assert missing is not None
        assert missing["error"]["data"]["code"] == "EXECUTABLE_NOT_FOUND"

    asyncio.run(_run())


def test_protocol_notifications_have_no_response():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        response = await runtime.dispatch({"jsonrpc": "2.0", "method": "runtime.hello"})
        assert response is None

    asyncio.run(_run())


def test_stdio_server_rejects_oversized_request():
    async def _run() -> None:
        source = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"runtime.hello"}\n')
        destination = io.StringIO()
        await serve_stdio(stdin=source, stdout=destination, max_request_bytes=10)
        response = __import__("json").loads(destination.getvalue())
        assert response["error"]["data"]["code"] == "RESOURCE_LIMITED"

    asyncio.run(_run())
