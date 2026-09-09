"""
LLM Client — LLM calls for knowledge extraction.

Supported providers:
- Anthropic (Claude) — via API
- OpenAI (GPT) — via API
- Ollama — local, free
"""

import json
from abc import ABC, abstractmethod
from typing import Optional


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


def _anthropic_text(response) -> str:
    """The text of a Messages response. Claude 5 models put a ThinkingBlock
    first, so `content[0].text` raises; join the text blocks instead."""
    parts = [getattr(b, "text", "") for b in (getattr(response, "content", None) or [])
             if getattr(b, "type", "") == "text" or (hasattr(b, "text") and not hasattr(b, "thinking"))]
    return "\n".join(p for p in parts if p)


class AnthropicClient(LLMClient):
    """Claude via Anthropic API"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic")
        # A stalled request must fail, not hang an import: the SDK default is
        # 10 min × 3 attempts. One extraction of a 16k-char session takes ~80s.
        self.client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=1)
        self.model = model

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        # No `temperature`: the anthropic SDK dropped it in 1.x (Messages.create()
        # rejects the keyword), and Claude 5 models ignore it anyway.
        response = self.client.messages.create(
            model=self.model,
            # Claude 5 thinks first and the thinking counts against max_tokens:
            # at 4096 the visible JSON was cut mid-object. Give it room.
            max_tokens=16384,
            system=system or "You are a knowledge extraction assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return _anthropic_text(response)

    def chat(self, messages: list[dict], system: str = "") -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=16384,
            system=system or "You are a helpful assistant.",
            messages=messages,
        )
        return _anthropic_text(response)


class OpenAIClient(LLMClient):
    """GPT via OpenAI API"""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("pip install openai")
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _is_reasoning_model(self) -> bool:
        """gpt-5.x and o1/o3 models accept reasoning_effort and ignore temperature."""
        m = (self.model or "").lower()
        return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3")

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
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def chat(self, messages: list[dict], system: str = "") -> str:
        msgs = [{"role": "system", "content": system or "You are a helpful assistant."}]
        msgs.extend(messages)
        kwargs = dict(model=self.model, messages=msgs)
        if self._is_reasoning_model():
            kwargs["reasoning_effort"] = "low"
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content


class OllamaClient(LLMClient):
    """Ollama — fully local LLM (free)"""

    # Ollama's default context window is small (4k on most models); the
    # extraction prompt alone is ~3.6k tokens plus the transcript, so the
    # default would silently truncate the input. 16k fits an 8B model on
    # a 16 GB machine; override with `num_ctx` in the folder config.
    DEFAULT_NUM_CTX = 16384

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2",
                 num_ctx: int = None, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = int(num_ctx or self.DEFAULT_NUM_CTX)
        self.timeout = timeout

    def _payload(self, **fields) -> dict:
        """Common request fields: context size, and no "thinking" — qwen3-style
        models otherwise spend the budget reasoning and return truncated JSON."""
        return {"model": self.model, "stream": False, "think": False,
                "options": {"num_ctx": self.num_ctx, "temperature": 0.2}, **fields}

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        import urllib.request
        import json

        req_data = self._payload(
            prompt=prompt,
            system=system or "You are a knowledge extraction assistant.",
        )
        if response_format:
            req_data["format"] = "json"
        data = json.dumps(req_data).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read())
            return result["response"]

    def chat(self, messages: list[dict], system: str = "") -> str:
        import urllib.request
        import json

        msgs = [{"role": "system", "content": system or "You are a helpful assistant."}]
        msgs.extend(messages)
        data = json.dumps(self._payload(messages=msgs)).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read())
            return result["message"]["content"]


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
        return OpenAIClient(
            api_key=settings["api_key"],
            model=settings.get("model", "gpt-4o-mini"),
        )
    elif provider == "ollama":
        settings = config.get("ollama", {})
        return OllamaClient(
            base_url=settings.get("base_url", "http://localhost:11434"),
            model=settings.get("model", "llama3.2"),
            num_ctx=settings.get("num_ctx"),
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
