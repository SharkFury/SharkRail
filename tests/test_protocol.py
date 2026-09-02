import asyncio
import base64
import io
import sys

from sharkrail.models import CommandSpec
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

        assert waited is not None
        assert waited["result"]["stdout"].splitlines() == ["HELLO"]
        assert events is not None and events["result"]["next_cursor"] > 0
        assert events["result"]["has_more"] is False
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
        error = await runtime.dispatch({"jsonrpc": "2.0", "method": "missing.method"})
        assert error is None

    asyncio.run(_run())


def test_stdio_server_rejects_oversized_request():
    async def _run() -> None:
        source = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"runtime.hello"}\n')
        destination = io.StringIO()
        await serve_stdio(stdin=source, stdout=destination, max_request_bytes=10)
        response = __import__("json").loads(destination.getvalue())
        assert response["error"]["data"]["code"] == "RESOURCE_LIMITED"

    asyncio.run(_run())


def test_stdio_eof_disposes_started_sessions():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        message = (
            '{"jsonrpc":"2.0","id":1,"method":"session.start","params":'
            '{"spec":{"executable":"'
            + sys.executable.replace("\\", "\\\\")
            + '","argv":["-c","import time; time.sleep(10)"]}}}\n'
        )
        destination = io.StringIO()
        await serve_stdio(runtime=runtime, stdin=io.StringIO(message), stdout=destination)
        assert runtime.manager.session_count == 0

    asyncio.run(_run())


def test_stdio_eof_unblocks_pending_session_wait():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        session = await runtime.manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import time; time.sleep(10)"),
            )
        )
        message = request("session.wait", {"session_id": session.id})
        source = io.StringIO(__import__("json").dumps(message) + "\n")

        await asyncio.wait_for(
            serve_stdio(runtime=runtime, stdin=source, stdout=io.StringIO()),
            timeout=2,
        )

        assert runtime.manager.session_count == 0

    asyncio.run(_run())


def test_stdio_server_applies_pending_request_backpressure():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        session = await runtime.manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import time; time.sleep(10)"),
            )
        )
        messages = "\n".join(
            __import__("json").dumps(
                request("session.wait", {"session_id": session.id}, request_id)
            )
            for request_id in (1, 2)
        )
        destination = io.StringIO()

        await serve_stdio(
            runtime=runtime,
            stdin=io.StringIO(messages + "\n"),
            stdout=destination,
            max_pending_requests=1,
        )

        responses = [
            __import__("json").loads(line)
            for line in destination.getvalue().splitlines()
        ]
        assert any(
            response.get("error", {}).get("data", {}).get("code")
            == "RESOURCE_LIMITED"
            for response in responses
        )

    asyncio.run(_run())


def test_protocol_exposes_traces_stats_and_session_inspection():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        started = await runtime.dispatch(
            request(
                "session.start",
                {
                    "trace_id": "trace-protocol",
                    "spec": {"executable": sys.executable, "argv": ["-c", "print('ok')"]},
                },
                41,
            )
        )
        assert started is not None
        session_id = started["result"]["session_id"]
        await runtime.dispatch(request("session.wait", {"session_id": session_id}, 42))
        inspected = await runtime.dispatch(
            request("session.inspect", {"session_id": session_id}, 43)
        )
        listed = await runtime.dispatch(request("session.list", {}, 44))
        stats = await runtime.dispatch(request("runtime.stats", {}, 45))
        events = await runtime.dispatch(
            request("session.subscribe", {"session_id": session_id}, 46)
        )

        assert inspected is not None
        assert inspected["result"]["trace_id"] == "trace-protocol"
        assert inspected["result"]["request_id"] == "41"
        assert listed is not None and listed["result"]["sessions"]
        assert stats is not None and stats["result"]["rpc"]["requests"] >= 5
        assert events is not None
        assert events["result"]["events"][0]["trace_id"] == "trace-protocol"
        assert events["result"]["events"][0]["timestamp"].endswith("+00:00")

    asyncio.run(_run())


def test_protocol_validates_resource_policy():
    async def _run() -> None:
        runtime = JsonRpcRuntime()
        response = await runtime.dispatch(
            request(
                "session.start",
                {
                    "spec": {
                        "executable": sys.executable,
                        "argv": ["-c", "pass"],
                        "resources": {"memory_bytes": 0},
                    }
                },
            )
        )

        assert response is not None
        assert response["error"]["code"] == -32602
        assert "memory_bytes" in response["error"]["data"]["message"]

    asyncio.run(_run())
