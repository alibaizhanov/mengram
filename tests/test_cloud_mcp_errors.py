"""A failed cloud MCP tool call goes out with isError=true.

Returned as plain TextContent, a failure reached the client with isError=false
and the model read "❌ Error: ..." as the tool's answer (audit 2026-10-23)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp import types  # noqa: E402

from api.cloud_mcp_server import create_cloud_mcp_server  # noqa: E402


class FakeMem:
    def search(self, query, user_id=None, limit=None):
        raise RuntimeError("API error 503: upstream down")

    def ask(self, query, user_id=None, max_facts=None):
        raise RuntimeError("API error 500: boom")

    def get_profile(self, user_id=None):
        return {}

    def get_all(self, user_id=None):
        return []

    def get(self, name, user_id=None):
        return None


def _call(server, name, arguments):
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(method="tools/call",
                                params=types.CallToolRequestParams(name=name, arguments=arguments))
    return asyncio.run(handler(req)).root


def test_exception_in_a_tool_is_a_tool_error():
    result = _call(create_cloud_mcp_server(FakeMem()), "recall", {"query": "x"})
    assert result.isError is True
    assert result.content[0].text.startswith("❌ Error: ")
    assert "upstream down" in result.content[0].text


def test_ask_failure_is_a_tool_error():
    result = _call(create_cloud_mcp_server(FakeMem()), "ask", {"query": "x"})
    assert result.isError is True
    assert "❌ Ask failed" in result.content[0].text


def test_filtered_out_tool_is_a_tool_error():
    server = create_cloud_mcp_server(FakeMem(), tool_filter={"remember"})
    result = _call(server, "recall", {"query": "x"})
    assert result.isError is True
    assert "not available on this connector" in result.content[0].text


def test_unknown_tool_is_a_tool_error():
    result = _call(create_cloud_mcp_server(FakeMem()), "no_such_tool", {})
    assert result.isError is True
    assert "Unknown tool: no_such_tool" in result.content[0].text


def test_a_successful_call_is_not_an_error():
    class OkMem(FakeMem):
        def search(self, query, user_id=None, limit=None):
            return []

    result = _call(create_cloud_mcp_server(OkMem()), "recall", {"query": "x"})
    assert result.isError is False
