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
        kwargs (e.g. db_path, dimension) are forwarded to the backend constructor;
        unrecognised keys are silently dropped to keep call sites forward-compatible.
        """
        backend_type = (backend_type or "sqlite").lower()
        if backend_type not in cls._backends:
            available = ", ".join(cls._backends.keys())
            raise ValueError(
                f"Unknown backend: '{backend_type}'. "
                f"Available: {available}"
            )

        # Forward only the kwargs the chosen backend actually accepts
        backend_class = cls._backends[backend_type]
        valid_backend_params = inspect.signature(backend_class.__init__).parameters
        backend_kwargs = {k: v for k, v in kwargs.items() if k in valid_backend_params}
        backend = backend_class(**backend_kwargs)

        if embedder is None:
            from engine.vector.embedder import Embedder
            embedder = Embedder()

        return VectorStore(embedder=embedder, backend=backend)

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
