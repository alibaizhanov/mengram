# Mission Copilot — Phase 2 : Parity Tests + FAISS Backend

## Contexte

Phase 1 est TERMINÉE et testée. Nous avons :
- `base.py` — ABC avec `dimension=384`
- `sqlite_store.py` — Backend SQLite refactoré
- `faiss_store.py` — Backend FAISS (Copilot l'a déjà refactoré)
- `__init__.py` — Factory + Façade `VectorStore`
- `brain.py` — `.conn` remplacé par méthodes propres

**Objectif Phase 2 :**
1. Créer les **tests de parité** entre SQLite et FAISS
2. S'assurer que les deux backends produisent les **mêmes résultats**
3. Préparer le **benchmark** (débit + latence)
4. Tout commit dans une **branche séparée** `feature/pluggable-vector-phase2`

---

## Structure du repo

```
engine/vector/
├── __init__.py          # ✅ Factory + Façade (Phase 1)
├── base.py              # ✅ ABC avec dimension=384 (Phase 1)
├── embedder.py          # ✅ all-MiniLM-L6-v2
├── sqlite_store.py      # ✅ Refactoré (Phase 1)
├── faiss_store.py       # ✅ Refactoré par toi (Phase 1)
tests/
├── test_vector_store_parity.py   # ❌ À CRÉER (Phase 2)
```

---

## Ce que tu dois faire

### 1. Créer `tests/test_vector_store_parity.py`

Tests paramétrés avec `@pytest.mark.parametrize` pour tester les deux backends :

```python
import numpy as np, pytest, tempfile
from pathlib import Path
from engine.vector import VectorStoreFactory
from engine.vector.base import BaseVectorStore, SearchResult

_BACKENDS = ["sqlite", "faiss"]

@pytest.fixture(params=_BACKENDS)
def raw_backend(request):
    name = request.param
    with tempfile.TemporaryDirectory() as tmp:
        backend = VectorStoreFactory.create(name, db_path=str(Path(tmp)/"test.db"), dimension=384)
        yield backend
        backend.close()

class TestParityAddAndSearch:
    def test_add_chunk_and_search_finds_it(self, raw_backend):
        # Create fake embedding
        emb = np.ones(384, dtype=np.float32) / np.sqrt(384)
        raw_backend.add_chunk("c1", "e1", "Entity", "facts", "content", emb, 0)
        results = raw_backend.search(emb, top_k=1, min_score=0.99)
        assert len(results) == 1
        assert results[0].score == pytest.approx(1.0, abs=1e-5)

    def test_search_returns_descending_scores(self, raw_backend):
        # Add 10 chunks
        for i in range(10):
            emb = np.random.default_rng(seed=i).random(384).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            raw_backend.add_chunk(f"c{i}", "e1", "Entity", "facts", f"text {i}", emb, i)
        # Search with chunk 5
        q = np.random.default_rng(seed=5).random(384).astype(np.float32)
        q = q / np.linalg.norm(q)
        results = raw_backend.search(q, top_k=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_respects_min_score(self, raw_backend):
        for i in range(10):
            emb = np.random.default_rng(seed=i).random(384).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            raw_backend.add_chunk(f"c{i}", "e1", "Entity", "facts", f"text {i}", emb, i)
        # Orthogonal vector
        q = np.zeros(384, dtype=np.float32)
        q[0] = 1.0
        results = raw_backend.search(q, top_k=10, min_score=0.5)
        assert len(results) == 0

class TestParityEntityOperations:
    def test_search_by_entity(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "A", "facts", "alpha", 
                              np.ones(384)/np.sqrt(384), 0)
        raw_backend.add_chunk("c2", "e2", "B", "facts", "beta",
                              np.ones(384)/np.sqrt(384), 0)
        results = raw_backend.search_by_entity("e1")
        assert len(results) == 1
        assert results[0]["entity_id"] == "e1"

    def test_get_indexed_entity_names(self, raw_backend):
        raw_backend.add_chunk("c1", "e1", "Entity One", "facts", "text",
                              np.ones(384)/np.sqrt(384), 0)
        names = raw_backend.get_indexed_entity_names()
        assert "Entity One" in names

    def test_delete_entity(self, raw_backend):
        for i in range(5):
            emb = np.random.default_rng(seed=i).random(384).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            raw_backend.add_chunk(f"c{i}", "e1", "Entity", "facts", f"text {i}", emb, i)
        assert len(raw_backend.search_by_entity("e1")) == 5
        raw_backend.delete_entity("e1")
        assert len(raw_backend.search_by_entity("e1")) == 0
        assert raw_backend.stats()["total_chunks"] == 0

class TestParityStats:
    def test_stats_format(self, raw_backend):
        stats = raw_backend.stats()
        assert "total_chunks" in stats
        assert "total_entities" in stats
        assert stats["total_chunks"] == 0
        assert stats["total_entities"] == 0
```

### 2. Ajouter le benchmark (scaffolding)

```python
class TestBenchmarkScaffolding:
    @pytest.mark.skip(reason="Benchmark: enable manually")
    def test_indexing_throughput(self, raw_backend):
        import time
        # Generate 1000 chunks
        # Measure time
        # Assert > 10 chunks/s

    @pytest.mark.skip(reason="Benchmark: enable manually")
    def test_search_latency(self, raw_backend):
        import time
        # Index 1000 chunks
        # Measure 100 queries
        # Assert < 100ms/query
```

### 3. Créer la branche git

```bash
git checkout -b feature/pluggable-vector-phase2
git add tests/test_vector_store_parity.py
git commit -m "feat: add parity tests for pluggable vector backends (Phase 2)"
```

### 4. Vérifier que tout passe

```bash
pytest tests/test_vector_store_parity.py -v
```

Attendu : **tous les tests passent** pour `sqlite` et `faiss`.

---

## Critères de validation

- `pytest tests/test_vector_store_parity.py -v` → tous passent
- SQLite et FAISS produisent les mêmes top-k
- Scores identiques (à `pytest.approx(1e-5)` près)
- `get_indexed_entity_names()` et `delete_entity()` fonctionnent sur les deux
- Branche `feature/pluggable-vector-phase2` créée et propre

---

## Notes

- Le FAISS store utilise `IndexFlatIP` (exact) pour garantir la parité
- Les vecteurs sont **pré-normalisés** (all-MiniLM-L6-v2 le fait)
- La suppression FAISS fait un **rebuild complet** de l'index (c'est normal pour `IndexFlatIP`)
- Pas besoin de modifier `brain.py` ni `hybrid_search.py` en Phase 2

Go ! 💎
