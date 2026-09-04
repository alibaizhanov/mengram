"""Tests for cloud MCP activity-feed formatting."""

from api.cloud_mcp_server import _format_activity_feed


def test_activity_feed_renders_fact_field():
    text = _format_activity_feed([
        {
            "entity": "smoochy",
            "fact": "uses speedtest-tracker on port 8003",
            "created_at": "2026-07-30T13:21:56.175549+00:00",
        },
    ])

    assert "- **smoochy** — uses speedtest-tracker on port 8003 (2026-07-30T13:21)" in text
    assert "?" not in text
