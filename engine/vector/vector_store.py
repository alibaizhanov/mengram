"""
Vector Store — text-first facade over the local SQLite vector backend.

Stores text chunks + their vectors.
Search via cosine similarity (numpy, no external dependencies).

Sufficient for vaults up to ~10K notes.
"""

from typing import Optional, TYPE_CHECKING

from engine.parser.markdown_parser import parse_vault
from engine.vector.base import SearchResult
from engine.vector.sqlite_store import SQLiteVectorStore

if TYPE_CHECKING:
    from engine.vector.embedder import Embedder


class VectorStore:
    """SQLite-based Vector Store with cosine similarity search."""

    def __init__(self, db_path: str = ":memory:", embedder: Optional["Embedder"] = None):
        if embedder is None:
            from engine.vector.embedder import Embedder
            embedder = Embedder()
        self.db_path = db_path
        self.embedder = embedder
        self._backend = SQLiteVectorStore(db_path=db_path)

    def add_chunk(self, chunk_id: str, entity_id: str, entity_name: str,
                  section: str, content: str, position: int = 0):
        """Add single chunk with automatic embedding generation."""
        vector = self.embedder.embed(content)
        self._backend.add_chunk(
            chunk_id, entity_id, entity_name, section, content, vector, position
        )

    def add_chunks_batch(self, chunks: list[dict]):
        """
        Batch-add chunks (faster for bulk indexing).
        chunks: [{chunk_id, entity_id, entity_name, section, content, position}]
        """
        if not chunks:
            return

        texts = [c["content"] for c in chunks]
        vectors = self.embedder.embed_batch(texts)
        enriched = [
            {**c, "embedding": vectors[i]}
            for i, c in enumerate(chunks)
        ]
        self._backend.add_chunks_batch(enriched)
        print(f"   ✅ Indexed {len(chunks)} chunks")

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> list[SearchResult]:
        """
        Semantic search by query.
        Returns top_k most relevant chunks.
        """
        query_vec = self.embedder.embed(query)
        return self._backend.search(query_vec, top_k=top_k, min_score=min_score)

    def search_by_entity(self, entity_id: str) -> list[dict]:
        """Get all chunks for specific entity."""
        return self._backend.search_by_entity(entity_id)

    def get_indexed_entity_names(self) -> set:
        """Return all entity names currently indexed."""
        return self._backend.get_indexed_entity_names()

    def delete_entity(self, entity_id: str) -> None:
        """Remove all chunks for a given entity."""
        self._backend.delete_entity(entity_id)

    def stats(self) -> dict:
        stats = self._backend.stats()
        return {
            "total_chunks": stats["total_chunks"],
            "total_entities": stats["total_entities"],
        }

    def close(self):
        self._backend.close()


def index_vault(vault_path: str, db_path: str = ":memory:") -> VectorStore:
    """
    Indexes entire Obsidian vault into Vector Store.
    """
    notes = parse_vault(vault_path)
    store = VectorStore(db_path)

    print(f"📝 Indexing {len(notes)} notes...")

    all_chunks = []
    for note in notes:
        entity_id = note.name.lower().replace(" ", "_")
        for chunk in note.chunks:
            all_chunks.append({
                "chunk_id": f"{entity_id}:{chunk.position}",
                "entity_id": entity_id,
                "entity_name": note.name,
                "section": chunk.section,
                "content": chunk.content,
                "position": chunk.position,
            })

    store.add_chunks_batch(all_chunks)

    stats = store.stats()
    print(f"✅ Done: {stats['total_chunks']} chunks from {stats['total_entities']} notes")

    return store


if __name__ == "__main__":
    import sys

    vault_path = sys.argv[1] if len(sys.argv) > 1 else "./test_vault"
    store = index_vault(vault_path)

    queries = [
        "database performance issue",
        "who works on backend",
        "caching and Redis",
        "microservices architecture",
    ]

    for q in queries:
        print(f"\n🔍 Query: '{q}'")
        results = store.search(q, top_k=3)
        for r in results:
            print(f"   {r.score:.3f} | {r.entity_name}/{r.section}")
            print(f"           {r.content[:80]}...")
