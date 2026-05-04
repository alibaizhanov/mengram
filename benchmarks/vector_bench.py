"""
Benchmark: SQLite vs FAISS Vector Store Performance.

Usage:
    python benchmarks/vector_bench.py

Measures:
- add_chunk throughput (vectors/sec)
- search latency (ms/query)
- memory usage (MB)
"""

import time
import numpy as np
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.vector.sqlite_store import SQLiteVectorStore

# Try FAISS
try:
    from engine.vector.faiss_store import FAISSVectorStore
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    print("⚠️  FAISS not installed. Run: pip install faiss-cpu")


def generate_vectors(n: int, dim: int = 1536) -> np.ndarray:
    """Generate random normalized vectors"""
    vectors = np.random.randn(n, dim).astype(np.float32)
    # Normalize
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / norms


def benchmark_add(store, vectors: np.ndarray, entity_ids: list) -> dict:
    """Benchmark add_chunk throughput"""
    n = len(vectors)

    start = time.perf_counter()
    for i, vec in enumerate(vectors):
        store.add_chunk(
            chunk_id=f"chunk_{i}",
            entity_id=entity_ids[i],
            entity_name=f"Entity_{entity_ids[i]}",
            section="default",
            content=f"Content {i}",
            embedding=vec,
            position=i,
        )
    elapsed = time.perf_counter() - start

    return {
        "total": n,
        "time_sec": elapsed,
        "vectors_per_sec": n / elapsed,
    }


def benchmark_search(store, query_vectors: np.ndarray, top_k: int = 5) -> dict:
    """Benchmark search latency"""
    n = len(query_vectors)

    start = time.perf_counter()
    for qv in query_vectors:
        store.search(qv, top_k=top_k)
    elapsed = time.perf_counter() - start

    return {
        "queries": n,
        "time_sec": elapsed,
        "ms_per_query": (elapsed / n) * 1000,
    }


def run_benchmark(vector_counts: list = [100, 500, 1000]):
    """Run full benchmark suite"""
    dim = 1536
    query_count = 100

    print("=" * 60)
    print("🧪 Vector Store Benchmark")
    print("=" * 60)
    print(f"Dimension: {dim}")
    print(f"Query count: {query_count}")
    print()

    for n in vector_counts:
        print(f"--- Dataset: {n} vectors ---")

        # Generate data
        vectors = generate_vectors(n, dim)
        entity_ids = [f"entity_{i % 10}" for i in range(n)]
        queries = generate_vectors(query_count, dim)

        # SQLite
        sqlite_store = SQLiteVectorStore(":memory:", dimension=dim)
        add_stats = benchmark_add(sqlite_store, vectors, entity_ids)
        search_stats = benchmark_search(sqlite_store, queries)
        stats = sqlite_store.stats()

        print(f"SQLite:")
        print(f"  Add: {add_stats['vectors_per_sec']:,.0f} vec/sec")
        print(f"  Search: {search_stats['ms_per_query']:.2f} ms/query")
        print(f"  Chunks: {stats['total_chunks']}")
        sqlite_store.close()

        # FAISS
        if FAISS_AVAILABLE:
            faiss_store = FAISSVectorStore(dimension=dim)
            add_stats = benchmark_add(faiss_store, vectors, entity_ids)
            search_stats = benchmark_search(faiss_store, queries)
            stats = faiss_store.stats()

            print(f"FAISS:")
            print(f"  Add: {add_stats['vectors_per_sec']:,.0f} vec/sec")
            print(f"  Search: {search_stats['ms_per_query']:.2f} ms/query")
            print(f"  Chunks: {stats['total_chunks']}")
            faiss_store.close()
        else:
            print("FAISS: (not installed)")

        print()

    print("=" * 60)
    print("✅ Benchmark complete")
    print("=" * 60)


if __name__ == "__main__":
    # Default: test with 100, 500, 1000 vectors
    # For quick test: [100]
    run_benchmark(vector_counts=[100, 500, 1000])
