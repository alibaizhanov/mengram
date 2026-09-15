"""A search the hooks make is not charged to the search quota.

54 free accounts had hit the 200-search wall by 2026-09-15 and 5 were paying —
all of them from day one. The wall was the hooks' own auto-recall spending the
month in days, then `quota_exceeded` in the user's context. These tests hold
the two ends of the fix: the hook says who it is, and the server believes only
that exact word.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud import source
from cloud.client import CloudMemory


def _resp(payload):
    class R:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()
    return R()


@patch("urllib.request.urlopen")
def test_a_hook_client_says_so_on_every_request(mock_urlopen):
    mock_urlopen.return_value = _resp({"results": []})
    CloudMemory("om-k", source="hook").search("deploy")
    req = mock_urlopen.call_args[0][0]
    assert req.get_header("X-mengram-source") == "hook"


@patch("urllib.request.urlopen")
def test_an_ordinary_client_sends_no_source(mock_urlopen):
    mock_urlopen.return_value = _resp({"results": []})
    CloudMemory("om-k").search("deploy")
    req = mock_urlopen.call_args[0][0]
    assert req.get_header("X-mengram-source") is None


def test_only_the_exact_word_counts():
    assert source.is_hook({"x-mengram-source": "hook"})
    assert source.is_hook({"X-Mengram-Source": " Hook "})
    assert not source.is_hook({"x-mengram-source": "hooks"})
    assert not source.is_hook({"x-mengram-source": ""})
    assert not source.is_hook({})
    assert not source.is_hook(None)


def test_auto_recall_marks_itself_as_a_hook(monkeypatch):
    import cli
    seen = {}

    class Fake:
        def __init__(self, api_key, base_url=None, source=None):
            seen["source"] = source
        def search(self, *a, **k):
            return []

    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Fake)
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-k")
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": "how did we deploy to railway?"})))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    class Args:
        memory = None; user_id = None; verbose = False
    try:
        cli.cmd_auto_recall(Args())
    except SystemExit:
        pass
    assert seen["source"] == "hook"
