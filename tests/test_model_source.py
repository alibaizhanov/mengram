"""Tests for engine.extractor.model_source cache helpers."""

import json

from engine.extractor.model_source import _read_cache, _write_cache


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
