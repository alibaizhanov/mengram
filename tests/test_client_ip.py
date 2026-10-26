"""Per-IP rate limits key on the visitor, not on the proxy in front of us.
Behind Cloudflare and Railway `request.client.host` is the proxy, so every
signup in the world shared one 5-per-minute bucket (audit 2026-10-24)."""
import sys
from pathlib import Path
from types import SimpleNamespace

from starlette.datastructures import Headers

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.client_ip import client_ip


def _req(headers=None, host="10.0.0.1"):
    return SimpleNamespace(headers=Headers(headers or {}),
                           client=SimpleNamespace(host=host) if host else None)


def test_cloudflare_header_wins():
    r = _req({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.1, 10.0.0.2"})
    assert client_ip(r) == "203.0.113.7"


def test_first_forwarded_for_entry_without_cloudflare():
    assert client_ip(_req({"X-Forwarded-For": " 198.51.100.1 , 10.0.0.2"})) == "198.51.100.1"


def test_socket_peer_when_no_proxy_headers():
    assert client_ip(_req()) == "10.0.0.1"
    assert client_ip(_req({"CF-Connecting-IP": "  ", "X-Forwarded-For": ""})) == "10.0.0.1"


def test_unknown_without_anything():
    assert client_ip(_req(host=None)) == "unknown"
    assert client_ip(None) == "unknown"
