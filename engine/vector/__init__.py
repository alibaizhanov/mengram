"""
Vector Store — Pluggable backends for semantic search.

Usage:
    from engine.vector import VectorStoreFactory, BaseVectorStore
    
    # SQLite (default, good for <10K chunks)
    store = VectorStoreFactory.create("sqlite", db_path="./vectors.db")
    
    # FAISS (fast, good for >1K chunks)
    store = VectorStoreFactory.create("faiss", dimension=1536)

Backends:
    - sqlite: SQLite with in-memory caching (default)
    - faiss: Facebook AI Similarity Search (high performance)
    - hnsw: Hierarchical Navigable Small World (future)
"""

from engine.vector.base import BaseVectorStore, SearchResult
from engine.vector.sqlite_store import SQLiteVectorStore

# Optional: FAISS backend (only if installed)
try:
    from engine.vector.faiss_store import FAISSVectorStore
    _FAISS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _FAISS_AVAILABLE = False


class VectorStoreFactory:
    """Factory for creating vector store backends"""

    _backends = {
        "sqlite": SQLiteVectorStore,
    }

    # Register FAISS if available
    if _FAISS_AVAILABLE:
        _backends["faiss"] = FAISSVectorStore

    @classmethod
    def create(cls, backend_type: str = "sqlite", **kwargs) -> BaseVectorStore:
        """
        Create a vector store instance.

        Args:
            backend_type: "sqlite" or "faiss"
            **kwargs: Passed to the backend constructor

        Returns:
            BaseVectorStore instance

        Raises:
            ValueError: If backend_type is unknown
            ImportError: If FAISS is requested but not installed
        """
        backend_type = backend_type.lower()

        if backend_type not in cls._backends:
            available = ", ".join(cls._backends.keys())
            raise ValueError(
                f"Unknown backend: '{backend_type}'. "
                f"Available: {available}"
            )

        backend_class = cls._backends[backend_type]
        return backend_class(**kwargs)

    @classmethod
    def available_backends(cls) -> list[str]:
        """List available backend names"""
        return list(cls._backends.keys())

    @classmethod
    def register_backend(cls, name: str, backend_class: type):
        """Register a custom backend (for extensions)"""
        if not issubclass(backend_class, BaseVectorStore):
            raise TypeError("Backend must inherit from BaseVectorStore")
        cls._backends[name.lower()] = backend_class


__all__ = [
    "BaseVectorStore",
    "SearchResult",
    "SQLiteVectorStore",
    "VectorStoreFactory",
]
