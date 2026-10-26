"""Outbound webhooks: a host that does not resolve is blocked, not waved
through, and a redirect is a failed delivery, not a second request to a URL
nobody vetted (audit 2026-10-24)."""
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store import _webhooks
from cloud.store._webhooks import webhook_target_blocked


def _resolves_to(monkeypatch, *ips):
    def fake(host, port, family=0, type=0, *a, **k):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_unresolvable_host_is_blocked(monkeypatch):
    def fail(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert webhook_target_blocked("https://nowhere.example/hook")


def test_empty_resolution_is_blocked(monkeypatch):
    _resolves_to(monkeypatch)
    assert webhook_target_blocked("https://nowhere.example/hook")


@pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1",
                                "::ffff:10.0.0.5", "0.0.0.0", "224.0.0.1"])
def test_private_addresses_are_blocked(monkeypatch, ip):
    _resolves_to(monkeypatch, ip)
    assert webhook_target_blocked("https://hooks.example/x")


def test_one_private_address_among_public_ones_blocks(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34", "10.0.0.5")
    assert webhook_target_blocked("https://hooks.example/x")


def test_public_address_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert webhook_target_blocked("https://hooks.example/x") is None


def test_internal_names_and_missing_host_are_blocked():
    assert webhook_target_blocked("http://localhost/x")
    assert webhook_target_blocked("http://db.internal/x")
    assert webhook_target_blocked("not a url")


def test_the_webhook_opener_does_not_follow_redirects():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/start", data=b"{}")
        with pytest.raises(urllib.error.HTTPError) as exc:
            _webhooks._WEBHOOK_OPENER.open(req, timeout=5)
        assert exc.value.code == 302
        assert hits == ["/start"]
    finally:
        srv.shutdown()
        srv.server_close()
