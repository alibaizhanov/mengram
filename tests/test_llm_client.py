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
