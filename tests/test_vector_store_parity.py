"""
Parity tests — Phase 2.

Verifies that SQLiteVectorStore and FAISSVectorStore behave identically
through the BaseVectorStore interface.  All tests are parametrised so
they run against both backends automatically.
"""

import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from engine.vector import VectorStoreFactory
from engine.vector.base import BaseVectorStore, SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKENDS = ["sqlite", "faiss"]
_DIM = 384


def _unit(seed: int) -> np.ndarray:
    """Return a deterministic normalised float32 vector of length _DIM."""
    rng = np.random.default_rng(seed=seed)
    v = rng.random(_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=_BACKENDS)
def raw_backend(request):
    """Provide a fresh, empty backend for each parametrised test."""
    name = request.param
    with tempfile.TemporaryDirectory() as tmp:
        # VectorStoreFactory.create() returns the VectorStore facade; we want
        # the raw backend to test the ABC contract directly.  We instantiate
        # the backend classes directly through the factory's _backends registry.
        backend_class = VectorStoreFactory._backends[name]
        kwargs = {"dimension": _DIM}
        if name == "sqlite":
            kwargs["db_path"] = str(Path(tmp) / "test.db")
        backend: BaseVectorStore = backend_class(**kwargs)
        yield backend
        backend.close()


# ---------------------------------------------------------------------------
# TestParityAddAndSearch
# ---------------------------------------------------------------------------


