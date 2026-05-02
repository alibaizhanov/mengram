"""
Benchmark: SQLiteVectorStore Performance Baseline

Usage:
    python scripts/bench_vector_store.py

Measures (Phase 1 — SQLite baseline):
- add_chunk throughput (vectors/sec)
- search latency (ms/query)
- memory usage (MB)

Phase 2 extension:
- Run with VECTOR_BACKEND=sqlite_vec to compare vs sqlite
- Recall@10 measured against brute-force ground truth

Acceptance criteria per RFC #33:
- >=2x search latency improvement at 10K+ vectors
- >=95% recall@10 vs ground truth (SQLiteVectorStore)
- Memory footprint reported (not a hard gate)
"""

import time
import sys
import os
import tempfile
import tracemalloc
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.vector.sqlite_store import SQLiteVectorStore


def generate_vectors(n: int, dim: int = 384) -> np.ndarray:
    """Generate random normalized float32 vectors."""
    rng = np.random.default_rng(seed=42)
    vectors = rng.random((n, dim)).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / norms


def benchmark_add(store, vectors: np.ndarray, entity_ids: list) -> dict:
    """Benchmark add_chunks_batch throughput."""
    n = len(vectors)
    chunks = []
    for i, vec in enumerate(vectors):
        chunks.append({
            "chunk_id": f"chunk_{i}",
            "entity_id": entity_ids[i],
            "entity_name": f"Entity_{entity_ids[i]}",
            "section": "default",
            "content": f"Content {i}",
            "embedding": vec,
            "position": i,
        })
    start = time.perf_counter()
    store.add_chunks_batch(chunks)
    elapsed = time.perf_counter() - start
    return {
        "total": n,
        "time_sec": elapsed,
        "vectors_per_sec": n / elapsed,
    }


def benchmark_search(store, query_vectors: np.ndarray, top_k: int = 10) -> dict:
    """Benchmark search latency."""
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


def measure_recall_at_k(store, data_vectors: np.ndarray, query_vectors: np.ndarray,
                        k: int = 10) -> float:
    """
    Measure recall@k against brute-force ground truth.
    
    Ground truth: exact cosine similarity via dot product on all vectors.
    Since SQLiteVectorStore already does exact search, recall should be ~100%.
    In Phase 2, sqlite-vec approximate search will be compared against this.
    """
    correct = 0
    total = 0
    
    for qv in query_vectors:
        # Backend search
        approx_results = store.search(qv, top_k=k)
        approx_ids = {r.chunk_id for r in approx_results}
        
        # Ground truth: brute force exact dot product
        scores = np.dot(data_vectors, qv)
        top_k_indices = np.argsort(scores)[::-1][:k]
        exact_ids = {f"chunk_{idx}" for idx in top_k_indices}
        
        # Recall = |intersection| / k
        intersection = approx_ids & exact_ids
        correct += len(intersection)
        total += k
    
    return (correct / total) * 100.0


def run_benchmark(vector_counts: list = [100, 1000, 5000, 10000], dim: int = 384):
    """Run full benchmark suite."""
    query_count = 100
    
    print("=" * 70)
    print("  SQLiteVectorStore Performance Baseline — Phase 1")
    print("=" * 70)
    print(f"Dimension: {dim}  |  Query count: {query_count}")
    print(f"Backend:   {os.environ.get('VECTOR_BACKEND', 'sqlite')}")
    print()
    
    for n in vector_counts:
        print(f"--- Dataset: {n:,} vectors ---")
        
        # Generate data
        vectors = generate_vectors(n, dim)
        entity_ids = [f"entity_{i % 10}" for i in range(n)]
        queries = generate_vectors(query_count, dim)
        
        # Create backend directly (no embedder needed for benchmark)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bench.db"
            store = SQLiteVectorStore(db_path=str(db_path), dimension=dim)
            
            # Measure memory before indexing
            tracemalloc.start()
            
            # Benchmark add
            add_stats = benchmark_add(store, vectors, entity_ids)
            
            # Benchmark search
            search_stats = benchmark_search(store, queries, top_k=10)
            
            # Measure recall@10
            recall = measure_recall_at_k(store, vectors, queries, k=10)
            
            # Memory
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            
            # Stats
            stats = store.stats()
            
            print(f"  Indexing: {add_stats['vectors_per_sec']:,.0f} vec/sec  ({add_stats['time_sec']:.2f}s)")
            print(f"  Search:   {search_stats['ms_per_query']:.3f} ms/query  ({search_stats['time_sec']:.2f}s total)")
            print(f"  Recall@10: {recall:.1f}%  (vs brute-force ground truth)")
            print(f"  Memory:   {peak / 1024 / 1024:.1f} MB peak")
            print(f"  Chunks:   {stats['total_chunks']:,}")
            
            store.close()
        
        print()
    
    print("=" * 70)
    print("  Benchmark complete — Phase 1 baseline established")
    print("=" * 70)
    print()
    print("Phase 2: Run with VECTOR_BACKEND=sqlite_vec to compare performance.")
    print("Expected improvement: >=2x search latency at 10K+ vectors")
    print("Expected recall: >=95% @10 vs this SQLite ground truth")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Vector store benchmark")
    parser.add_argument(
        "--dim", type=int, default=384,
        help="Embedding dimension (default: 384)",
    )
    parser.add_argument(
        "--sizes", nargs="+", type=int, default=[100, 1000, 5000, 10000],
        help="Vector counts to benchmark (default: 100 1000 5000 10000)",
    )
    args = parser.parse_args()
    
    run_benchmark(vector_counts=args.sizes, dim=args.dim)
