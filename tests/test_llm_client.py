"""Tests for FallbackOpenAIClient and AllModelsFailedError."""

from unittest.mock import patch

import logging
import pytest

from engine.extractor.llm_client import (
    AllModelsFailedError,
    FallbackOpenAIClient,
    OpenAIClient,
)


def _make_openai_client(responses_by_model):
    """Return a fake OpenAIClient constructor: responses_by_model maps model -> str or Exception."""

    class FakeClient:
        def __init__(self, api_key, model, provider_sort=""):
            self.model = model
            self.provider_sort = provider_sort

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


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content="ok", choices=None, model=None, provider=None):
        self.choices = [_FakeChoice(content)] if choices is None else choices
        if model is not None:
            self.model = model
        if provider is not None:
            self.provider = provider


class _FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeChatAPI:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAISDK:
    def __init__(self, completions):
        self.chat = _FakeChatAPI(completions)


def _make_openai_client_with_fake_sdk(response=None, provider_sort=""):
    client = OpenAIClient(api_key="key", model="test/model", provider_sort=provider_sort)
    completions = _FakeCompletions(response or _FakeResponse())
    client.client = _FakeOpenAISDK(completions)
    return client, completions


def test_openai_client_complete_without_provider_sort_omits_extra_body():
    client, completions = _make_openai_client_with_fake_sdk()
    assert client.complete("prompt") == "ok"
    assert "extra_body" not in completions.calls[0]


def test_openai_client_complete_with_provider_sort_adds_extra_body():
    client, completions = _make_openai_client_with_fake_sdk(provider_sort="latency")
    client.complete("prompt")
    assert completions.calls[0]["extra_body"] == {"provider": {"sort": "latency"}}


def test_openai_client_chat_with_provider_sort_adds_extra_body():
    client, completions = _make_openai_client_with_fake_sdk(provider_sort="throughput")
    client.chat([{"role": "user", "content": "hi"}])
    assert completions.calls[0]["extra_body"] == {"provider": {"sort": "throughput"}}


def test_openai_client_complete_raises_runtime_error_on_empty_choices():
    client, _ = _make_openai_client_with_fake_sdk(response=_FakeResponse(choices=[]))
    with pytest.raises(RuntimeError, match="returned no choices"):
        client.complete("prompt")


def test_openai_client_complete_raises_runtime_error_on_none_content():
    client, _ = _make_openai_client_with_fake_sdk(response=_FakeResponse(content=None))
    with pytest.raises(RuntimeError, match="returned empty content"):
        client.complete("prompt")


def test_openai_client_complete_logs_model_and_provider(caplog):
    response = _FakeResponse(content="ok", model="google/gemma-4-31b-it-20260402:free", provider="OpenInference")
    client, _ = _make_openai_client_with_fake_sdk(response=response)
    with caplog.at_level(logging.INFO, logger="mengram"):
        client.complete("prompt")
    assert "google/gemma-4-31b-it-20260402:free" in caplog.text
    assert "OpenInference" in caplog.text


def test_openai_client_complete_logs_fallback_provider_when_missing(caplog):
    client, _ = _make_openai_client_with_fake_sdk(response=_FakeResponse(content="ok"))
    with caplog.at_level(logging.INFO, logger="mengram"):
        client.complete("prompt")
    assert "test/model served by ?" in caplog.text


def test_fallback_client_passes_provider_sort_to_each_model():
    fake_cls = _make_openai_client({"model-a": "ok", "model-b": "ok"})
    with patch("engine.extractor.llm_client.OpenAIClient", fake_cls):
        client = FallbackOpenAIClient(api_key="key", models=["model-a", "model-b"], provider_sort="latency")

    assert client._clients[0].provider_sort == "latency"
    assert client._clients[1].provider_sort == "latency"


def test_create_llm_client_openai_passes_provider_sort_to_openai_client():
    client = create_llm_client({
        "provider": "openai",
        "openai": {"api_key": "key", "model": "gpt-4o-mini", "provider_sort": "latency"},
    })

    assert isinstance(client, OpenAIClient)
    assert client.provider_sort == "latency"


def test_create_llm_client_fallback_passes_provider_sort(tmp_path, monkeypatch):
    cache_path = tmp_path / "model-cache.json"
    monkeypatch.setattr(
        "engine.extractor.model_source.DEFAULT_CACHE_PATH", cache_path
    )

    def fetch_fn(url):
        import json as _json
        return _json.dumps({"models": [{"id": "list/model-a"}]}).encode()

    monkeypatch.setattr("engine.extractor.llm_client._default_fetch_fn", fetch_fn)

    client = create_llm_client({
        "provider": "openai",
        "openai": {
            "api_key": "key",
            "model": "fallback/model",
            "model_list_url": "https://example.com/models.json",
            "provider_sort": "throughput",
        },
    })

    assert isinstance(client, FallbackOpenAIClient)
    assert all(c.provider_sort == "throughput" for c in client._clients)
