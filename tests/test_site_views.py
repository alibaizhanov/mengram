"""A page view leaves one log line, and a crawler's fetch leaves none.

Visits were never counted, so the 2026-09-14 baseline could not say whether the
landing was failing to convert or simply not being seen. The line is the whole
measurement; these tests keep it honest.
"""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
pytest.importorskip("fastapi")   # the site router is part of the API extra
from cloud import site  # noqa: E402


class _Req:
    def __init__(self, ua, ref="", method="GET"):
        self.headers = {"user-agent": ua, "referer": ref}
        self.method = method


def test_a_human_visit_is_logged_with_referrer_and_device(caplog):
    with caplog.at_level(logging.INFO, logger="mengram"):
        site._note_view(_Req("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1",
                             "https://github.com/alibaizhanov/mengram"), "/")
    assert "🌐 VIEW / | ref=github.com | ua=mac" in caplog.text


def test_direct_and_internal_referrers_are_named(caplog):
    with caplog.at_level(logging.INFO, logger="mengram"):
        site._note_view(_Req("Mozilla/5.0 (Windows NT 10.0) Chrome/120"), "/pricing")
        site._note_view(_Req("Mozilla/5.0 (Linux; Android 14) Mobile", "https://mengram.io/blog/x"), "/pricing")
    assert "VIEW /pricing | ref=direct | ua=win" in caplog.text
    assert "VIEW /pricing | ref=internal | ua=mobile" in caplog.text


def test_crawlers_monitors_and_head_requests_are_not_visits(caplog):
    with caplog.at_level(logging.INFO, logger="mengram"):
        site._note_view(_Req("Mozilla/5.0 (compatible; Googlebot/2.1)"), "/")
        site._note_view(_Req("curl/8.4.0"), "/")
        site._note_view(_Req("Better Uptime Bot"), "/")
        site._note_view(_Req("Mozilla/5.0 (Macintosh)", method="HEAD"), "/")
        site._note_view(_Req(""), "/")
    assert "VIEW" not in caplog.text
