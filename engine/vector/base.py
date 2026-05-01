"""
BaseVectorStore — Abstract interface for pluggable vector backends.

All vector storage backends (SQLite, FAISS, HNSW, etc.) must implement this interface.
This enables swapping backends without changing Brain or retrieval logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List
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

    def __init__(self, dimension: int = 1536, embedder=None):
        """
        Args:
            dimension: Vector dimension (default 1536 for OpenAI embeddings)
            embedder: Optional embedder instance for automatic embedding generation
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
            Dict with keys like: total_chunks, total_entities, backend_type
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Clean up resources (connections, memory, etc.)"""
        pass

    def _validate_embedding(self, embedding: np.ndarray) -> np.ndarray:
        """Validate and normalize embedding vector"""
        if embedding.shape[0] != self.dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimension}, "
                f"got {embedding.shape[0]}"
            )
        # Normalize for cosine similarity
        norm = np.linalg.norm(embedding)
        if norm > 0:
            return embedding / norm
        return embedding
