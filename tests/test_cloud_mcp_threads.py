"""The cloud MCP server never makes a blocking memory call on the event loop.

In production `mem` is a CloudMemory pointed at 127.0.0.1: every call is an
HTTP request back into the same app. Made on the event loop, two tool calls
landing on the two gunicorn workers at once each wait on the other until the
watchdog kills both (audit 2026-10-23)."""
import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp import types  # noqa: E402

from api.cloud_mcp_server import create_cloud_mcp_server  # noqa: E402


class FakeMem:
    """Records which thread each call ran on."""

    def __init__(self):
        self.threads = {}

    def _note(self, name):
        self.threads[name] = threading.get_ident()

    def get_profile(self, user_id=None):
        self._note("get_profile")
        return {}

    def get_all(self, user_id=None):
        self._note("get_all")
        return []

    def search(self, query, user_id=None, limit=None):
        self._note("search")
        return []

    def get(self, name, user_id=None):
        self._note("get")
        return None


def _server(mem):
    return create_cloud_mcp_server(mem)


def test_tool_calls_run_off_the_event_loop():
    mem = FakeMem()
    server = _server(mem)
    handler = server.request_handlers[types.CallToolRequest]

    async def run():
        loop_thread = threading.get_ident()
        req = types.CallToolRequest(method="tools/call",
                                    params=types.CallToolRequestParams(name="recall",
                                                                       arguments={"query": "x"}))
        await handler(req)
        return loop_thread

    loop_thread = asyncio.run(run())
    assert "search" in mem.threads, "the recall tool did not reach mem.search"
    assert mem.threads["search"] != loop_thread


def test_resource_reads_run_off_the_event_loop():
    mem = FakeMem()
    server = _server(mem)
    handler = server.request_handlers[types.ReadResourceRequest]

    async def run():
        loop_thread = threading.get_ident()
        req = types.ReadResourceRequest(method="resources/read",
                                        params=types.ReadResourceRequestParams(uri="memory://entity/Ali"))
        await handler(req)
        return loop_thread

    loop_thread = asyncio.run(run())
    assert mem.threads["get"] != loop_thread
