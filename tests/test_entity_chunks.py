"""Regression tests for CloudStore.build_entity_chunks.

The chunk set an entity gets embedded under is what search can see. Two
properties are load-bearing and were both broken in production:

  * chunks must cover the entity's *whole* current state — the add path
    replaces embeddings, so any active fact left out becomes unsearchable
    even though its row still exists;
  * fact chunks must stay in `"{name}: {fact}"` form — search_vector recovers
    fact text by splitting the chunk on the first ": " and drops any fact it
    can't match (relevance 0 < the 0.15 floor).

Hermetic: CloudStore is constructed without __init__ and given a stub cursor,
so no database is required.
"""

import contextlib

from cloud.store import CloudStore


class _FakeCursor:
    """Returns a queued result set per execute(), recording the SQL it saw."""

    def __init__(self, results):
        self._results = list(results)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchall(self):
        return self._results.pop(0) if self._results else []


def _store(results):
    store = CloudStore.__new__(CloudStore)
    cursor = _FakeCursor(results)

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cursor

    store._cursor = _cursor
    store._last_cursor = cursor
    return store


class TestBuildEntityChunks:
    def test_name_and_every_active_fact_present(self):
        store = _store([
            [{"content": "uses Rust"}, {"content": "lives in Tokyo"}],
            [],
            [],
        ])
        chunks = store.build_entity_chunks("eid", "Frank")

        assert chunks[0] == "Frank"
        assert "Frank: uses Rust" in chunks
        assert "Frank: lives in Tokyo" in chunks

    def test_fact_chunk_round_trips_through_the_search_split(self):
        """search_vector does chunk.split(": ", 1)[1] to key facts by content."""
        facts = ["uses Rust", "note: ships on Friday", "café: 東京"]
        store = _store([[{"content": f} for f in facts], [], []])

        chunks = store.build_entity_chunks("eid", "Frank")

        recovered = [c.split(": ", 1)[1] for c in chunks if ": " in c]
        for fact in facts:
            assert fact in recovered, f"search could not recover {fact!r}"

    def test_archived_and_expired_facts_are_excluded_in_sql(self):
        store = _store([[], [], []])
        store.build_entity_chunks("eid", "Frank")

        facts_sql = store._last_cursor.sql[0]
        assert "archived = FALSE" in facts_sql
        assert "expires_at IS NULL OR expires_at > NOW()" in facts_sql

    def test_relations_become_triple_chunks(self):
        store = _store([
            [],
            [{"type": "works_at", "target": "Anthropic"},
             {"type": "", "target": "Ignored"}],
            [],
        ])
        chunks = store.build_entity_chunks("eid", "Frank")

        assert "Frank works_at Anthropic" in chunks
        assert not any("Ignored" in c for c in chunks)

    def test_long_knowledge_uses_the_summarizer(self):
        long_text = "x" * 2500
        store = _store([[], [], [{"title": "Notes", "content": long_text}]])

        chunks = store.build_entity_chunks(
            "eid", "Frank", summarize=lambda t: "SUMMARY")

        assert "SUMMARY" in chunks

    def test_long_knowledge_truncates_without_a_summarizer(self):
        store = _store([[], [], [{"title": "Notes", "content": "x" * 2500}]])

        chunks = store.build_entity_chunks("eid", "Frank")

        assert len(chunks[-1]) == 2000

    def test_empty_knowledge_rows_are_skipped(self):
        store = _store([[], [], [{"title": None, "content": None}]])

        assert store.build_entity_chunks("eid", "Frank") == ["Frank"]


class TestEmbeddedChunkLookup:
    def test_dimension_selects_the_matching_vector_column(self):
        store = _store([[("Frank: uses Rust",)]])
        texts = store.get_embedded_chunk_texts("eid", 1024)

        assert texts == {"Frank: uses Rust"}
        assert "embedding_v2 IS NOT NULL" in store._last_cursor.sql[0]

        store = _store([[]])
        store.get_embedded_chunk_texts("eid", 1536)
        sql = store._last_cursor.sql[0]
        assert "embedding IS NOT NULL" in sql and "embedding_v2" not in sql

    def test_deleting_no_texts_touches_nothing(self):
        store = _store([[]])
        store.delete_embeddings_for_texts("eid", [])

        assert store._last_cursor.sql == []
