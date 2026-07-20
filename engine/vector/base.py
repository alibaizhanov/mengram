"""
BaseVectorStore — Abstract interface for local vector backends.

Vector storage backends must implement this interface.
This enables swapping backends without changing Brain or retrieval logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List
import numpy as np


@dataclass
class SearchResult:
    """Single search result from any backend"""
    chunk_id: str
    entity_id: str
    entity_name: str
    section: str
    content: str
    score: float

    def __repr__(self):
        preview = self.content[:60] + "..." if len(self.content) > 60 else self.content
        return f"Result({self.score:.3f} | {self.entity_name}/{self.section}: {preview})"


class BaseVectorStore(ABC):
    """
    Abstract base class for vector storage backends.
    
    Implementations must handle:
    - Storing chunks with embeddings
    - Semantic search by query vector
    - Entity-scoped retrieval
    - Stats and lifecycle management
    """

    def __init__(self, dimension: int = 384, embedder=None):
        """
        Args:
            dimension: Vector dimension (default 384 for all-MiniLM-L6-v2)
            embedder: Optional embedder instance (used by facade, not backends directly)
        """
        self.dimension = dimension
        self.embedder = embedder

    @abstractmethod
    def add_chunk(self, chunk_id: str, entity_id: str, entity_name: str,
                  section: str, content: str, embedding: np.ndarray,
                  position: int = 0) -> None:
        """
        Add a single chunk with its embedding.
        
        Args:
            chunk_id: Unique identifier for the chunk
            entity_id: Parent entity identifier
            entity_name: Human-readable entity name
            section: Section within the entity
            content: Text content
            embedding: Pre-computed embedding vector
            position: Ordering position within entity
        """
        pass

    @abstractmethod
    def add_chunks_batch(self, chunks: List[dict]) -> None:
        """
        Batch-add chunks for efficient bulk indexing.
        
        chunks: List of dicts with keys:
            - chunk_id, entity_id, entity_name, section, content, embedding, position
        """
        pass

    @abstractmethod
    def search(self, query_embedding: np.ndarray, top_k: int = 5,
               min_score: float = 0.0) -> List[SearchResult]:
        """
        Semantic search using a pre-computed query embedding.
        
        Args:
            query_embedding: Query vector (already normalized)
            top_k: Maximum results to return
            min_score: Minimum similarity score threshold
            
        Returns:
            List of SearchResult, sorted by score descending
        """
        pass

    @abstractmethod
    def search_by_entity(self, entity_id: str) -> List[dict]:
        """
        Retrieve all chunks for a specific entity.
        
        Args:
            entity_id: Entity identifier
            
        Returns:
            List of chunk dicts with all metadata
        """
        pass

    @abstractmethod
    def stats(self) -> dict:
        """
        Return store statistics.
        
        Returns:
            Dict with store statistics such as total_chunks and total_entities.
        """
        pass

    @abstractmethod
    def get_indexed_entity_names(self) -> set:
        """
        Return the set of entity names currently indexed.

        Used by Brain to detect missing/new entities without direct DB access.

        Returns:
            Set of entity name strings
        """
        pass

    @abstractmethod
    def delete_entity(self, entity_id: str) -> None:
        """
        Remove all chunks belonging to a given entity.

        Args:
            entity_id: The entity identifier whose chunks should be deleted
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Clean up resources (connections, memory, etc.)"""
        pass

    def _validate_embedding(self, embedding: np.ndarray) -> np.ndarray:
        """Validate, normalize, and cast to float32."""
        if embedding.shape[0] != self.dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimension}, "
                f"got {embedding.shape[0]}"
            )
        # Always return float32 — dividing by a float64 norm would upcast
        # to float64, doubling the byte length stored/loaded from SQLite.
        v = embedding.astype(np.float32)
        norm = np.linalg.norm(v)
        if norm > 0:
            return (v / norm).astype(np.float32)
        return v
