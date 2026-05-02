"""
Vector Store — Pluggable backends for semantic search.

Usage:
    from engine.vector import VectorStoreFactory, VectorStore

    # SQLite (default, good for <10K chunks) — text-first facade
    store = VectorStoreFactory.create("sqlite", db_path="./vectors.db")
    store.add_chunks_batch([{"chunk_id": "a:0", ...}])   # no embedding needed
    results = store.search("my query")                    # text in, SearchResult out

Backends:
    - sqlite: SQLite with in-memory caching (default)
    - sqlite_vec: sqlite-vec extension (Phase 2, optional)
    - faiss: Facebook AI Similarity Search (Phase 2, optional)

Exception contract:
    VectorStoreFactory.create() raises:
    - ValueError:   backend name is completely unknown
    - ImportError:  backend is known but its optional dependency is not installed
"""

import inspect
import os
from typing import Optional, List

import numpy as np

from engine.vector.base import BaseVectorStore, SearchResult
from engine.vector.sqlite_store import SQLiteVectorStore

# Phase 2: Optional backends — loaded lazily via env var
_OPTIONAL_BACKENDS = {
    "sqlite_vec": ("engine.vector.sqlite_vec_store", "SQLiteVecVectorStore", "sqlite-vec"),
    "faiss": ("engine.vector.faiss_store", "FAISSVectorStore", "faiss-cpu"),
}

class VectorStore:
    """
    Text-first facade over any BaseVectorStore backend.

    Brain always interacts with this class — it never calls backends directly.
    Responsibilities:
      - Accept raw text, generate embeddings via the injected Embedder
      - Delegate vector operations to the concrete backend
      - Expose entity-level helpers (delete_entity, get_indexed_entity_names)
    """

    def __init__(self, backend: BaseVectorStore, embedder):
        """
        Args:
            backend: Concrete BaseVectorStore implementation
            embedder: Embedder instance (e.g. all-MiniLM-L6-v2, 384D)
        """
        self._backend = backend
        self._embedder = embedder

    # ------------------------------------------------------------------
    # Write operations (text-first)
    # ------------------------------------------------------------------

    def add_chunk(self, chunk_id: str, entity_id: str, entity_name: str,
                  section: str, content: str, position: int = 0) -> None:
        """Index a single chunk — embedding is generated automatically."""
        embedding = self._embedder.embed(content)
        self._backend.add_chunk(
            chunk_id, entity_id, entity_name, section, content,
            embedding, position,
        )

    def add_chunks_batch(self, chunks: List[dict]) -> None:
        """
        Batch-index chunks — embeddings are generated automatically.

        chunks: List of dicts with keys:
            chunk_id, entity_id, entity_name, section, content, position (opt.)
        No ``embedding`` key should be present; it is computed here.
        """
        if not chunks:
            return
        texts = [c["content"] for c in chunks]
        embeddings = self._embedder.embed_batch(texts)
        enriched = [
            {**c, "embedding": embeddings[i]}
            for i, c in enumerate(chunks)
        ]
        self._backend.add_chunks_batch(enriched)

    def delete_entity(self, entity_id: str) -> None:
        """Remove all chunks for an entity from the backend."""
        self._backend.delete_entity(entity_id)

    # ------------------------------------------------------------------
    # Read operations (text-first)
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.0) -> List[SearchResult]:
        """Semantic search — accepts raw text, returns SearchResult list."""
        query_embedding = self._embedder.embed(query)
        return self._backend.search(query_embedding, top_k=top_k, min_score=min_score)

    def search_by_entity(self, entity_id: str) -> List[dict]:
        """Retrieve all chunks for a specific entity."""
        return self._backend.search_by_entity(entity_id)

    def get_indexed_entity_names(self) -> set:
        """Return the set of entity names currently indexed."""
        return self._backend.get_indexed_entity_names()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return backend statistics."""
        return self._backend.stats()

    def close(self) -> None:
        """Release backend resources."""
        self._backend.close()


class VectorStoreFactory:
    """Factory that creates VectorStore facades wrapping a chosen backend."""

    _backends = {
        "sqlite": SQLiteVectorStore,
    }

    @classmethod
    def _load_optional_backend(cls, name: str):
        """Lazy-load an optional backend by name."""
        if name not in _OPTIONAL_BACKENDS:
            return None
        module_path, class_name, pkg = _OPTIONAL_BACKENDS[name]
        try:
            module = __import__(module_path, fromlist=[class_name])
            backend_class = getattr(module, class_name)
            cls._backends[name] = backend_class
            return backend_class
        except (ImportError, AttributeError) as e:
            raise ImportError(
                f"Backend '{name}' requires {pkg}. "
                f"Run: pip install {pkg}"
            ) from e

    @classmethod
    def create(cls, backend_type: str = None,
               embedder=None, **kwargs) -> VectorStore:
        """
        Create a text-first VectorStore facade.

        Args:
            backend_type: ``"sqlite"`` (default). Optional backends loaded lazily.
            embedder: Optional pre-built Embedder.  A new one is created when
                      omitted.  Passed to the facade, *not* to the backend.
            **kwargs: Backend-specific keyword arguments (e.g. ``db_path`` for
                      SQLite).  Unknown keys are filtered out automatically.

        Returns:
            :class:`VectorStore` facade ready for text-first operations.

        Raises:
            ValueError: If ``backend_type`` is not registered.
            ImportError: If optional backend is requested but not installed.
        """
        # Default from env var, fallback to "sqlite"
        backend_type = (backend_type or os.environ.get("VECTOR_BACKEND", "sqlite")).lower()

        if backend_type not in cls._backends:
            # Try to lazy-load optional backend
            backend_class = cls._load_optional_backend(backend_type)
            if backend_class is None:
                available = ", ".join(cls._backends.keys())
                raise ValueError(
                    f"Unknown backend: '{backend_type}'. "
                    f"Available: {available}"
                )

        backend_class = cls._backends[backend_type]

        # Filter kwargs to only those accepted by the backend constructor
        valid_params = inspect.signature(backend_class.__init__).parameters
        filtered = {k: v for k, v in kwargs.items() if k in valid_params}
        backend = backend_class(**filtered)

        # Resolve embedder lazily to avoid import cost when not needed
        if embedder is None:
            from engine.vector.embedder import Embedder
            embedder = Embedder()

        return VectorStore(backend, embedder)

    @classmethod
    def available_backends(cls) -> list:
        """List registered backend names."""
        return list(cls._backends.keys())

    @classmethod
    def register_backend(cls, name: str, backend_class: type) -> None:
        """Register a custom backend (for extensions)."""
        if not issubclass(backend_class, BaseVectorStore):
            raise TypeError("Backend must inherit from BaseVectorStore")
        cls._backends[name.lower()] = backend_class


__all__ = [
    "BaseVectorStore",
    "SearchResult",
    "SQLiteVectorStore",
    "VectorStore",
    "VectorStoreFactory",
]
