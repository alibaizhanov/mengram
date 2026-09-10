-- Drop five HNSW indexes that have served 0 scans in 275 days (1242 MB).
-- Rollback: specs/rollback-dropped-indexes-2026-09-10.sql
-- CONCURRENTLY takes a SHARE UPDATE EXCLUSIVE lock: reads and writes keep working.
-- Each statement must run outside a transaction, so run this file as-is, not inside BEGIN.

\timing on
SELECT pg_size_pretty(pg_database_size(current_database())) AS size_before;

DROP INDEX CONCURRENTLY IF EXISTS public.idx_embeddings_vector;    -- 313 MB, duplicate
DROP INDEX CONCURRENTLY IF EXISTS public.idx_embeddings_hnsw;      -- 313 MB, legacy 1536 column
DROP INDEX CONCURRENTLY IF EXISTS public.idx_embeddings_v2_hnsw;   -- 415 MB, live column, unusable by the query shape
DROP INDEX CONCURRENTLY IF EXISTS public.idx_chunk_emb_hnsw;       --  98 MB
DROP INDEX CONCURRENTLY IF EXISTS public.idx_chunk_emb_v2_hnsw;    -- 103 MB

SELECT pg_size_pretty(pg_database_size(current_database())) AS size_after;

SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size, idx_scan
  FROM pg_stat_user_indexes
 WHERE relname IN ('embeddings','chunk_embeddings')
 ORDER BY pg_relation_size(indexrelid) DESC;
