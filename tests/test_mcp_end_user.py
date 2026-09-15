"""One MCP connection per end user, scoped by a header.

A product on the OpenAI Agents API opens a connection per customer and names
them in `X-Mengram-User` (or `?user_id=`); every tool call on it is that
customer's. The id is normalised so a stray value cannot become a surprising
sub-user.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.source import end_user_id as _mcp_end_user_id  # noqa: E402


def test_plain_ids_pass_through():
    assert _mcp_end_user_id("cust_1042") == "cust_1042"
    assert _mcp_end_user_id(" user@example.com ") == "user@example.com"
    assert _mcp_end_user_id("tenant:acme+42") == "tenant:acme+42"


def test_missing_or_odd_ids_fall_back_to_default():
    assert _mcp_end_user_id("") == "default"
    assert _mcp_end_user_id(None) == "default"
    assert _mcp_end_user_id("x" * 201) == "default"
    assert _mcp_end_user_id("../etc/passwd") == "default"
    assert _mcp_end_user_id("a b") == "default"
