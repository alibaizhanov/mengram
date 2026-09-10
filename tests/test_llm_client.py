"""Tests for FallbackOpenAIClient and AllModelsFailedError."""

from unittest.mock import patch

import pytest

from engine.extractor.llm_client import (
    AllModelsFailedError,
    FallbackOpenAIClient,
    OpenAIClient,
)


def _make_openai_client(responses_by_model):
    """Return a fake OpenAIClient constructor: responses_by_model maps model -> str or Exception."""

    class FakeClient:
        def __init__(self, api_key, model):
            self.model = model

        def complete(self, prompt, system="", response_format=None):
            result = responses_by_model[self.model]
            if isinstance(result, Exception):
                raise result
            return result

        def chat(self, messages, system=""):
            result = responses_by_model[self.model]
            if isinstance(result, Exception):
                raise result
            return result

    return FakeClient


def test_fallback_client_requires_at_least_one_model():
    with pytest.raises(ValueError):
        FallbackOpenAIClient(api_key="key", models=[])


def test_fallback_client_uses_first_model_on_success():
    fake_cls = _make_openai_client({"model-a": "ok from a"})
    with patch("engine.extractor.llm_client.OpenAIClient", fake_cls):
        client = FallbackOpenAIClient(api_key="key", models=["model-a"])

    assert client.complete("prompt") == "ok from a"


def test_fallback_client_falls_back_to_second_model_on_first_failure():
    fake_cls = _make_openai_client({
        "model-a": RuntimeError("model-a down"),
        "model-b": "ok from b",
    })
    with patch("engine.extractor.llm_client.OpenAIClient", fake_cls):
        client = FallbackOpenAIClient(api_key="key", models=["model-a", "model-b"])

    assert client.complete("prompt") == "ok from b"


def test_fallback_client_raises_all_models_failed_when_all_fail():
    fake_cls = _make_openai_client({
        "model-a": RuntimeError("model-a down"),
        "model-b": RuntimeError("model-b down"),
    })
    with patch("engine.extractor.llm_client.OpenAIClient", fake_cls):
        client = FallbackOpenAIClient(api_key="key", models=["model-a", "model-b"])

    with pytest.raises(AllModelsFailedError):
        client.complete("prompt")


def test_fallback_client_chat_falls_back_too():
    fake_cls = _make_openai_client({
        "model-a": RuntimeError("model-a down"),
        "model-b": "chat ok from b",
    })
    with patch("engine.extractor.llm_client.OpenAIClient", fake_cls):
        client = FallbackOpenAIClient(api_key="key", models=["model-a", "model-b"])

    assert client.chat([{"role": "user", "content": "hi"}]) == "chat ok from b"


from engine.extractor.llm_client import create_llm_client


def test_create_llm_client_openai_without_model_list_url_returns_openai_client():
    client = create_llm_client({
        "provider": "openai",
        "openai": {"api_key": "key", "model": "gpt-4o-mini"},
    })

    assert isinstance(client, OpenAIClient)
    assert client.model == "gpt-4o-mini"


def test_create_llm_client_openai_with_empty_model_list_url_returns_openai_client():
    client = create_llm_client({
        "provider": "openai",
        "openai": {"api_key": "key", "model": "gpt-4o-mini", "model_list_url": ""},
    })

    assert isinstance(client, OpenAIClient)


def test_create_llm_client_openai_with_model_list_url_returns_fallback_client(tmp_path, monkeypatch):
    cache_path = tmp_path / "model-cache.json"
    monkeypatch.setattr(
        "engine.extractor.model_source.DEFAULT_CACHE_PATH", cache_path
    )

    def fetch_fn(url):
        import json as _json
        return _json.dumps({"models": [{"id": "list/model-a"}, {"id": "list/model-b"}]}).encode()

    monkeypatch.setattr("engine.extractor.llm_client._default_fetch_fn", fetch_fn)

    client = create_llm_client({
        "provider": "openai",
        "openai": {
            "api_key": "key",
            "model": "fallback/model",
            "model_list_url": "https://example.com/models.json",
        },
    })

    assert isinstance(client, FallbackOpenAIClient)
    assert client.models == ["list/model-a", "list/model-b", "fallback/model"]
