import asyncio

from sharkrail.protocol import JsonRpcRuntime
from sharkrail.schema import protocol_schema


def test_bundled_protocol_schema_has_stable_identity_and_contracts():
    schema = protocol_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("protocol-1.0.json")
    methods = schema["$defs"]["method"]["enum"]
    assert "runtime.schema" in methods
    assert "session.start" in methods
    assert "session.subscribe" in methods
    event_kinds = schema["$defs"]["lifecycleEvent"]["properties"]["kind"]["enum"]
    assert "capability.degraded" in event_kinds
    assert "resource.limit_hit" in event_kinds


def test_runtime_serves_the_bundled_schema():
    async def _run() -> None:
        response = await JsonRpcRuntime().dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "runtime.schema", "params": {}}
        )
        assert response is not None
        assert response["result"]["$id"] == protocol_schema()["$id"]

    asyncio.run(_run())


def test_json_rpc_rejects_non_scalar_request_ids():
    async def _run() -> None:
        response = await JsonRpcRuntime().dispatch(
            {"jsonrpc": "2.0", "id": {"unsafe": True}, "method": "runtime.hello"}
        )
        assert response is not None
        assert response["error"]["code"] == -32600
        assert response["id"] is None

    asyncio.run(_run())
