"""OllamaClient sends a real context window, no thinking, and json format when asked."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import engine.extractor.llm_client as llm  # noqa: E402


class _Resp:
    def __init__(self, body): self._b = json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _capture(monkeypatch):
    sent = {}
    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url; sent["body"] = json.loads(req.data.decode()); sent["timeout"] = timeout
        return _Resp({"response": '{"entities": []}', "message": {"content": "hi"}})
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return sent


def test_generate_carries_num_ctx_no_think_and_json_format(monkeypatch):
    sent = _capture(monkeypatch)
    c = llm.OllamaClient(model="llama3.1:8b")
    out = c.complete("extract", response_format={"type": "object"})
    assert out == '{"entities": []}'
    b = sent["body"]
    assert b["model"] == "llama3.1:8b" and b["think"] is False and b["format"] == "json"
    assert b["options"]["num_ctx"] == llm.OllamaClient.DEFAULT_NUM_CTX == 16384
    assert sent["timeout"] == 600.0 and sent["url"].endswith("/api/generate")


def test_num_ctx_comes_from_the_folder_config(monkeypatch):
    sent = _capture(monkeypatch)
    c = llm.create_llm_client({"provider": "ollama", "ollama": {"model": "qwen2.5:7b", "num_ctx": 8192}})
    c.complete("x")
    assert sent["body"]["options"]["num_ctx"] == 8192 and "format" not in sent["body"]


def test_chat_uses_the_same_payload(monkeypatch):
    sent = _capture(monkeypatch)
    llm.OllamaClient(model="m").chat([{"role": "user", "content": "hi"}])
    assert sent["body"]["think"] is False and sent["body"]["options"]["num_ctx"] == 16384 and sent["url"].endswith("/api/chat")
