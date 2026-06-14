"""Tests for engine.extractor.model_source cache helpers."""

import hashlib
import json

from engine.extractor.model_source import _read_cache, _refresh, _write_cache


def test_read_cache_missing_file_returns_none(tmp_path):
    cache_path = tmp_path / "model-cache.json"

    assert _read_cache(cache_path) is None


def test_read_cache_invalid_json_returns_none(tmp_path):
    cache_path = tmp_path / "model-cache.json"
    cache_path.write_text("not json")

    assert _read_cache(cache_path) is None


def test_write_cache_then_read_round_trips(tmp_path):
    cache_path = tmp_path / "model-cache.json"
    data = {
        "url": "https://example.com/models.json",
        "fetched_at": 1000.0,
        "content_hash": "abc123",
        "models": ["a/model", "b/model"],
    }

    _write_cache(cache_path, data)

    assert _read_cache(cache_path) == data
    assert json.loads(cache_path.read_text()) == data


def test_write_cache_creates_parent_directory(tmp_path):
    cache_path = tmp_path / "nested" / "dir" / "model-cache.json"
    data = {"url": "x", "fetched_at": 0.0, "content_hash": "h", "models": []}

    _write_cache(cache_path, data)

    assert cache_path.exists()
    assert _read_cache(cache_path) == data


def _fetch_ok(body: bytes):
    def fetch(url: str) -> bytes:
        return body
    return fetch


def _fetch_raises(exc: Exception):
    def fetch(url: str):
        raise exc
    return fetch


MODELS_JSON = json.dumps({
    "generated_at": "2026-06-14T00:00:00Z",
    "schema_version": 2,
    "models": [
        {"id": "openrouter/owl-alpha", "score": 0.94},
        {"id": "qwen/qwen3-next-80b-a3b-instruct:free", "score": 0.81},
    ],
}).encode()


def test_refresh_parses_models_and_writes_cache(tmp_path):
    cache_path = tmp_path / "model-cache.json"

    models = _refresh(
        url="https://example.com/models.json",
        cache=None,
        now=1000.0,
        cache_path=cache_path,
        fetch_fn=_fetch_ok(MODELS_JSON),
    )

    assert models == ["openrouter/owl-alpha", "qwen/qwen3-next-80b-a3b-instruct:free"]

    cached = _read_cache(cache_path)
    assert cached["url"] == "https://example.com/models.json"
    assert cached["fetched_at"] == 1000.0
    assert cached["models"] == models


def test_refresh_unchanged_content_reuses_cached_models_and_bumps_fetched_at(tmp_path):
    cache_path = tmp_path / "model-cache.json"
    content_hash = hashlib.sha256(MODELS_JSON).hexdigest()
    old_cache = {
        "url": "https://example.com/models.json",
        "fetched_at": 1000.0,
        "content_hash": content_hash,
        "models": ["openrouter/owl-alpha", "qwen/qwen3-next-80b-a3b-instruct:free"],
    }
    _write_cache(cache_path, old_cache)

    models = _refresh(
        url="https://example.com/models.json",
        cache=old_cache,
        now=2000.0,
        cache_path=cache_path,
        fetch_fn=_fetch_ok(MODELS_JSON),
    )

    assert models == old_cache["models"]
    assert _read_cache(cache_path)["fetched_at"] == 2000.0


def test_refresh_fetch_error_with_matching_cache_returns_stale_models(tmp_path):
    cache_path = tmp_path / "model-cache.json"
    old_cache = {
        "url": "https://example.com/models.json",
        "fetched_at": 1000.0,
        "content_hash": "irrelevant",
        "models": ["openrouter/owl-alpha"],
    }
    _write_cache(cache_path, old_cache)

    models = _refresh(
        url="https://example.com/models.json",
        cache=old_cache,
        now=2000.0,
        cache_path=cache_path,
        fetch_fn=_fetch_raises(OSError("network down")),
    )

    assert models == ["openrouter/owl-alpha"]


def test_refresh_fetch_error_with_no_cache_returns_none(tmp_path):
    cache_path = tmp_path / "model-cache.json"

    models = _refresh(
        url="https://example.com/models.json",
        cache=None,
        now=2000.0,
        cache_path=cache_path,
        fetch_fn=_fetch_raises(OSError("network down")),
    )

    assert models is None


def test_refresh_malformed_json_with_no_cache_returns_none(tmp_path):
    cache_path = tmp_path / "model-cache.json"

    models = _refresh(
        url="https://example.com/models.json",
        cache=None,
        now=2000.0,
        cache_path=cache_path,
        fetch_fn=_fetch_ok(b"not json"),
    )

    assert models is None
