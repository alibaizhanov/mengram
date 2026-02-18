"""
Embedder — vector embeddings generation.

Uses sentence-transformers with all-MiniLM-L6-v2 model:
- 80MB model size
- 384-dimensional vectors
- On Mac M1 uses Metal GPU automatically
- Fully local, nothing sent to cloud
"""

import sys
from sentence_transformers import SentenceTransformer
from typing import Optional
import numpy as np


class Embedder:
    """Local embeddings generator"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model: Optional[SentenceTransformer] = None

    @property
    def model(self) -> SentenceTransformer:
        """Lazy loading — model loaded on first use"""
        if self._model is None:
            print(f"🧠 Loading model {self.model_name}...", file=sys.stderr)
            self._model = SentenceTransformer(self.model_name)
            print(f"✅ Model loaded ({self.dimensions}D)", file=sys.stderr)
        return self._model

    @property
    def dimensions(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> np.ndarray:
        """Embed single text → vector"""
        return self.model.encode(text, normalize_embeddings=True)

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Embed multiple texts → vector matrix"""
        return self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 50,
        )

    def similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Cosine similarity between two vectors"""
        return float(np.dot(vec_a, vec_b))

    def search(self, query_vec: np.ndarray, corpus_vecs: np.ndarray, top_k: int = 5) -> list[tuple[int, float]]:
        """
        Search for top-K nearest vectors.
        Returns [(index, score), ...]
        """
        scores = np.dot(corpus_vecs, query_vec)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(idx), float(scores[idx])) for idx in top_indices]


if __name__ == "__main__":
    embedder = Embedder()

    # Test
    texts = [
        "PostgreSQL connection pool exhaustion",
        "Database issues under high load",
        "React component for dashboard",
        "Kafka consumer lag issues",
    ]

    vectors = embedder.embed_batch(texts)
    print(f"\n📐 Vectors shape: {vectors.shape}", file=sys.stderr)

    query = embedder.embed("database performance issue")
    results = embedder.search(query, vectors, top_k=3)

    print(f"\n🔍 Query: 'database performance issue'", file=sys.stderr)
    for idx, score in results:
        print(f"   {score:.3f}  {texts[idx]}", file=sys.stderr)
