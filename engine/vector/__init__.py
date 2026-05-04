"""
Vector Store — private backend interface and default SQLite facade.

Phase 1 keeps the public VectorStore API unchanged while moving SQLite storage
behind a BaseVectorStore implementation.
"""

import inspect

from engine.vector.base import BaseVectorStore, SearchResult
from engine.vector.sqlite_store import SQLiteVectorStore
from engine.vector.vector_store import VectorStore


class VectorStoreFactory:
    """Factory that creates VectorStore facades wrapping the SQLite backend."""

    _backends = {
        "sqlite": SQLiteVectorStore,
    }

    @classmethod
    def create(cls, backend_type: str = "sqlite", embedder=None, **kwargs) -> VectorStore:
        """
        Create a text-first VectorStore facade.

        Phase 1 intentionally exposes only the SQLite backend.
        """
        backend_type = (backend_type or "sqlite").lower()
        if backend_type not in cls._backends:
            available = ", ".join(cls._backends.keys())
            raise ValueError(
                f"Unknown backend: '{backend_type}'. "
                f"Available: {available}"
            )

        valid_params = inspect.signature(VectorStore.__init__).parameters
        filtered = {k: v for k, v in kwargs.items() if k in valid_params}
        return VectorStore(embedder=embedder, **filtered)

    @classmethod
    def available_backends(cls) -> list:
        """List registered backend names."""
        return list(cls._backends.keys())


__all__ = [
    "BaseVectorStore",
    "SearchResult",
    "SQLiteVectorStore",
    "VectorStore",
    "VectorStoreFactory",
]
