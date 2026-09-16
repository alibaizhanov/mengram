"""Raw conversation chunks in /v1/search/all are a fallback, not the meal.

E1b measured five chunks at 78% of the tokens handed to the answer model on a
90-day history; one chunk kept recall of old facts at 1.00 for 45% fewer
tokens; none at all lost it (0.92). So the server default is one, a caller
may ask for up to ten, and zero turns the fallback off. cloud.api connects to
a database on import, so the request field is checked through the source.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.client import CloudMemory

API = (Path(__file__).parent.parent / "cloud" / "api.py").read_text()


def test_server_default_is_one_chunk_with_a_ceiling_of_ten():
    assert re.search(r"^\s+chunks: int = 1$", API, re.M)
    assert "top_k=min(req.chunks, 10)" in API and "req.chunks > 0" in API
    assert "chunks must be between 0 and 10" in API


def test_cache_key_tells_chunk_counts_apart():
    assert ":{req.chunks}'" in API


def test_client_sends_chunks_only_when_asked(monkeypatch):
    seen = {}
    mem = CloudMemory(api_key="om-test")
    monkeypatch.setattr(mem, "_request", lambda method, path, **kw: seen.update(kw) or {})
    mem.search_all("q")
    assert "chunks" not in seen["data"]
    mem.search_all("q", chunks=0)
    assert seen["data"]["chunks"] == 0
