"""
LLM Client — LLM calls for knowledge extraction.

Supported providers:
- Anthropic (Claude) — via API
- OpenAI (GPT) — via API
- Ollama — local, free
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional

from engine.extractor.model_source import get_model_candidates

_default_fetch_fn = None

_logger = logging.getLogger("mengram")


class LLMClient(ABC):
    """Abstract LLM client"""

    @abstractmethod
    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        """Send prompt, get response. response_format is provider-specific (OpenAI structured outputs)."""
        pass

    def chat(self, messages: list[dict], system: str = "") -> str:
        """Multi-turn chat. Default: use last user message as prompt."""
        last_user = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user = m["content"]
                break
        return self.complete(last_user, system=system)


class AnthropicClient(LLMClient):
    """Claude via Anthropic API"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=0.2,
            system=system or "You are a knowledge extraction assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def chat(self, messages: list[dict], system: str = "") -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a helpful assistant.",
            messages=messages,
        )
        return response.content[0].text


class OpenAIClient(LLMClient):
    """GPT via OpenAI API"""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", provider_sort: str = ""):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("pip install openai")
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.provider_sort = provider_sort

    def _is_reasoning_model(self) -> bool:
        """gpt-5.x and o1/o3 models accept reasoning_effort and ignore temperature."""
        m = (self.model or "").lower()
        return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3")

    def _finish(self, response):
        if not response.choices:
            raise RuntimeError(f"model {self.model} returned no choices: {response}")
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError(f"model {self.model} returned empty content: {response}")
        _logger.info(
            "model %s served by %s",
            getattr(response, "model", self.model),
            getattr(response, "provider", "?"),
        )
        return content

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system or "You are a knowledge extraction assistant."},
                {"role": "user", "content": prompt},
            ],
        )
        if self._is_reasoning_model():
            kwargs["reasoning_effort"] = "low"  # ~32% cheaper, ~90% entity overlap vs medium
        else:
            kwargs["temperature"] = 0.2
        if response_format:
            kwargs["response_format"] = response_format
        if self.provider_sort:
            kwargs["extra_body"] = {"provider": {"sort": self.provider_sort}}
        response = self.client.chat.completions.create(**kwargs)
        return self._finish(response)

    def chat(self, messages: list[dict], system: str = "") -> str:
        msgs = [{"role": "system", "content": system or "You are a helpful assistant."}]
        msgs.extend(messages)
        kwargs = dict(model=self.model, messages=msgs)
        if self._is_reasoning_model():
            kwargs["reasoning_effort"] = "low"
        if self.provider_sort:
            kwargs["extra_body"] = {"provider": {"sort": self.provider_sort}}
        response = self.client.chat.completions.create(**kwargs)
        return self._finish(response)


class OllamaClient(LLMClient):
    """Ollama — fully local LLM (free)"""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        import urllib.request
        import json

        req_data = {
            "model": self.model,
            "prompt": prompt,
            "system": system or "You are a knowledge extraction assistant.",
            "stream": False,
        }
        if response_format:
            req_data["format"] = "json"
        data = json.dumps(req_data).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result["response"]

    def chat(self, messages: list[dict], system: str = "") -> str:
        import urllib.request
        import json

        msgs = [{"role": "system", "content": system or "You are a helpful assistant."}]
        msgs.extend(messages)
        data = json.dumps({
            "model": self.model,
            "messages": msgs,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result["message"]["content"]


class AllModelsFailedError(Exception):
    """Raised by FallbackOpenAIClient when every candidate model fails."""


class FallbackOpenAIClient(LLMClient):
    """OpenAI-compatible client that tries a list of models in order.

    Used when ~/.mengram/config.yaml sets `model_list_url`: `models` is the
    ordered candidate list from get_model_candidates (curated list, scored
    highest-first, with the configured `model` appended as final fallback).
    """

    def __init__(self, api_key: str, models: list[str], provider_sort: str = ""):
        if not models:
            raise ValueError("FallbackOpenAIClient requires at least one model")
        self.models = models
        self._clients = [
            OpenAIClient(api_key=api_key, model=m, provider_sort=provider_sort) for m in models
        ]

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        errors = []
        for model, client in zip(self.models, self._clients):
            try:
                return client.complete(prompt, system=system, response_format=response_format)
            except Exception as e:
                _logger.warning("model %s failed: %s", model, e)
                errors.append((model, e))
        _logger.error("all models failed: %s", ", ".join(f"{m}: {e}" for m, e in errors))
        raise AllModelsFailedError(f"all models failed: {[m for m, _ in errors]}")

    def chat(self, messages: list[dict], system: str = "") -> str:
        errors = []
        for model, client in zip(self.models, self._clients):
            try:
                return client.chat(messages, system=system)
            except Exception as e:
                _logger.warning("model %s failed: %s", model, e)
                errors.append((model, e))
        _logger.error("all models failed: %s", ", ".join(f"{m}: {e}" for m, e in errors))
        raise AllModelsFailedError(f"all models failed: {[m for m, _ in errors]}")


def create_llm_client(config: dict) -> LLMClient:
    """Creates LLM client from config"""
    provider = config.get("provider", "anthropic")

    if provider == "anthropic":
        settings = config.get("anthropic", {})
        return AnthropicClient(
            api_key=settings["api_key"],
            model=settings.get("model", "claude-sonnet-4-20250514"),
        )
    elif provider == "openai":
        settings = config.get("openai", {})
        api_key = settings["api_key"]
        model = settings.get("model", "gpt-4o-mini")
        provider_sort = (settings.get("provider_sort") or "").strip()
        if (settings.get("model_list_url") or "").strip():
            candidates = get_model_candidates(settings, fetch_fn=_default_fetch_fn)
            return FallbackOpenAIClient(api_key=api_key, models=candidates, provider_sort=provider_sort)
        return OpenAIClient(api_key=api_key, model=model, provider_sort=provider_sort)
    elif provider == "ollama":
        settings = config.get("ollama", {})
        return OllamaClient(
            base_url=settings.get("base_url", "http://localhost:11434"),
            model=settings.get("model", "llama3.2"),
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
