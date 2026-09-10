-- Rollback for the vector indexes dropped on 2026-09-10.
--
-- Why they were dropped: 0 index scans in 275 days of collected statistics,
-- 1242 MB between them. Every query touching `embeddings` and `chunk_embeddings`
-- filters by distance (WHERE 1 - (col <=> q) > threshold) instead of ordering by
-- it, and only `ORDER BY col <=> q LIMIT n` can use an HNSW index. The same
-- indexes on episode_embeddings and procedure_embeddings are heavily used,
-- because those queries do order by distance — which is how we know the
-- statistics are trustworthy and pgvector is not the problem.
--
-- `idx_embeddings_vector` was an exact duplicate of `idx_embeddings_hnsw`:
-- same table, same column, same opclass, same parameters.
--
-- Recreate any of these the moment a query on those tables is rewritten to
-- order by distance with a LIMIT. Build CONCURRENTLY: on this data each takes
-- minutes and would otherwise lock writes.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_embeddings_hnsw
    ON public.embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m='16', ef_construction='64');

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_embeddings_v2_hnsw
    ON public.embeddings USING hnsw (embedding_v2 vector_cosine_ops)
    WITH (m='16', ef_construction='64');

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunk_emb_hnsw
    ON public.chunk_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m='16', ef_construction='64');

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunk_emb_v2_hnsw
    ON public.chunk_embeddings USING hnsw (embedding_v2 vector_cosine_ops)
    WITH (m='16', ef_construction='64');

-- Deliberately NOT recreated: idx_embeddings_vector, the duplicate.
