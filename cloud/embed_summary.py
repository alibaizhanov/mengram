"""Shorten long knowledge text before it is embedded.

Re-embedding an entity re-sends every knowledge item over the limit, so the
same long text was summarized again on every add; results are kept in a small
in-process LRU keyed by a hash of the input.
"""

import hashlib
import logging
import os
import threading
from collections import OrderedDict

logger = logging.getLogger("mengram")

_CACHE_SIZE = 512
_cache: "OrderedDict[str, str]" = OrderedDict()
_cache_lock = threading.Lock()
_client = None
_client_key = None


def _openai_client(api_key: str):
    """One client per process. The SDK defaults (600 s timeout, two retries)
    could hold a request for half an hour; this runs inline in an add."""
    global _client, _client_key
    if _client is None or _client_key != api_key:
        import openai
        _client = openai.OpenAI(api_key=api_key, timeout=30, max_retries=1)
        _client_key = api_key
    return _client


def _is_reasoning_model(model: str) -> bool:
    # Same rule as engine.extractor.llm_client.OpenAIClient._is_reasoning_model
    m = (model or "").lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3")


def summarize_for_embedding(text: str, max_chars: int = 1500) -> str:
    """Summarize long text for embedding. Preserves key facts for search.
    Falls back to truncation on any failure (failures are not cached)."""
    if len(text) <= max_chars:
        return text
    key = hashlib.sha256(f"{max_chars}\0{text}".encode("utf-8", errors="replace")).hexdigest()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    try:
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            return text[:max_chars]
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": f"Summarize this into a dense, fact-rich paragraph under {max_chars} characters. Keep all key facts, names, technologies, and outcomes:\n\n{text[:30000]}"}],
            max_completion_tokens=500,
        )
        if _is_reasoning_model(model):
            # On a reasoning model max_completion_tokens also pays for the
            # hidden reasoning; at the default effort 500 tokens could all go
            # there and the reply came back empty (→ truncation every time).
            kwargs["reasoning_effort"] = "low"
            kwargs["max_completion_tokens"] = 2000
        resp = _openai_client(openai_key).chat.completions.create(**kwargs)
        summary = (resp.choices[0].message.content or "").strip()
        if not summary:
            return text[:max_chars]
    except Exception as e:
        logger.debug(f"Summarize for embedding failed, truncating: {e}")
        return text[:max_chars]
    with _cache_lock:
        _cache[key] = summary
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return summary
