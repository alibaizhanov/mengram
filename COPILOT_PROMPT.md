# Mission Copilot — Refactor Pluggable Vector Backend (Version 2)

## Contexte

Je prépare une PR pour Mengram (`alibaizhanov/mengram`, Issue #33). Le but : rendre le backend vectoriel local pluggable en unifiant SQLite et FAISS sous une même interface ABC.

**Bonne nouvelle :** `BaseVectorStore` existe DÉJÀ dans `engine/vector/base.py`.  
**Mauvaise nouvelle :** Le `VectorStore` SQLite actuel (`engine/vector/vector_store.py`) ne l'implémente PAS encore. C'est un monolithe séparé. De plus, il y a un bug de dimension (1536 au lieu de 384).

---

## Architecture ACTUELLE du repo

```
engine/vector/
├── __init__.py          # VIDE actuellement
├── base.py              # ✅ BaseVectorStore existe déjà
├── embedder.py          # Embedder (all-MiniLM-L6-v2, 384D)
├── faiss_store.py       # ✅ FAISSVectorStore (hérite de BaseVectorStore)
└── vector_store.py      # ❌ VectorStore monolithique (PAS d'héritage)
```

### `engine/vector/base.py` (EXISTANT)

```python
@dataclass
class SearchResult:
    chunk_id: str
    entity_id: str
    entity_name: str
    section: str
    content: str
    score: float

class BaseVectorStore(ABC):
    def __init__(self, dimension: int = 1536, embedder=None):  # ❌ BUG: 1536 au lieu de 384
        self.dimension = dimension
        self.embedder = embedder

    @abstractmethod
    def add_chunk(self, chunk_id: str, entity_id: str, entity_name: str,
                  section: str, content: str, embedding: np.ndarray,  # embedding externe
                  position: int = 0) -> None: ...

    @abstractmethod
    def add_chunks_batch(self, chunks: List[dict]) -> None: ...  # chunks avec embedding

    @abstractmethod
    def search(self, query_embedding: np.ndarray, top_k: int = 5,  # vector-first
               min_score: float = 0.0) -> List[SearchResult]: ...

    @abstractmethod
    def search_by_entity(self, entity_id: str) -> List[dict]: ...

    @abstractmethod
    def stats(self) -> dict: ...

    @abstractmethod
    def close(self) -> None: ...

    def _validate_embedding(self, embedding: np.ndarray) -> np.ndarray:
        """Normalize vector for cosine similarity"""
        ...
```

### `engine/vector/vector_store.py` (EXISTANT — Monolithe SQLite)

```python
class VectorStore:
    def __init__(self, db_path: str = ":memory:", embedder: Optional[Embedder] = None):
        self.db_path = db_path
        self.embedder = embedder or Embedder()  # Embedder interne
        self.conn = sqlite3.connect(db_path)    # ⚠️ Accès direct par brain.py
        ...

    def add_chunk(self, chunk_id, entity_id, entity_name, section, content, position=0):
        """Génère l'embedding AUTOMATIQUEMENT via self.embedder.embed(content)"""
        vector = self.embedder.embed(content)
        ...

    def add_chunks_batch(self, chunks: list[dict]):
        """Génère les embeddings en batch : self.embedder.embed_batch(texts)"""
        texts = [c["content"] for c in chunks]
        vectors = self.embedder.embed_batch(texts)
        ...

    def search(self, query: str, top_k=5, min_score=0.0) -> list[SearchResult]:
        """TEXT-FIRST : embed la query, puis cherche"""
        query_vec = self.embedder.embed(query)
        ...

    def search_by_entity(self, entity_id: str) -> list[dict]: ...

    def stats(self) -> dict:  # {"total_chunks": int, "total_entities": int}

    def close(self): ...
```

### `engine/vector/faiss_store.py` (EXISTANT — Hérite déjà de BaseVectorStore)

```python
class FAISSVectorStore(BaseVectorStore):
    def __init__(self, dimension: int = 1536, index_type: str = "flat", ...):  # ❌ 1536
        ...

    def add_chunk(self, chunk_id, entity_id, entity_name, section, content,
                  embedding: np.ndarray, position=0):  # embedding externe
        ...

    def add_chunks_batch(self, chunks: List[dict]):  # chunks avec "embedding" pré-calculé
        ...

    def search(self, query_embedding: np.ndarray, top_k=5, min_score=0.0):  # vector-first
        ...
```

### `engine/brain.py` (MengramBrain — Consommateur)

```python
# Ligne ~89: Factory qui n'existe pas encore
from engine.vector import VectorStoreFactory  # ❌ N'EXISTE PAS

self._vector_store = VectorStoreFactory.create(
    backend_type,           # "sqlite" ou "faiss"
    db_path=self._vector_db_path,
    embedder=embedder,
)

# Ligne ~103: Accès direct à .conn (VIOLATION d'encapsulation)
rows = self._vector_store.conn.execute(
    "SELECT DISTINCT entity_name FROM chunks"
).fetchall()
indexed_ids = {r[0] for r in rows}
```

---

## Problèmes à résoudre

### 1. Inconsistance des signatures

| Méthode | SQLite (actuel) | FAISS (actuel) | Brain attend |
|---------|----------------|----------------|--------------|
| `add_chunk` | `(..., position)` — embed auto | `(..., embedding, position)` — embed externe | `(..., position)` — le backend gère |
| `add_chunks_batch` | `chunks` sans embedding | `chunks` AVEC embedding | `chunks` sans embedding |
| `search` | `query: str` — text-first | `query_embedding: np.ndarray` — vector-first | `query: str` — text-first |

**Décision architecturale :** Le backend doit être **text-first** (comme SQLite actuel) car c'est ce que brain.py utilise. L'embedder est injecté dans le `__init__` du backend.

### 2. Bug dimension 1536 → 384

- `BaseVectorStore.__init__` a `dimension: int = 1536` 
- `FAISSVectorStore.__init__` a `dimension: int = 1536`
- Mais l'embedder est `all-MiniLM-L6-v2` qui fait du **384D**
- **Fix :** Mettre `dimension: int = 384` partout

### 3. Accès direct à `.conn` dans brain.py

```python
# Ligne ~103 — À remplacer par une méthode publique
rows = self._vector_store.conn.execute("SELECT DISTINCT entity_name FROM chunks").fetchall()
```

**Solution :** Ajouter `get_indexed_entity_names() -> set[str]` à l'ABC et aux deux backends.

### 4. `VectorStoreFactory` n'existe pas

Brain.py essaie déjà d'importer `VectorStoreFactory` (ligne ~89), mais le fichier n'existe pas.

**Solution :** Créer `VectorStoreFactory` dans `engine/vector/__init__.py`.

---

## Plan de refactoring

### Étape 1 : Corriger `base.py`

- `dimension: int = 384` (au lieu de 1536)
- Ajouter `get_indexed_entity_names() -> set[str]` à l'ABC
- Garder `SearchResult` dataclass inchangé
- Les signatures restent vector-first (embedding externe) pour FAISS, mais on ajoute une **façade** qui gère l'embedding

### Étape 2 : Créer une Façade `VectorStore`

Au lieu de modifier les signatures de `BaseVectorStore`, créer une classe `VectorStore` qui :
- Embarque un `embedder`
- Reçoit `query: str` et convertit en embedding
- Reçoit `chunks` sans embedding, les convertit
- Délègue au backend concret (SQLite ou FAISS)

```python
class VectorStore:
    """Façade publique — text-first, compatible avec brain.py existant"""
    
    def __init__(self, db_path: str = ":memory:", embedder=None, backend: str = "sqlite"):
        self.embedder = embedder or Embedder()
        self._backend = VectorStoreFactory.create(backend, db_path=db_path, embedder=self.embedder)
    
    def add_chunk(self, chunk_id, entity_id, entity_name, section, content, position=0):
        # Génère l'embedding et délègue au backend
        embedding = self.embedder.embed(content)
        self._backend.add_chunk(chunk_id, entity_id, entity_name, section, content, embedding, position)
    
    def add_chunks_batch(self, chunks):
        # Génère les embeddings et délègue
        texts = [c["content"] for c in chunks]
        embeddings = self.embedder.embed_batch(texts)
        for i, chunk in enumerate(chunks):
            chunk["embedding"] = embeddings[i]
        self._backend.add_chunks_batch(chunks)
    
    def search(self, query: str, top_k=5, min_score=0.0):
        query_embedding = self.embedder.embed(query)
        return self._backend.search(query_embedding, top_k, min_score)
    
    def search_by_entity(self, entity_id):
        return self._backend.search_by_entity(entity_id)
    
    def stats(self):
        return self._backend.stats()
    
    def get_indexed_entity_names(self):
        return self._backend.get_indexed_entity_names()
    
    def close(self):
        return self._backend.close()
    
    # Propriété pour backward-compat (brain.py accède à .conn)
    @property
    def conn(self):
        """⚠️ Deprecated — Utiliser get_indexed_entity_names() à la place"""
        if hasattr(self._backend, 'conn'):
            return self._backend.conn
        raise AttributeError("Backend doesn't expose direct DB connection")
```

### Étape 3 : Adapter `sqlite_store.py` (refactor de `vector_store.py`)

- Renommer `VectorStore` → `SQLiteVectorStore`
- Hériter de `BaseVectorStore`
- `add_chunk` prend `embedding` en paramètre (comme FAISS)
- `search` prend `query_embedding` (comme FAISS)
- Ajouter `get_indexed_entity_names()`
- Garder `conn` en propriété publique (pour backward-compat temporaire)

### Étape 4 : Adapter `faiss_store.py`

- Corriger `dimension: int = 384`
- Ajouter `get_indexed_entity_names()`
- Vérifier que toutes les méthodes de l'ABC sont implémentées

### Étape 5 : Créer `VectorStoreFactory` dans `__init__.py`

```python
from engine.vector.base import BaseVectorStore, SearchResult
from engine.vector.sqlite_store import SQLiteVectorStore
from engine.vector.faiss_store import FAISSVectorStore
from engine.vector.embedder import Embedder

class VectorStoreFactory:
    _registry = {}
    
    @classmethod
    def register(cls, name: str, backend: type[BaseVectorStore]):
        cls._registry[name.lower()] = backend
    
    @classmethod
    def create(cls, name: str, **kwargs) -> BaseVectorStore:
        if name.lower() not in cls._registry:
            raise KeyError(f"Backend '{name}' inconnu. Disponibles: {list(cls._registry)}")
        return cls._registry[name.lower()](**kwargs)
    
    @classmethod
    def available(cls):
        return list(cls._registry.keys())

# Auto-register
VectorStoreFactory.register("sqlite", SQLiteVectorStore)
VectorStoreFactory.register("faiss", FAISSVectorStore)

# Façade publique
class VectorStore:
    """Text-first façade — Compatible 100% avec brain.py existant"""
    def __init__(self, db_path: str = ":memory:", embedder=None, backend: str = "sqlite"):
        ...  # Comme défini en Étape 2

__all__ = ["VectorStore", "VectorStoreFactory", "BaseVectorStore", "SearchResult"]
```

### Étape 6 : Adapter `brain.py`

Remplacer l'accès direct à `.conn` (ligne ~103) :

```python
# AVANT:
rows = self._vector_store.conn.execute("SELECT DISTINCT entity_name FROM chunks").fetchall()
indexed_ids = {r[0] for r in rows}

# APRÈS:
indexed_ids = self._vector_store.get_indexed_entity_names()
```

---

## Mission pour Copilot

Génère le code complet pour :

1. **`engine/vector/base.py`** — Corriger dimension=384, ajouter `get_indexed_entity_names()`
2. **`engine/vector/sqlite_store.py`** — Refactor de `vector_store.py` → `SQLiteVectorStore(BaseVectorStore)`
3. **`engine/vector/faiss_store.py`** — Corriger dimension=384, ajouter `get_indexed_entity_names()`
4. **`engine/vector/__init__.py`** — Factory + Façade `VectorStore`
5. **Diff pour `engine/brain.py`** — Remplacer `.conn` par `get_indexed_entity_names()`

### Critères de validation

- `python -c "from engine.vector import VectorStore, VectorStoreFactory; print('OK')"` → OK
- `python -c "from engine.vector import VectorStore; vs = VectorStore(); print(vs.stats())"` → fonctionne
- `python -c "from engine.brain import MengramBrain; b = MengramBrain('./vault'); print(b.get_stats())"` → fonctionne
- Les deux backends (`sqlite`, `faiss`) sont enregistrés dans la factory

**N'oublie pas :** Le code doit être prêt pour une PR. Pas de hack, pas de quick-fix. Propre, testable, backward-compatible.

Merci ! 💎