class TestParityAddAndSearch:

    def test_add_chunk_and_search_finds_it(self, raw_backend):
        """A freshly-added chunk must be retrievable with score ≈ 1.0."""
        emb = np.ones(_DIM, dtype=np.float32) / np.sqrt(_DIM)
        raw_backend.add_chunk("c1", "e1", "Entity", "facts", "content", emb, 0)
        results = raw_backend.search(emb, top_k=1, min_score=0.99)
        assert len(results) == 1
        assert results[0].score == pytest.approx(1.0, abs=1e-5)

    def test_add_chunks_batch_and_search(self, raw_backend):
        """Batch-add must be queryable just like individual add_chunk calls."""
        chunks = [
            {
                "chunk_id": f"c{i}",
                "entity_id": "e1",
                "entity_name": "Entity",
                "section": "facts",
                "content": f"text {i}",
                "embedding": _unit(i),
                "position": i,
            }
            for i in range(5)
        ]
        raw_backend.add_chunks_batch(chunks)
        assert raw_backend.stats()["total_chunks"] == 5

    def test_search_returns_descending_scores(self, raw_backend):
        """Results must be ordered highest-score first."""
        for i in range(10):
            raw_backend.add_chunk(
                f"c{i}", "e1", "Entity", "facts", f"text {i}", _unit(i), i
            )
        q = _unit(5)
        results = raw_backend.search(q, top_k=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_returns_search_result_type(self, raw_backend):
        """Each item in results must be a SearchResult dataclass."""
        emb = _unit(0)
        raw_backend.add_chunk("c1", "e1", "Entity", "facts", "hello", emb, 0)
        results = raw_backend.search(emb, top_k=1)
        assert isinstance(results[0], SearchResult)

    def test_search_result_fields_are_correct(self, raw_backend):
        """SearchResult fields must match what was inserted."""
        emb = _unit(42)
        raw_backend.add_chunk("chunk42", "ent1", "Alpha", "intro", "hello world", emb, 3)
        results = raw_backend.search(emb, top_k=1)
        r = results[0]
        assert r.chunk_id == "chunk42"
        assert r.entity_id == "ent1"
        assert r.entity_name == "Alpha"
        assert r.section == "intro"
        assert r.content == "hello world"

    def test_search_respects_min_score(self, raw_backend):
        """Chunks with score below min_score must not appear in results."""
        # Add 10 random chunks
        for i in range(10):
            raw_backend.add_chunk(
                f"c{i}", "e1", "Entity", "facts", f"text {i}", _unit(i), i
            )
        # Query with a vector that is roughly orthogonal to all stored ones
        # by using a very high threshold that nothing should reach
        q = _unit(999)
        results = raw_backend.search(q, top_k=10, min_score=0.999)
        # With random unit vectors in 384D, cosine ≈ 0, so nothing passes 0.999
        assert len(results) == 0

    def test_empty_store_search_returns_empty(self, raw_backend):
        """Searching an empty store must return an empty list, not raise."""
        results = raw_backend.search(_unit(0), top_k=5)
        assert results == []

    def test_top_k_limits_results(self, raw_backend):
        """Search must never return more than top_k results."""
        for i in range(20):
            raw_backend.add_chunk(
                f"c{i}", "e1", "Entity", "facts", f"text {i}", _unit(i), i
            )
        results = raw_backend.search(_unit(0), top_k=5)
        assert len(results) <= 5

    def test_duplicate_chunk_id_overwrites(self, raw_backend):
        """Inserting the same chunk_id twice must not create a duplicate."""
        emb = _unit(0)
        raw_backend.add_chunk("c1", "e1", "Entity", "facts", "v1", emb, 0)
        raw_backend.add_chunk("c1", "e1", "Entity", "facts", "v2", emb, 0)
        assert raw_backend.stats()["total_chunks"] == 1


# ---------------------------------------------------------------------------
# TestParityEntityOperations
# ---------------------------------------------------------------------------


class TestParityEntityOperations:

    def test_search_by_entity_returns_only_that_entity(self, raw_backend):
        """search_by_entity must filter to the requested entity_id."""
        raw_backend.add_chunk("c1", "e1", "A", "facts", "alpha", _unit(1), 0)
        raw_backend.add_chunk("c2", "e2", "B", "facts", "beta", _unit(2), 0)
        results = raw_backend.search_by_entity("e1")
        assert len(results) == 1
        assert results[0]["entity_id"] == "e1"

    def test_search_by_entity_returns_all_chunks(self, raw_backend):
        """All chunks of an entity must be returned."""
        for i in range(4):
            raw_backend.add_chunk(
                f"c{i}", "e1", "E", "s", f"text {i}", _unit(i), i
            )
        results = raw_backend.search_by_entity("e1")
        assert len(results) == 4

    def test_search_by_entity_unknown_returns_empty(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "A", "facts", "x", _unit(0), 0)
        results = raw_backend.search_by_entity("does_not_exist")
        assert results == []

    def test_get_indexed_entity_names_contains_inserted(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "Entity One", "facts", "text", _unit(0), 0)
        names = raw_backend.get_indexed_entity_names()
        assert "Entity One" in names

    def test_get_indexed_entity_names_empty_store(self, raw_backend):
        assert raw_backend.get_indexed_entity_names() == set()

    def test_get_indexed_entity_names_multiple_entities(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "Alpha", "s", "t", _unit(0), 0)
        raw_backend.add_chunk("c2", "e2", "Beta", "s", "t", _unit(1), 0)
        names = raw_backend.get_indexed_entity_names()
        assert names == {"Alpha", "Beta"}

    def test_delete_entity_removes_all_its_chunks(self, raw_backend):
        for i in range(5):
            raw_backend.add_chunk(
                f"c{i}", "e1", "Entity", "facts", f"text {i}", _unit(i), i
            )
        assert len(raw_backend.search_by_entity("e1")) == 5
        raw_backend.delete_entity("e1")
        assert len(raw_backend.search_by_entity("e1")) == 0

    def test_delete_entity_updates_stats(self, raw_backend):
        for i in range(5):
            raw_backend.add_chunk(
                f"c{i}", "e1", "Entity", "facts", f"text {i}", _unit(i), i
            )
        raw_backend.delete_entity("e1")
        assert raw_backend.stats()["total_chunks"] == 0

    def test_delete_entity_does_not_remove_other_entities(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "A", "s", "t", _unit(0), 0)
        raw_backend.add_chunk("c2", "e2", "B", "s", "t", _unit(1), 0)
        raw_backend.delete_entity("e1")
        assert raw_backend.stats()["total_chunks"] == 1
        assert len(raw_backend.search_by_entity("e2")) == 1

    def test_delete_unknown_entity_is_noop(self, raw_backend):
        """Deleting an entity that doesn't exist must not raise."""
        raw_backend.delete_entity("nonexistent")
        assert raw_backend.stats()["total_chunks"] == 0


# ---------------------------------------------------------------------------
# TestParityStats
# ---------------------------------------------------------------------------


class TestParityStats:

    def test_stats_keys_present(self, raw_backend):
        stats = raw_backend.stats()
        assert "total_chunks" in stats
        assert "total_entities" in stats

    def test_stats_empty_store(self, raw_backend):
        stats = raw_backend.stats()
        assert stats["total_chunks"] == 0
        assert stats["total_entities"] == 0

    def test_stats_after_insert(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "E", "s", "t", _unit(0), 0)
        raw_backend.add_chunk("c2", "e2", "F", "s", "t", _unit(1), 0)
        stats = raw_backend.stats()
        assert stats["total_chunks"] == 2
        assert stats["total_entities"] == 2


# ---------------------------------------------------------------------------
# TestBenchmarkScaffolding — disabled, enable manually
# ---------------------------------------------------------------------------


class TestBenchmarkScaffolding:

    @pytest.mark.benchmark
    def test_indexing_throughput(self, raw_backend):
        """Index 1 000 chunks; must sustain >10 chunks/s."""
        n = 1_000
        chunks = [
            {
                "chunk_id": f"c{i}",
                "entity_id": f"e{i % 50}",
                "entity_name": f"Entity {i % 50}",
                "section": "facts",
                "content": f"text content number {i}",
                "embedding": _unit(i),
                "position": i,
            }
            for i in range(n)
        ]
        t0 = time.perf_counter()
        raw_backend.add_chunks_batch(chunks)
        elapsed = time.perf_counter() - t0
        throughput = n / elapsed
        print(f"\n[{raw_backend.__class__.__name__}] indexing: {throughput:.0f} chunks/s")
        assert throughput > 10, f"Too slow: {throughput:.1f} chunks/s"

    @pytest.mark.benchmark
    def test_search_latency(self, raw_backend):
        """Index 1 000 chunks; 100 queries must average <100 ms each."""
        n = 1_000
        chunks = [
            {
                "chunk_id": f"c{i}",
                "entity_id": f"e{i % 50}",
                "entity_name": f"Entity {i % 50}",
                "section": "facts",
                "content": f"text content number {i}",
                "embedding": _unit(i),
                "position": i,
            }
            for i in range(n)
        ]
        raw_backend.add_chunks_batch(chunks)

        queries = n
        t0 = time.perf_counter()
        for i in range(queries):
            raw_backend.search(_unit(i + n), top_k=5)
        avg_ms = (time.perf_counter() - t0) / queries * 1000
        print(f"\n[{raw_backend.__class__.__name__}] search: {avg_ms:.2f} ms/query")
        assert avg_ms < 100, f"Too slow: {avg_ms:.2f} ms/query"
