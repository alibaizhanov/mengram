"""Fetch and cache the curated free-model fallback list for self-hosted LLM config.

See https://github.com/<user>/mengram-model-list for the models.json schema.
"""

import hashlib
import json
import logging
from pathlib import Path

_logger = logging.getLogger("mengram")

DEFAULT_CACHE_PATH = Path.home() / ".mengram" / "model-cache.json"
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


def _read_cache(cache_path: Path) -> dict | None:
    try:
        return json.loads(cache_path.read_text())
    except (OSError, ValueError):
        return None


def _write_cache(cache_path: Path, data: dict) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data))
    except OSError as e:
        _logger.warning("failed to write model cache to %s: %s", cache_path, e)


def _refresh(
    url: str,
    cache: dict | None,
    now: float,
    cache_path: Path,
    fetch_fn,
) -> list[str] | None:
    try:
        raw = fetch_fn(url)
    except Exception as e:
        _logger.warning("failed to fetch model list from %s: %s", url, e)
        if cache and cache.get("url") == url:
            return cache["models"]
        return None

    content_hash = hashlib.sha256(raw).hexdigest()

    if cache and cache.get("url") == url and cache.get("content_hash") == content_hash:
        models = cache["models"]
    else:
        try:
            data = json.loads(raw)
            models = [m["id"] for m in data["models"]]
        except (ValueError, KeyError, TypeError) as e:
            _logger.warning("failed to parse model list from %s: %s", url, e)
            if cache and cache.get("url") == url:
                return cache["models"]
            return None

    _write_cache(cache_path, {
        "url": url,
        "fetched_at": now,
        "content_hash": content_hash,
        "models": models,
    })
    return models
