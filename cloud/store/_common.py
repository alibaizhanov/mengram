"""Module-level helpers shared by the store mixins (formerly the top of cloud/store.py)."""
"""
Mengram Cloud Storage — PostgreSQL backend.

Replaces VaultManager (local .md files) with PostgreSQL + pgvector.
Same interface, different storage.

Usage:
    store = CloudStore(database_url="postgresql://...")
    store.save_entity("PostgreSQL", "technology", facts=[...], relations=[...], knowledge=[...])
    results = store.search("database pool", user_id="...", top_k=5)
"""

import datetime
import hashlib
import json
import logging
import math
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional
from contextlib import contextmanager

from cloud.reliability import annotate_steps, carry_step_history, estimate
from cloud.procedure_match import (  # noqa: F401 — re-exported for callers and tests
    _proc_name_tokens, procedure_similarity, is_near_duplicate_procedure, apply_step_outcome,
)

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger("mengram")

ENTITY_TYPES = frozenset({
    "person", "technology", "company", "project", "concept", "place",
    "activity", "event", "book", "tool", "food", "pet", "game",
    "language", "sport", "organization", "unknown", "service",
    "product", "framework", "platform",
})


def _normalize_fact(f) -> str:
    """Normalize a fact to string. LLM extraction sometimes returns dicts."""
    if isinstance(f, str):
        return f
    logger.warning("Non-string fact from LLM extraction: type=%s value=%r", type(f).__name__, f)
    if isinstance(f, dict):
        # Known keys from common LLM output shapes
        for key in ("fact", "text", "content", "value", "description", "summary"):
            if key in f and isinstance(f[key], str) and f[key]:
                return f[key]
        # Unknown shape — join all string values to preserve data
        parts = [str(v) for v in f.values() if v is not None and str(v)]
        return "; ".join(parts) if parts else str(f)
    return str(f)


def _normalize_step(s) -> str:
    """Normalize a procedure step to string. Steps can be dicts like {"action": "...", "description": "..."}."""
    if isinstance(s, str):
        return s
    if isinstance(s, dict):
        return s.get("action", "") or s.get("step", "") or s.get("description", "") or str(s)
    return str(s)



def _safe_parse_json(raw: str, fallback=None):
    """Parse JSON from LLM output with multiple fallback strategies."""
    clean = raw.strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Strip markdown fences (handles text before ```)
    if "```" in clean:
        start = clean.find("```")
        end = clean.rfind("```")
        if start != end:
            inner = clean[start:end]
            lines = inner.split("\n", 1)
            if len(lines) > 1:
                try:
                    result = json.loads(lines[1])
                    logger.debug("JSON parsed via markdown fence stripping")
                    return result
                except (json.JSONDecodeError, ValueError):
                    pass

    # Strategy 3: Find outermost { } or [ ]
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = clean.find(open_ch)
        end = clean.rfind(close_ch)
        if start >= 0 and end > start:
            try:
                result = json.loads(clean[start:end + 1])
                logger.debug("JSON parsed via bracket extraction")
                return result
            except (json.JSONDecodeError, ValueError):
                pass

    logger.warning(f"⚠️ All JSON parse strategies failed, returning fallback (input length: {len(raw)})")
    return fallback


# ---- TTL Cache (Redis or in-memory) ----
class TTLCache:
    """Thread-safe cache with TTL. Uses Redis if available, falls back to in-memory.
    In-memory fallback has max-size eviction to prevent unbounded growth."""
    MAX_MEMORY_KEYS = 10_000  # Evict oldest entries when exceeded

    def __init__(self, default_ttl: int = 60, redis_url: str = None):
        self._store = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self._redis = None

        if redis_url:
            try:
                import redis as _redis
                self._redis = _redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                logger.info("Redis cache connected")
            except Exception as e:
                logger.warning(f"Redis unavailable, falling back to in-memory cache: {e}")
                self._redis = None

    def get(self, key: str):
        if self._redis:
            try:
                val = self._redis.get(f"mc:{key}")
                return json.loads(val) if val else None
            except Exception:
                pass
        with self._lock:
            item = self._store.get(key)
            if item and item["expires"] > time.time():
                return item["value"]
            if item:
                del self._store[key]
            return None

    def set(self, key: str, value, ttl: int = None):
        ttl = ttl or self.default_ttl
        if self._redis:
            try:
                self._redis.setex(f"mc:{key}", ttl, json.dumps(value, default=str))
                return
            except Exception:
                pass
        with self._lock:
            # Evict expired + oldest if over limit
            if len(self._store) >= self.MAX_MEMORY_KEYS:
                now = time.time()
                # First pass: remove expired
                expired = [k for k, v in self._store.items() if v["expires"] <= now]
                for k in expired:
                    del self._store[k]
                # Second pass: evict oldest 20% if still over limit
                if len(self._store) >= self.MAX_MEMORY_KEYS:
                    sorted_keys = sorted(self._store, key=lambda k: self._store[k]["expires"])
                    for k in sorted_keys[:len(sorted_keys) // 5]:
                        del self._store[k]
            self._store[key] = {
                "value": value,
                "expires": time.time() + ttl
            }

    def invalidate(self, prefix: str = ""):
        if self._redis:
            try:
                # Use SCAN instead of KEYS to avoid blocking Redis
                pattern = "mc:*" if not prefix else f"mc:{prefix}*"
                cursor = 0
                while True:
                    cursor, keys = self._redis.scan(cursor, match=pattern, count=200)
                    if keys:
                        self._redis.delete(*keys)
                    if cursor == 0:
                        break
                return
            except Exception:
                pass
        with self._lock:
            if not prefix:
                self._store.clear()
            else:
                keys = [k for k in self._store if k.startswith(prefix)]
                for k in keys:
                    del self._store[k]

    def stats(self) -> dict:
        if self._redis:
            try:
                info = self._redis.info("keyspace")
                db_keys = 0
                for db_info in info.values():
                    if isinstance(db_info, dict):
                        db_keys += db_info.get("keys", 0)
                return {"total_keys": db_keys, "alive": db_keys, "backend": "redis"}
            except Exception:
                pass
        with self._lock:
            now = time.time()
            alive = sum(1 for v in self._store.values() if v["expires"] > now)
            return {"total_keys": len(self._store), "alive": alive, "backend": "memory"}


@dataclass
class CloudEntity:
    id: str
    name: str
    type: str
    facts: list[str]
    relations: list[dict]
    knowledge: list[dict]
    metadata: dict = None


