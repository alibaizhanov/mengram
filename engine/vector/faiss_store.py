"""
FAISSVectorStore — High-performance vector backend using Facebook AI Similarity Search.

Install: pip install faiss-cpu  (or faiss-gpu for CUDA)

Features:
- Approximate Nearest Neighbor (ANN) search
- IndexFlatIP for exact inner product (default, <10K vectors)
- IndexIVFFlat for approximate search (>10K vectors, future)
- Metadata stored in-memory (FAISS stores only vectors)
"""

import json
import numpy as np
from typing import Optional, List, Dict

from engine.vector.base import BaseVectorStore, SearchResult

# Try to import FAISS
try:
    import faiss
    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False


class FAISSVectorStore(BaseVectorStore):
    """
    FAISS-based vector storage with metadata management.
    
    Uses IndexFlatIP for exact search (best for <10K vectors).
    For larger datasets, consider IndexIVFFlat or IndexHNSW.
    """

    def __init__(self, dimension: int = 1536, index_type: str = "flat",
                 nlist: int = 100, metric=faiss.METRIC_INNER_PRODUCT):
        if not _FAISS_AVAILABLE:
            raise ImportError(
                "FAISS not installed. Run: pip install faiss-cpu"
            )

        super().__init__(dimension=dimension)
        self.index_type = index_type.lower()
        self.nlist = nlist
        self.metric = metric

        # Metadata storage (FAISS stores only vectors)
        self._metadata: Dict[str, dict] = {}
        self._id_to_idx: Dict[str, int] = {}
        self._idx_to_id: Dict[int, str] = {}
        self._next_idx = 0

        # Create index
        self._index = self._create_index()

    def _create_index(self):
        """Create FAISS index based on type"""
        if self.index_type == "flat":
            # Exact search — best for <10K vectors
            return faiss.IndexFlatIP(self.dimension)
        elif self.index_type == "ivf":
            # Approximate — requires training
            quantizer = faiss.IndexFlatIP(self.dimension)
            index = faiss.IndexIVFFlat(quantizer, self.dimension, self.nlist, self.metric)
            return index
        else:
            raise ValueError(f"Unknown index type: {self.index_type}")

    def _train_if_needed(self, vectors: np.ndarray):
        """Train IVF index if needed"""
        if self.index_type == "ivf" and not self._index.is_trained:
            if len(vectors) < self.nlist * 10:
                # Too few vectors for IVF, fall back to Flat
                print("   ⚠️  Too few vectors for IVF, using FlatIP")
                self._index = faiss.IndexFlatIP(self.dimension)
            else:
                self._index.train(vectors.astype(np.float32))

    def add_chunk(self, chunk_id: str, entity_id: str, entity_name: str,
                  section: str, content: str, embedding: np.ndarray,
                  position: int = 0) -> None:
        """Add single chunk with metadata"""
        vector = self._validate_embedding(embedding)

        # Add to FAISS index
        vec_array = vector.reshape(1, -1).astype(np.float32)
        self._train_if_needed(vec_array)
        self._index.add(vec_array)

        # Store metadata
        idx = self._next_idx
        self._id_to_idx[chunk_id] = idx
        self._idx_to_id[idx] = chunk_id
        self._metadata[chunk_id] = {
            "entity_id": entity_id,
            "entity_name": entity_name,
            "section": section,
            "content": content,
            "position": position,
        }
        self._next_idx += 1

    def add_chunks_batch(self, chunks: List[dict]) -> None:
        """Batch-add chunks efficiently"""
        if not chunks:
            return

        # Collect vectors
        vectors = []
        for c in chunks:
            emb = self._validate_embedding(c["embedding"])
            vectors.append(emb)

        # Add to FAISS in one call
        vec_array = np.array(vectors).astype(np.float32)
        self._train_if_needed(vec_array)
        self._index.add(vec_array)

        # Store metadata
        for i, c in enumerate(chunks):
            idx = self._next_idx + i
            chunk_id = c["chunk_id"]
            self._id_to_idx[chunk_id] = idx
            self._idx_to_id[idx] = chunk_id
            self._metadata[chunk_id] = {
                "entity_id": c["entity_id"],
                "entity_name": c["entity_name"],
                "section": c["section"],
                "content": c["content"],
                "position": c.get("position", 0),
            }

        self._next_idx += len(chunks)
        print(f"   ✅ Indexed {len(chunks)} chunks (FAISS)")

    def search(self, query_embedding: np.ndarray, top_k: int = 5,
               min_score: float = 0.0) -> List[SearchResult]:
        """Search using FAISS ANN"""
        if self._index.ntotal == 0:
            return []

        # Normalize and reshape
        query_vec = self._validate_embedding(query_embedding)
        query_array = query_vec.reshape(1, -1).astype(np.float32)

        # Search
        scores, indices = self._index.search(query_array, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:  # FAISS returns -1 for empty slots
                continue
            if score < min_score:
                break

            chunk_id = self._idx_to_id.get(int(idx))
            if not chunk_id:
                continue

            meta = self._metadata.get(chunk_id, {})
            results.append(SearchResult(
                chunk_id=chunk_id,
                entity_id=meta.get("entity_id", ""),
                entity_name=meta.get("entity_name", ""),
                section=meta.get("section", ""),
                content=meta.get("content", ""),
                score=float(score),
            ))

        return results

    def search_by_entity(self, entity_id: str) -> List[dict]:
        """Get all chunks for entity (brute force — FAISS doesn't support this natively)"""
        results = []
        for chunk_id, meta in self._metadata.items():
            if meta.get("entity_id") == entity_id:
                results.append({
                    "id": chunk_id,
                    **meta,
                })
        return results

    def stats(self) -> dict:
        """Store statistics"""
        return {
            "total_chunks": self._index.ntotal,
            "total_entities": len(set(
                m["entity_id"] for m in self._metadata.values()
            )),
            "backend_type": f"faiss_{self.index_type}",
            "dimension": self.dimension,
        }

    def close(self) -> None:
        """Clean up FAISS index"""
        del self._index
        self._metadata.clear()
        self._id_to_idx.clear()
        self._idx_to_id.clear()
