import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from sharkrail.mcp import McpRuntime
from sharkrail.output import capture_output
from sharkrail.protocol import JsonRpcRuntime

json_scalars = st.none() | st.booleans() | st.integers() | st.text(max_size=64)
json_values = st.recursive(
    json_scalars,
    lambda children: st.lists(children, max_size=5)
    | st.dictionaries(st.text(max_size=32), children, max_size=5),
    max_leaves=20,
)


@given(request=json_values)
@settings(max_examples=300, deadline=None)
def test_json_rpc_boundary_never_leaks_an_exception(request):
    response = asyncio.run(JsonRpcRuntime().dispatch(request))
    assert response is None or response["jsonrpc"] == "2.0"


@given(request=json_values)
@settings(max_examples=300, deadline=None)
def test_mcp_boundary_never_leaks_an_exception(request):
    response = asyncio.run(McpRuntime().dispatch(request))
    assert response is None or response["jsonrpc"] == "2.0"


@given(
    stdout=st.binary(max_size=4096),
    stderr=st.binary(max_size=4096),
    budget=st.integers(min_value=0, max_value=4096),
)
def test_output_budget_accounting_is_conserved(stdout, stderr, budget):
    captured = capture_output(stdout, stderr, budget)

    assert captured.retained_bytes <= budget
    assert captured.retained_bytes + captured.truncated_bytes == len(stdout) + len(stderr)
    assert captured.truncated == (captured.truncated_bytes > 0)
