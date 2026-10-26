"""Long knowledge text is summarized once per distinct text, with a bounded
client, and on a reasoning model the call leaves room for the reply
(audit 2026-10-24). No network: the OpenAI client is replaced."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud import embed_summary


class _FakeOpenAI:
    instances = []

    def __init__(self, api_key=None, timeout=None, max_retries=None):
        self.kwargs = {"timeout": timeout, "max_retries": max_retries}
        self.calls = []
        self.reply = "dense summary"
        self.fail = False
        _FakeOpenAI.instances.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


@pytest.fixture
def fake(monkeypatch):
    import openai
    _FakeOpenAI.instances = []
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(embed_summary, "_client", None)
    monkeypatch.setattr(embed_summary, "_client_key", None)
    monkeypatch.setattr(embed_summary, "_cache", type(embed_summary._cache)())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.4-mini")
    return _FakeOpenAI


def test_short_text_is_returned_untouched(fake):
    assert embed_summary.summarize_for_embedding("short") == "short"
    assert fake.instances == []


def test_client_is_bounded(fake):
    embed_summary.summarize_for_embedding("x" * 3000)
    (client,) = fake.instances
    assert client.kwargs == {"timeout": 30, "max_retries": 1}


def test_reasoning_model_gets_effort_and_room(fake):
    embed_summary.summarize_for_embedding("x" * 3000)
    (call,) = fake.instances[0].calls
    assert call["reasoning_effort"] == "low"
    assert call["max_completion_tokens"] > 500


def test_non_reasoning_model_is_unchanged(fake, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    embed_summary.summarize_for_embedding("x" * 3000)
    (call,) = fake.instances[0].calls
    assert "reasoning_effort" not in call
    assert call["max_completion_tokens"] == 500


def test_same_text_is_summarized_once(fake):
    text = "fact " * 600
    assert embed_summary.summarize_for_embedding(text) == "dense summary"
    assert embed_summary.summarize_for_embedding(text) == "dense summary"
    assert len(fake.instances[0].calls) == 1
    embed_summary.summarize_for_embedding(text + "more")
    assert len(fake.instances[0].calls) == 2


def test_failures_truncate_and_are_not_cached(fake):
    text = "y" * 3000
    embed_summary.summarize_for_embedding("warm" * 1000)  # create the client
    client = fake.instances[0]
    client.fail = True
    assert embed_summary.summarize_for_embedding(text) == text[:1500]
    client.fail = False
    assert embed_summary.summarize_for_embedding(text) == "dense summary"


def test_cache_is_bounded(fake, monkeypatch):
    monkeypatch.setattr(embed_summary, "_CACHE_SIZE", 2)
    for i in range(4):
        embed_summary.summarize_for_embedding(f"{i}" * 3000)
    assert len(embed_summary._cache) == 2
