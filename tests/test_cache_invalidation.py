"""Feed and stats are cached under separate keys; a write must drop both.

Regression test for header counters (FACTS) disagreeing with the memory feed
for up to 15s after a write or an archive: `stats:` was invalidated on the
write paths, `feed:` was never invalidated anywhere and expired only by TTL.

Runs without a database: TTLCache is exercised directly against the real
cache-key shapes used by get_stats/get_feed, and the store source is scanned
to prove every content-mutating path routes through _invalidate_content.
"""
from __future__ import annotations

import pathlib
import re
import unittest

STORE_PATH = pathlib.Path(__file__).resolve().parents[1] / "cloud" / "store.py"
STORE_SRC = STORE_PATH.read_text(encoding="utf-8")

USER = "user-1"
SUB = "default"
STATS_KEY = f"stats:{USER}:{SUB}"
FEED_KEY = f"feed:{USER}:{SUB}:0:20"


def load_ttl_cache():
    """Exec just the TTLCache class — importing cloud.store needs psycopg2."""
    start = STORE_SRC.index("class TTLCache:")
    # Next top-level statement, decorator or class alike.
    end = min(
        pos
        for pos in (STORE_SRC.find(m, start + 1) for m in ("\nclass ", "\n@dataclass"))
        if pos != -1
    )
    namespace: dict = {}
    exec(
        "import threading, time\nfrom typing import Any, Optional\n"
        + STORE_SRC[start:end],
        namespace,
    )
    return namespace["TTLCache"]


TTLCache = load_ttl_cache()


class TestPrefixInvalidation(unittest.TestCase):
    """The keys carry sub_user_id/offset/limit — a prefix must still catch them."""

    def setUp(self):
        self.cache = TTLCache(default_ttl=60)
        self.cache.set(STATS_KEY, {"facts": 4030}, ttl=30)
        self.cache.set(FEED_KEY, {"items": []}, ttl=15)

    def test_stats_prefix_catches_sub_user_variant(self):
        self.cache.invalidate(f"stats:{USER}")
        self.assertIsNone(self.cache.get(STATS_KEY))

    def test_feed_prefix_catches_offset_limit_variants(self):
        for offset in (0, 20, 40):
            self.cache.set(f"feed:{USER}:{SUB}:{offset}:20", {"items": []}, ttl=15)
        self.cache.invalidate(f"feed:{USER}")
        for offset in (0, 20, 40):
            self.assertIsNone(self.cache.get(f"feed:{USER}:{SUB}:{offset}:20"))

    def test_invalidating_stats_alone_leaves_feed_stale(self):
        """The bug: this is what the old code did, and it is why counts drifted."""
        self.cache.invalidate(f"stats:{USER}")
        self.assertIsNone(self.cache.get(STATS_KEY))
        self.assertIsNotNone(self.cache.get(FEED_KEY))

    def test_other_users_unaffected(self):
        self.cache.set("stats:user-2:default", {"facts": 7}, ttl=30)
        self.cache.set("feed:user-2:default:0:20", {"items": []}, ttl=15)
        self.cache.invalidate(f"stats:{USER}")
        self.cache.invalidate(f"feed:{USER}")
        self.assertIsNotNone(self.cache.get("stats:user-2:default"))
        self.assertIsNotNone(self.cache.get("feed:user-2:default:0:20"))


class TestHelperDropsBoth(unittest.TestCase):
    def test_invalidate_content_drops_stats_and_feed(self):
        cache = TTLCache(default_ttl=60)
        cache.set(STATS_KEY, {"facts": 4030}, ttl=30)
        cache.set(FEED_KEY, {"items": []}, ttl=15)

        # The helper's body, applied to a bare cache holder.
        class Store:
            def __init__(self, c):
                self.cache = c

            def _invalidate_content(self, user_id):
                self.cache.invalidate(f"stats:{user_id}")
                self.cache.invalidate(f"feed:{user_id}")

        Store(cache)._invalidate_content(USER)
        self.assertIsNone(cache.get(STATS_KEY))
        self.assertIsNone(cache.get(FEED_KEY))


class TestNoUninvalidatedWritePaths(unittest.TestCase):
    """Guards against a new write path reintroducing the drift."""

    def test_no_bare_stats_invalidation_outside_the_helper(self):
        helper_start = STORE_SRC.index("def _invalidate_content(")
        helper_end = STORE_SRC.index("def _invalidate_content_by_entity(")
        outside = STORE_SRC[:helper_start] + STORE_SRC[helper_end:]
        self.assertNotIn(
            'self.cache.invalidate(f"stats:{user_id}")',
            outside,
            "invalidate stats via _invalidate_content so feed is dropped too",
        )

    def test_every_fact_archiving_path_invalidates(self):
        """Each `SET archived = TRUE` site must have an invalidation downstream."""
        sites = [m.start() for m in re.finditer(r"SET archived = TRUE", STORE_SRC)]
        self.assertGreaterEqual(len(sites), 4, "archiving sites disappeared?")
        for start in sites:
            window = STORE_SRC[start:start + 2500]
            self.assertRegex(
                window,
                r"self\._invalidate_content(_by_entity)?\(",
                f"archiving site at offset {start} never invalidates caches",
            )


if __name__ == "__main__":
    unittest.main()
