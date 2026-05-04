"""
SQLiteVectorStore — Reference implementation of BaseVectorStore.

Migrates the original VectorStore logic to the pluggable interface.
Uses SQLite with in-memory caching for fast cosine similarity search.
"""

import sqlite3
import numpy as np
from typing import Optional, List

from engine.vector.base import BaseVectorStore, SearchResult


class SQLiteVectorStore(BaseVectorStore):
    """
    SQLite-based vector storage with cosine similarity.
    
    Sufficient for vaults up to ~10K notes.
    """

    def __init__(self, db_path: str = ":memory:", embedder=None,
                 dimension: int = 384):
        super().__init__(dimension=dimension, embedder=embedder)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

        # In-memory cache for fast search
        self._vectors: Optional[np.ndarray] = None
        self._chunk_ids: List[str] = []

    def _create_tables(self):
        self.conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                entity_name TEXT NOT NULL,
                section TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                position INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_entity ON chunks(entity_id);
        """)
        self.conn.commit()

    def add_chunk(self, chunk_id: str, entity_id: str, entity_name: str,
                  section: str, content: str, embedding: np.ndarray,
                  position: int = 0) -> None:
        """Add single chunk with embedding (already computed externally)"""
        vector = self._validate_embedding(embedding)
        self.conn.execute(
            """INSERT OR REPLACE INTO chunks 
               (id, entity_id, entity_name, section, content, embedding, position)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chunk_id, entity_id, entity_name, section, content,
             vector.tobytes(), position),
        )
        self.conn.commit()
        self._invalidate_cache()

    def add_chunks_batch(self, chunks: List[dict]) -> None:
        """Batch-add chunks for efficient indexing"""
        if not chunks:
            return

        rows = []
        for c in chunks:
            emb = self._validate_embedding(c["embedding"])
            rows.append((
                c["chunk_id"], c["entity_id"], c["entity_name"],
                c["section"], c["content"], emb.tobytes(),
                c.get("position", 0)
            ))

        self.conn.executemany(
            """INSERT OR REPLACE INTO chunks 
               (id, entity_id, entity_name, section, content, embedding, position)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        self._invalidate_cache()

    def search(self, query_embedding: np.ndarray, top_k: int = 5,
               min_score: float = 0.0) -> List[SearchResult]:
        """Semantic search using pre-computed query embedding"""
        self._ensure_cache()

        if len(self._chunk_ids) == 0:
            return []

        # Normalize query
        query_vec = self._validate_embedding(query_embedding)

        # Cosine similarity (vectors already normalized)
        scores = np.dot(self._vectors, query_vec)

        # Top-K
        top_indices = np.argsort(scores)[::-1][:top_k]

        # Collect candidate chunk_ids and scores in score order
        candidates = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                break
            candidates.append((self._chunk_ids[idx], score))

        if not candidates:
            return []

        # Batch fetch all rows in one query — avoids N+1 per-loop SELECT
        # Chunk into batches of 900 to stay under SQLite 999 parameter limit
        candidate_ids = [c[0] for c in candidates]
        row_by_id = {}
        SQLITE_MAX_PARAMS = 900
        for i in range(0, len(candidate_ids), SQLITE_MAX_PARAMS):
            batch = candidate_ids[i:i + SQLITE_MAX_PARAMS]
            placeholders = ",".join("?" * len(batch))
            rows = self.conn.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})",
                batch,
            ).fetchall()
            for r in rows:
                row_by_id[r["id"]] = r

        results = []
        for chunk_id, score in candidates:
            row = row_by_id.get(chunk_id)
            if row:
                results.append(SearchResult(
                    chunk_id=row["id"],
                    entity_id=row["entity_id"],
                    entity_name=row["entity_name"],
                    section=row["section"],
                    content=row["content"],
                    score=score,
                ))

        return results

    def search_by_entity(self, entity_id: str) -> List[dict]:
        """Get all chunks for specific entity"""
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE entity_id = ? ORDER BY position",
            (entity_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Store statistics"""
        total = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        entities = self.conn.execute(
            "SELECT COUNT(DISTINCT entity_id) FROM chunks"
        ).fetchone()[0]
        return {
            "total_chunks": total,
            "total_entities": entities,
            "backend_type": "sqlite",
            "db_path": self.db_path,
        }

    def get_indexed_entity_names(self) -> set:
        """Return all entity names currently stored in this backend."""
        rows = self.conn.execute(
            "SELECT DISTINCT entity_name FROM chunks"
        ).fetchall()
        return {r["entity_name"] for r in rows}

    def delete_entity(self, entity_id: str) -> None:
        """Remove all chunks for a given entity and invalidate the cache."""
        self.conn.execute(
            "DELETE FROM chunks WHERE entity_id = ?", (entity_id,)
        )
        self.conn.commit()
        self._invalidate_cache()

    def close(self) -> None:
        """Close database connection"""
        self.conn.close()

    def _ensure_cache(self):
        """Load all vectors into RAM for fast search"""
        if self._vectors is not None:
            return

        rows = self.conn.execute("SELECT id, embedding FROM chunks").fetchall()
        if not rows:
            self._vectors = np.array([])
            self._chunk_ids = []
            return

        self._chunk_ids = [r["id"] for r in rows]
        self._vectors = np.array([
            np.frombuffer(r["embedding"], dtype=np.float32)
            for r in rows
        ])

    def _invalidate_cache(self):
        """Reset cache when data changes"""
        self._vectors = None
        self._chunk_ids = []
