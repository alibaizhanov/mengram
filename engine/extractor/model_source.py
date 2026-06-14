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
