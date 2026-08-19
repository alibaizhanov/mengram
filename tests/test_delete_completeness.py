"""Regression tests for deletion completeness.

`DELETE /v1/memories/all` used to remove entities and nothing else, so after a
user was told their memories were deleted, /v1/episodes and /v1/procedures kept
answering and the verbatim conversation text stayed in the database. These
tests pin down what the wipe has to reach, and in what order.

Production tables lack the ON DELETE CASCADE that schema.sql declares, so
child-before-parent ordering is load-bearing rather than cosmetic.

Hermetic: CloudStore is built without __init__ and given a stub cursor.
"""

import contextlib
import re

import pytest

from cloud.store import CloudStore

ROOTS = ("entities", "episodes", "procedures", "conversation_chunks")
ALL_TABLES = ROOTS + (
    "facts", "knowledge", "embeddings", "relations", "memory_triggers",
    "episode_embeddings", "procedure_embeddings", "procedure_evolution",
    "chunk_embeddings",
)


class _FakeCursor:
    """Answers the three query shapes delete_all_memories issues."""

    def __init__(self, present, row_ids):
        self.present = set(present)
        self.row_ids = row_ids          # table -> list of ids it should report
        self.sql = []
        self.rowcount = 1
        self._last = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        if "to_regclass" in flat:
            self._last = [(n,) for n in (params[0] if params else []) if n in self.present]
        elif flat.startswith("SELECT id FROM"):
            table = flat.split()[3]
            self._last = [(i,) for i in self.row_ids.get(table, [])]
        else:
            self._last = []

    def fetchall(self):
        return self._last

    def deletes(self):
        """Tables targeted by DELETE, in the order they were issued."""
        return [m.group(1) for s in self.sql
                if (m := re.match(r"DELETE FROM (\w+)", s))]


class _Cache:
    def invalidate(self, key):
        pass


def _store(present=ALL_TABLES, row_ids=None):
    store = CloudStore.__new__(CloudStore)
    cur = _FakeCursor(present, row_ids if row_ids is not None
                      else {t: ["11111111-1111-1111-1111-111111111111"] for t in ROOTS})

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cur

    store._cursor = _cursor
    store.cache = _Cache()
    store._schedule_matview_refresh = lambda: None
    store._cur = cur
    return store


class TestEverythingUserFacingIsRemoved:
    def test_wipe_reaches_beyond_entities(self):
        """The original bug: only entities were deleted."""
        s = _store()
        s.delete_all_memories("user-1")

        hit = set(s._cur.deletes())
        for table in ("episodes", "procedures", "conversation_chunks", "memory_triggers"):
            assert table in hit, f"{table} survived the wipe"

    def test_verbatim_conversation_text_is_removed(self):
        s = _store()
        s.delete_all_memories("user-1")

        assert "conversation_chunks" in s._cur.deletes()

    def test_every_child_table_is_reached(self):
        s = _store()
        s.delete_all_memories("user-1")

        hit = set(s._cur.deletes())
        for table in ("facts", "knowledge", "embeddings", "relations",
                      "episode_embeddings", "procedure_embeddings", "chunk_embeddings"):
            assert table in hit, f"{table} would be orphaned"

    def test_counts_are_reported_per_table(self):
        s = _store()
        counts = s.delete_all_memories("user-1")

        assert counts["entities"] > 0 and counts["episodes"] > 0


class TestChildBeforeParent:
    """No CASCADE in production — order is what keeps rows from orphaning."""

    @pytest.mark.parametrize("child,parent", [
        ("episode_embeddings", "episodes"),
        ("procedure_embeddings", "procedures"),
        ("procedure_evolution", "procedures"),
        ("chunk_embeddings", "conversation_chunks"),
        ("facts", "entities"),
        ("knowledge", "entities"),
        ("embeddings", "entities"),
        ("relations", "entities"),
    ])
    def test_child_deleted_first(self, child, parent):
        s = _store()
        s.delete_all_memories("user-1")
        order = s._cur.deletes()

        assert order.index(child) < order.index(parent), \
            f"{child} must be deleted before {parent}"


class TestScoping:
    def test_roots_are_scoped_by_user_and_sub_user(self):
        s = _store()
        s.delete_all_memories("user-1", sub_user_id="alice")

        selects = [q for q in s._cur.sql if q.startswith("SELECT id FROM")]
        assert selects, "no root lookup ran"
        for q in selects:
            assert "sub_user_id = %s" in q, f"unscoped root lookup: {q}"

    def test_entities_use_the_uuid_owner_column(self):
        """entities.user_id is uuid; the other roots store it as text."""
        s = _store()
        s.delete_all_memories("user-1")

        ent = next(q for q in s._cur.sql if q.startswith("SELECT id FROM entities"))
        eps = next(q for q in s._cur.sql if q.startswith("SELECT id FROM episodes"))
        assert "WHERE user_id = %s" in ent
        assert "WHERE user_id::text = %s" in eps


class TestMissingTablesDoNotAbortTheWipe:
    """Lazily-created tables are absent on installs that never used them.
    Connections are autocommit, so aborting half-way leaves data destroyed
    and the account intact — the worst of both."""

    def test_absent_table_is_skipped_not_queried(self):
        s = _store(present=[t for t in ALL_TABLES if t != "memory_triggers"])
        s.delete_all_memories("user-1")

        assert "memory_triggers" not in s._cur.deletes()

    def test_the_rest_still_gets_deleted(self):
        s = _store(present=[t for t in ALL_TABLES if t != "chunk_embeddings"])
        counts = s.delete_all_memories("user-1")

        assert "entities" in counts and "episodes" in counts

    def test_nothing_to_delete_is_not_an_error(self):
        s = _store(row_ids={t: [] for t in ROOTS})
        counts = s.delete_all_memories("user-1")

        assert counts.get("entities", 0) == 0
        assert "entities" not in s._cur.deletes()
