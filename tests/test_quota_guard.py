"""Regression tests for CloudStore.check_and_increment.

The upsert has two arms and only one of them used to carry the quota bound.
The ON CONFLICT arm checks `current + count <= max`, but the INSERT arm fires
on the period's first call — when no counter row exists yet — and wrote any
count it was given. A single oversized request on the 1st of the month (a
300-page PDF against a 40-add plan) therefore went through unmetered.

Hermetic: CloudStore is built without __init__ and given a stub cursor.
"""

import contextlib

import pytest

from cloud.store import CloudStore


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class _FakeCache:
    def __init__(self):
        self.invalidated = []

    def invalidate(self, key):
        self.invalidated.append(key)


def _store(rows):
    store = CloudStore.__new__(CloudStore)
    cursor = _FakeCursor(rows)

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cursor

    store._cursor = _cursor
    store._last_cursor = cursor
    store.cache = _FakeCache()
    return store


class TestOversizedCallIsRejected:
    def test_first_call_of_the_period_cannot_exceed_the_whole_allowance(self):
        """No counter row yet (fetchone → None) and count > max."""
        store = _store([None])

        with pytest.raises(ValueError) as exc:
            store.check_and_increment("user-1", "add", max_allowed=40, count=300)

        assert str(exc.value) == "quota_exceeded:add:0:40"

    def test_oversized_call_never_reaches_the_upsert(self):
        store = _store([None])

        with pytest.raises(ValueError):
            store.check_and_increment("user-1", "add", max_allowed=40, count=300)

        assert not any("INSERT INTO usage_counters" in s
                       for s in store._last_cursor.sql), "unbounded INSERT ran"

    def test_error_reports_the_real_current_count(self):
        store = _store([(12,)])

        with pytest.raises(ValueError) as exc:
            store.check_and_increment("user-1", "search", max_allowed=200, count=500)

        assert str(exc.value) == "quota_exceeded:search:12:200"


class TestCallsWithinQuotaStillWork:
    def test_count_equal_to_the_limit_is_allowed(self):
        """The limit is reachable — 40 adds on a 40-add plan must pass."""
        store = _store([(40,)])

        assert store.check_and_increment("user-1", "add", max_allowed=40, count=40) == 40
        assert any("INSERT INTO usage_counters" in s for s in store._last_cursor.sql)

    def test_quota_exceeded_by_existing_usage_still_raises(self):
        """Upsert returns no row (bound failed), then current is read back."""
        store = _store([None, (39,)])

        with pytest.raises(ValueError) as exc:
            store.check_and_increment("user-1", "add", max_allowed=40, count=2)

        assert str(exc.value) == "quota_exceeded:add:39:40"

    def test_unlimited_plan_delegates_without_bounds_checking(self):
        store = _store([])
        calls = []
        store.increment_usage = lambda u, a, c: calls.append((u, a, c)) or 999

        assert store.check_and_increment("user-1", "add", max_allowed=-1, count=300) == 999
        assert calls == [("user-1", "add", 300)]

    def test_unknown_action_is_rejected(self):
        store = _store([])

        with pytest.raises(ValueError, match="Invalid action"):
            store.check_and_increment("user-1", "drop_tables", max_allowed=10)
