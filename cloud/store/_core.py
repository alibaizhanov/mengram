"""Connection pool, cursors, schema migration, job tracking, close.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import json
import re
import secrets
import time
from typing import Optional
from contextlib import contextmanager

from ._common import (  # noqa: F401
    psycopg2, PSYCOPG2_AVAILABLE, logger, TTLCache,
)


class CoreMixin:
    """Connection pool, cursors, schema migration, job tracking, close."""


    def __init__(self, database_url: str, pool_min: int = 2, pool_max: int = 10,
                 redis_url: str = None):
        if not PSYCOPG2_AVAILABLE:
            raise ImportError("pip install psycopg2-binary")
        self.database_url = database_url
        self.redis_url = redis_url
        self.cache = TTLCache(default_ttl=30, redis_url=redis_url)

        # Connection pool — keep minimal for multi-replica deploys
        self._pool = None
        self.conn = None
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                pool_min, pool_max, database_url
            )
            logger.info(f"Connection pool created ({pool_min}-{pool_max})")
        except Exception as e:
            logger.warning(f"Pool creation failed, falling back to single connection: {e}")
            self.conn = psycopg2.connect(database_url)
            self.conn.autocommit = True

        self._migrate()

    @contextmanager
    def _get_conn(self):
        """Get a connection from pool (or fallback to self.conn).
        Auto-returns to pool on exit. Auto-reconnects on failure.
        Retries up to 3 times on pool exhaustion."""
        import time as _time
        conn = None
        from_pool = False
        try:
            if self._pool:
                # Retry on pool exhaustion (all connections busy)
                for attempt in range(3):
                    try:
                        conn = self._pool.getconn()
                        break
                    except psycopg2.pool.PoolError:
                        if attempt < 2:
                            _time.sleep(0.1 * (attempt + 1))
                        else:
                            raise
                conn.autocommit = True
                from_pool = True
            else:
                conn = self.conn
            yield conn
        except psycopg2.OperationalError as e:
            logger.error(f"Database connection error: {e}")
            if from_pool and self._pool:
                try:
                    self._pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = None  # Mark as returned so finally doesn't double-return
            else:
                # Reconnect single connection
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = psycopg2.connect(self.database_url)
                self.conn.autocommit = True
            raise  # Always re-raise so caller knows the operation failed
        finally:
            if from_pool and self._pool and conn:
                try:
                    self._pool.putconn(conn)
                except Exception:
                    pass

    @contextmanager
    def _cursor(self, dict_cursor=False):
        """Get a cursor from a pooled connection. THIS is the primary DB access method.
        All methods should use: with self._cursor() as cur: ...
        This ensures connection pooling is actually used."""
        factory = psycopg2.extras.DictCursor if dict_cursor else None
        with self._get_conn() as conn:
            cur = conn.cursor(cursor_factory=factory)
            try:
                yield cur
            finally:
                cur.close()

    def _migrate(self):
        """Auto-migrate: add new columns if missing.
        Uses advisory lock to prevent race conditions when multiple
        gunicorn workers start simultaneously."""
        with self._cursor() as cur:
            # Serialize migrations across workers (released on unlock or connection close)
            cur.execute("SELECT pg_advisory_lock(42)")
            # facts.created_at for temporal queries
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS created_at 
                TIMESTAMPTZ DEFAULT NOW()
            """)
            # facts.archived for conflict resolution
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS archived 
                BOOLEAN DEFAULT FALSE
            """)
            # facts.superseded_by for tracking what replaced it
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS superseded_by
                TEXT DEFAULT NULL
            """)

            # v2.28: per-account settings (capture policy lives here) — the
            # capture boundary: deterministic, server-side control over what
            # extraction is allowed to persist. Empty = capture everything
            # (unchanged behavior for existing users).
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS settings JSONB DEFAULT '{}'
            """)

            # v2.31: where a signup came from. Without it a launch can only be
            # guessed at — a spike in registrations tells you something worked
            # but never which thing. NULL for everyone who arrived before this
            # existed, which is honest: we genuinely do not know.
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_source TEXT
            """)

            # --- v1.5 Hybrid search: tsvector on embeddings ---
            cur.execute("""
                ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS tsv tsvector
            """)
            # Populate tsvector for existing rows
            cur.execute("""
                UPDATE embeddings SET tsv = to_tsvector('english', chunk_text)
                WHERE tsv IS NULL AND chunk_text IS NOT NULL
            """)
            # GIN index for fast text search
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_embeddings_tsv 
                ON embeddings USING gin(tsv)
            """)

            # --- v1.5 HNSW index for vector search ---
            # Drop old index if wrong dimensions, recreate
            try:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw 
                    ON embeddings USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """)
            except Exception:
                pass  # Index may already exist or dimensions mismatch

        logger.info("✅ Migration complete (v1.5: HNSW + tsvector)")

        # --- v1.6 Importance scoring ---
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS importance 
                FLOAT DEFAULT 0.5
            """)
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS access_count 
                INTEGER DEFAULT 0
            """)
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS last_accessed 
                TIMESTAMPTZ DEFAULT NULL
            """)
        logger.info("✅ Migration complete (v1.6: importance scoring)")

        # --- v1.7 Reflection system ---
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS scope 
                VARCHAR(20) DEFAULT 'insight'
            """)
            cur.execute("""
                ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS confidence 
                FLOAT DEFAULT 1.0
            """)
            cur.execute("""
                ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS based_on_facts 
                TEXT[] DEFAULT '{}'
            """)
            cur.execute("""
                ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS refreshed_at 
                TIMESTAMPTZ DEFAULT NOW()
            """)
            cur.execute("""
                ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS user_id 
                VARCHAR(255) DEFAULT NULL
            """)
            # Index for efficient reflection queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_scope 
                ON knowledge (scope) WHERE scope IN ('entity', 'cross', 'temporal')
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_user 
                ON knowledge (user_id) WHERE user_id IS NOT NULL
            """)
        logger.info("✅ Migration complete (v1.7: reflection system)")

        # --- v2.2 Memory categories ---
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE entities ADD COLUMN IF NOT EXISTS metadata 
                JSONB DEFAULT '{}'
            """)
            # Index for filtering by agent_id, app_id
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_entities_metadata 
                ON entities USING gin(metadata)
            """)
        logger.info("✅ Migration complete (v2.2: memory categories)")

        # --- v2.3 TTL expiry ---
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS expires_at 
                TIMESTAMPTZ DEFAULT NULL
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_expires 
                ON facts (expires_at) WHERE expires_at IS NOT NULL
            """)
        logger.info("✅ Migration complete (v2.3: TTL expiry)")

        # --- v2.5 Episodic + Procedural memory ---
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    user_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    context TEXT,
                    outcome TEXT,
                    participants TEXT[] DEFAULT '{}',
                    emotional_valence VARCHAR(20) DEFAULT 'neutral',
                    importance FLOAT DEFAULT 0.5,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_user
                ON episodes (user_id, created_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_participants
                ON episodes USING gin(participants)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_expires
                ON episodes (expires_at) WHERE expires_at IS NOT NULL
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS episode_embeddings (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    episode_id UUID REFERENCES episodes(id) ON DELETE CASCADE,
                    chunk_text TEXT NOT NULL,
                    embedding vector(1536),
                    tsv tsvector,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ep_emb_episode
                ON episode_embeddings (episode_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ep_emb_hnsw
                ON episode_embeddings USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ep_emb_tsv
                ON episode_embeddings USING gin(tsv)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS procedures (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    user_id TEXT NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    trigger_condition TEXT,
                    steps JSONB NOT NULL DEFAULT '[]',
                    source_episode_ids UUID[] DEFAULT '{}',
                    entity_names TEXT[] DEFAULT '{}',
                    success_count INT DEFAULT 0,
                    fail_count INT DEFAULT 0,
                    last_used TIMESTAMPTZ,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ,
                    UNIQUE(user_id, name)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_procedures_user
                ON procedures (user_id, updated_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_procedures_entities
                ON procedures USING gin(entity_names)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS procedure_embeddings (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    procedure_id UUID REFERENCES procedures(id) ON DELETE CASCADE,
                    chunk_text TEXT NOT NULL,
                    embedding vector(1536),
                    tsv tsvector,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_proc_emb_procedure
                ON procedure_embeddings (procedure_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_proc_emb_hnsw
                ON procedure_embeddings USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_proc_emb_tsv
                ON procedure_embeddings USING gin(tsv)
            """)
            # --- v2.7: Experience-Driven Procedures ---
            # Episodes: link to procedure + failed step
            cur.execute("""
                ALTER TABLE episodes ADD COLUMN IF NOT EXISTS linked_procedure_id
                UUID REFERENCES procedures(id) ON DELETE SET NULL
            """)
            cur.execute("""
                ALTER TABLE episodes ADD COLUMN IF NOT EXISTS failed_at_step INT
            """)

            # Procedures: versioning
            cur.execute("""
                ALTER TABLE procedures ADD COLUMN IF NOT EXISTS version
                INT DEFAULT 1
            """)
            cur.execute("""
                ALTER TABLE procedures ADD COLUMN IF NOT EXISTS parent_version_id
                UUID REFERENCES procedures(id) ON DELETE SET NULL
            """)
            cur.execute("""
                ALTER TABLE procedures ADD COLUMN IF NOT EXISTS evolved_from_episode
                UUID REFERENCES episodes(id) ON DELETE SET NULL
            """)
            cur.execute("""
                ALTER TABLE procedures ADD COLUMN IF NOT EXISTS is_current
                BOOLEAN DEFAULT TRUE
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_procedures_current
                ON procedures(user_id, is_current) WHERE is_current = TRUE
            """)

            # --- v2.16: MCP connection tracking (for /connect/claude health check) ---
            cur.execute("""
                ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_mcp_call_at TIMESTAMPTZ
            """)

            # Procedure evolution log
            cur.execute("""
                CREATE TABLE IF NOT EXISTS procedure_evolution (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    procedure_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
                    episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
                    change_type VARCHAR(30) NOT NULL,
                    diff JSONB DEFAULT '{}',
                    version_before INT,
                    version_after INT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_proc_evolution_proc
                ON procedure_evolution(procedure_id, created_at DESC)
            """)

            # Update UNIQUE constraint: allow versioned rows
            # Drop old constraint if exists, add new one
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'procedures_user_id_name_key'
                    ) THEN
                        ALTER TABLE procedures DROP CONSTRAINT procedures_user_id_name_key;
                    END IF;
                END $$
            """)
            # Deduplicate procedures before creating unique index
            cur.execute("""
                DELETE FROM procedures p1 USING procedures p2
                WHERE p1.ctid < p2.ctid
                  AND p1.user_id = p2.user_id
                  AND p1.name = p2.name
                  AND p1.version = p2.version
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_procedures_user_name_version
                ON procedures(user_id, name, version)
            """)

        logger.info("✅ Migration complete (v2.7: experience-driven procedures)")

        # --- v2.10 Jobs table (persistent across workers/restarts) ---
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    job_type TEXT DEFAULT 'add',
                    status TEXT DEFAULT 'processing',
                    result JSONB,
                    error TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_user
                ON jobs(user_id, created_at DESC)
            """)
        logger.info("✅ Migration complete (v2.10: persistent jobs)")

        # --- v2.12 Sub-user isolation ---
        # Split into separate transactions so each table commits independently
        with self._cursor() as cur:
            # entities: add sub_user_id, update UNIQUE constraint
            cur.execute("""
                ALTER TABLE entities ADD COLUMN IF NOT EXISTS sub_user_id
                TEXT NOT NULL DEFAULT 'default'
            """)
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'entities_user_id_name_key'
                    ) THEN
                        ALTER TABLE entities DROP CONSTRAINT entities_user_id_name_key;
                    END IF;
                END $$
            """)
            # Also drop the old unique INDEX (constraint drop doesn't remove it)
            cur.execute("DROP INDEX IF EXISTS entities_user_id_name_key")
            # Deduplicate entities before creating unique constraint
            cur.execute("""
                DELETE FROM entities e1 USING entities e2
                WHERE e1.ctid < e2.ctid
                  AND e1.user_id = e2.user_id
                  AND e1.sub_user_id = e2.sub_user_id
                  AND e1.name = e2.name
            """)
            # Drop old unique index, then add proper CONSTRAINT (required for ON CONFLICT)
            cur.execute("DROP INDEX IF EXISTS idx_entities_user_sub_name")
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_entities_user_sub_name'
                    ) THEN
                        ALTER TABLE entities
                            ADD CONSTRAINT uq_entities_user_sub_name
                            UNIQUE (user_id, sub_user_id, name);
                    END IF;
                END $$
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_entities_sub_user
                ON entities(user_id, sub_user_id)
            """)
        logger.info("✅ Migration v2.12a: entities sub-user isolation")

        with self._cursor() as cur:
            # episodes: add sub_user_id
            cur.execute("""
                ALTER TABLE episodes ADD COLUMN IF NOT EXISTS sub_user_id
                TEXT NOT NULL DEFAULT 'default'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_sub_user
                ON episodes(user_id, sub_user_id, created_at DESC)
            """)
        logger.info("✅ Migration v2.12b: episodes sub-user isolation")

        with self._cursor() as cur:
            # procedures: add sub_user_id, update UNIQUE constraint
            cur.execute("""
                ALTER TABLE procedures ADD COLUMN IF NOT EXISTS sub_user_id
                TEXT NOT NULL DEFAULT 'default'
            """)
            # Drop old unique constraint (user_id, name) if it exists
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'procedures_user_id_name_key'
                    ) THEN
                        ALTER TABLE procedures DROP CONSTRAINT procedures_user_id_name_key;
                    END IF;
                END $$
            """)
            cur.execute("""
                DROP INDEX IF EXISTS idx_procedures_user_name_version
            """)
            # Deduplicate procedures before creating unique index
            cur.execute("""
                DELETE FROM procedures p1 USING procedures p2
                WHERE p1.ctid < p2.ctid
                  AND p1.user_id = p2.user_id
                  AND p1.sub_user_id = p2.sub_user_id
                  AND p1.name = p2.name
                  AND p1.version = p2.version
            """)
            cur.execute("DROP INDEX IF EXISTS idx_procedures_user_sub_name_version")
            cur.execute("DROP INDEX IF EXISTS procedures_user_id_name_key")
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_procedures_user_sub_name_ver'
                    ) THEN
                        ALTER TABLE procedures
                            ADD CONSTRAINT uq_procedures_user_sub_name_ver
                            UNIQUE (user_id, sub_user_id, name, version);
                    END IF;
                END $$
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_procedures_sub_user
                ON procedures(user_id, sub_user_id)
            """)
        logger.info("✅ Migration v2.12c: procedures sub-user isolation")

        with self._cursor() as cur:
            # knowledge: add sub_user_id
            cur.execute("""
                ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS sub_user_id
                TEXT NOT NULL DEFAULT 'default'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_sub_user
                ON knowledge(user_id, sub_user_id) WHERE user_id IS NOT NULL
            """)

            # memory_triggers: add sub_user_id
            cur.execute("""
                ALTER TABLE memory_triggers ADD COLUMN IF NOT EXISTS sub_user_id
                TEXT NOT NULL DEFAULT 'default'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_triggers_sub_user
                ON memory_triggers(user_id, sub_user_id)
            """)

            # entity_overview view — now handled by v2.17 as MATERIALIZED VIEW
            # (kept here as no-op for migration ordering, v2.17 replaces it)
        logger.info("✅ Migration complete (v2.12: sub-user isolation)")

        # --- v2.13 Temporal metadata + raw chunk indexing ---
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS event_date TEXT
            """)
            cur.execute("""
                ALTER TABLE episodes ADD COLUMN IF NOT EXISTS happened_at TEXT
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_event_date
                ON facts(event_date) WHERE event_date IS NOT NULL
            """)

            # Provenance metadata on facts (v2.16)
            cur.execute("""
                ALTER TABLE facts ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_metadata
                ON facts USING gin(metadata)
            """)

            # Raw conversation chunk storage
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_chunks (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    user_id TEXT NOT NULL,
                    sub_user_id TEXT NOT NULL DEFAULT 'default',
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_user
                ON conversation_chunks(user_id, sub_user_id)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    chunk_id UUID NOT NULL REFERENCES conversation_chunks(id) ON DELETE CASCADE,
                    embedding vector(1536),
                    tsv tsvector
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunk_emb_hnsw ON chunk_embeddings
                    USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunk_emb_tsv ON chunk_embeddings USING gin(tsv)
            """)

            logger.info("✅ Migration complete (v2.13: temporal metadata + raw chunk indexing)")

        # --- v2.15 Subscriptions + Usage counters (billing) ---
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan VARCHAR(20) NOT NULL DEFAULT 'free',
                    paddle_customer_id VARCHAR(255),
                    paddle_subscription_id VARCHAR(255),
                    status VARCHAR(20) DEFAULT 'active',
                    current_period_start TIMESTAMPTZ,
                    current_period_end TIMESTAMPTZ,
                    canceled_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_user
                ON subscriptions(user_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_subscriptions_paddle
                ON subscriptions(paddle_customer_id) WHERE paddle_customer_id IS NOT NULL
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage_counters (
                    id BIGSERIAL PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    period_start DATE NOT NULL,
                    add_count INT DEFAULT 0,
                    search_count INT DEFAULT 0,
                    agent_count INT DEFAULT 0,
                    reflect_count INT DEFAULT 0,
                    dedup_count INT DEFAULT 0,
                    reindex_count INT DEFAULT 0,
                    rules_count INT DEFAULT 0,
                    UNIQUE(user_id, period_start)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_counters_user_period
                ON usage_counters(user_id, period_start)
            """)
        logger.info("✅ Migration complete (v2.15: subscriptions + usage counters)")

        # --- v2.16 Performance indexes ---
        with self._cursor() as cur:
            # Base FK indexes (schema.sql has them, but migration path didn't)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entity ON knowledge(entity_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_entity ON embeddings(entity_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_emb_chunk ON chunk_embeddings(chunk_id)")

            # Composite partial index for the hottest query pattern (search, feed, reflect, agents)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_entity_active
                ON facts(entity_id, importance DESC, created_at DESC)
                WHERE archived = FALSE
            """)
            # Time-window queries on facts (feed, digest, reflection stats)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_facts_created
                ON facts(created_at DESC) WHERE archived = FALSE
            """)
            # Entities ORDER BY updated_at DESC (get_all_entities, entity_overview)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_entities_updated
                ON entities(user_id, sub_user_id, updated_at DESC)
            """)
            # Procedures filtered by sub_user + current
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_procedures_current_sub
                ON procedures(user_id, sub_user_id, updated_at DESC)
                WHERE is_current = TRUE
            """)
            # Episodes linked to procedures
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_linked_proc
                ON episodes(linked_procedure_id)
                WHERE linked_procedure_id IS NOT NULL
            """)
        logger.info("✅ Migration complete (v2.16: performance indexes)")

        # --- v2.17 Materialized view for entity_overview ---
        with self._cursor() as cur:
            # Check if entity_overview is a regular VIEW (not materialized) and drop it
            cur.execute("""
                SELECT COUNT(*) FROM pg_catalog.pg_views
                WHERE viewname = 'entity_overview'
            """)
            if cur.fetchone()[0] > 0:
                cur.execute("DROP VIEW entity_overview")
                logger.info("Dropped regular VIEW entity_overview → will recreate as MATERIALIZED")

            # Create materialized view (idempotent)
            cur.execute("""
                CREATE MATERIALIZED VIEW IF NOT EXISTS entity_overview AS
                SELECT e.id, e.user_id, e.sub_user_id, e.name, e.type,
                       e.created_at, e.updated_at,
                       (SELECT COUNT(*) FROM facts f WHERE f.entity_id = e.id AND f.archived = FALSE) AS facts_count,
                       (SELECT COUNT(*) FROM knowledge k WHERE k.entity_id = e.id) AS knowledge_count,
                       (SELECT COUNT(*) FROM relations r WHERE r.source_id = e.id OR r.target_id = e.id) AS relations_count
                FROM entities e
            """)
            # Unique index required for REFRESH CONCURRENTLY
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matview_eo_id ON entity_overview (id)")
            # Query indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matview_eo_user_sub ON entity_overview (user_id, sub_user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_matview_eo_facts ON entity_overview (user_id, sub_user_id, facts_count DESC)")
        logger.info("✅ Migration complete (v2.17: materialized view)")

        # --- v2.18 Rules quota counter ---
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE usage_counters
                ADD COLUMN IF NOT EXISTS rules_count INT DEFAULT 0
            """)
        logger.info("✅ Migration complete (v2.18: rules quota counter)")

        # --- v2.19 Entity/procedure case-insensitive dedup, relation cleanup ---
        with self._cursor() as cur:
            # 1. Merge case-insensitive duplicate entities
            cur.execute("""
                DO $$
                DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN
                        SELECT user_id, sub_user_id, LOWER(name) as lname,
                               array_agg(id ORDER BY length(name) DESC, updated_at DESC) as ids,
                               (array_agg(id ORDER BY length(name) DESC, updated_at DESC))[1] as canonical_id
                        FROM entities
                        GROUP BY user_id, sub_user_id, LOWER(name)
                        HAVING count(*) > 1
                    LOOP
                        -- Move facts from duplicates to canonical
                        UPDATE facts SET entity_id = r.canonical_id
                        WHERE entity_id = ANY(r.ids[2:])
                        AND NOT EXISTS (
                            SELECT 1 FROM facts f2
                            WHERE f2.entity_id = r.canonical_id AND f2.content = facts.content
                        );
                        DELETE FROM facts WHERE entity_id = ANY(r.ids[2:]);
                        -- Move relations (source), skip self-relations
                        UPDATE relations SET source_id = r.canonical_id
                        WHERE source_id = ANY(r.ids[2:])
                        AND target_id != r.canonical_id
                        AND NOT EXISTS (
                            SELECT 1 FROM relations r2
                            WHERE r2.source_id = r.canonical_id AND r2.target_id = relations.target_id AND r2.type = relations.type
                        );
                        DELETE FROM relations WHERE source_id = ANY(r.ids[2:]);
                        -- Move relations (target), skip self-relations
                        UPDATE relations SET target_id = r.canonical_id
                        WHERE target_id = ANY(r.ids[2:])
                        AND source_id != r.canonical_id
                        AND NOT EXISTS (
                            SELECT 1 FROM relations r2
                            WHERE r2.source_id = relations.source_id AND r2.target_id = r.canonical_id AND r2.type = relations.type
                        );
                        DELETE FROM relations WHERE target_id = ANY(r.ids[2:]);
                        -- Move embeddings
                        UPDATE embeddings SET entity_id = r.canonical_id
                        WHERE entity_id = ANY(r.ids[2:])
                        AND NOT EXISTS (
                            SELECT 1 FROM embeddings e2 WHERE e2.entity_id = r.canonical_id
                        );
                        DELETE FROM embeddings WHERE entity_id = ANY(r.ids[2:]);
                        -- Delete duplicate entities
                        DELETE FROM entities WHERE id = ANY(r.ids[2:]);
                    END LOOP;
                END $$
            """)
            # Case-insensitive unique index (prevents future duplicates)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_user_sub_lname
                ON entities (user_id, sub_user_id, LOWER(name))
            """)

            # 2. Merge case-insensitive duplicate procedures
            cur.execute("""
                DO $$
                DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN
                        SELECT user_id, sub_user_id, LOWER(name) as lname, version,
                               array_agg(id ORDER BY updated_at DESC) as ids,
                               (array_agg(id ORDER BY updated_at DESC))[1] as canonical_id
                        FROM procedures
                        GROUP BY user_id, sub_user_id, LOWER(name), version
                        HAVING count(*) > 1
                    LOOP
                        -- Move embeddings to canonical
                        UPDATE procedure_embeddings SET procedure_id = r.canonical_id
                        WHERE procedure_id = ANY(r.ids[2:])
                        AND NOT EXISTS (
                            SELECT 1 FROM procedure_embeddings pe2 WHERE pe2.procedure_id = r.canonical_id
                        );
                        DELETE FROM procedure_embeddings WHERE procedure_id = ANY(r.ids[2:]);
                        -- Move evolution records
                        UPDATE procedure_evolution SET procedure_id = r.canonical_id
                        WHERE procedure_id = ANY(r.ids[2:]);
                        -- Delete duplicate procedures
                        DELETE FROM procedures WHERE id = ANY(r.ids[2:]);
                    END LOOP;
                END $$
            """)
            # Case-insensitive unique index for procedures
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_procedures_user_sub_lname_ver
                ON procedures (user_id, sub_user_id, LOWER(name), version)
            """)

            # 3. Clean relations: self-referential and reverse duplicates
            cur.execute("DELETE FROM relations WHERE source_id = target_id")
            cur.execute("""
                DELETE FROM relations r1
                USING relations r2
                WHERE r1.source_id = r2.target_id
                  AND r1.target_id = r2.source_id
                  AND r1.type = r2.type
                  AND r1.created_at > r2.created_at
            """)

            # Prevent self-referential relations (idempotent)
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'chk_no_self_relation' AND table_name = 'relations'
                    ) THEN
                        ALTER TABLE relations ADD CONSTRAINT chk_no_self_relation
                        CHECK (source_id != target_id);
                    END IF;
                END $$
            """)
        logger.info("✅ Migration complete (v2.19: entity/procedure CI dedup, relation cleanup)")

        # --- v2.20: Cohere multilingual embeddings (1024-dim) — additive column ---
        # Adds embedding_v2 vector(1024) NULL to all four embedding tables so we
        # can dual-store / migrate to Cohere embed-multilingual-v3.0 without
        # downtime. HNSW indexes on embedding_v2 are created lazily — only once
        # backfill produces enough data that an index is meaningful.
        with self._cursor() as cur:
            cur.execute("ALTER TABLE embeddings           ADD COLUMN IF NOT EXISTS embedding_v2 vector(1024)")
            cur.execute("ALTER TABLE episode_embeddings   ADD COLUMN IF NOT EXISTS embedding_v2 vector(1024)")
            cur.execute("ALTER TABLE chunk_embeddings     ADD COLUMN IF NOT EXISTS embedding_v2 vector(1024)")
            cur.execute("ALTER TABLE procedure_embeddings ADD COLUMN IF NOT EXISTS embedding_v2 vector(1024)")
        logger.info("✅ Migration complete (v2.20: embedding_v2 column for Cohere multilingual)")

        # --- v2.21: HNSW indexes on embedding_v2 (idempotent CREATE INDEX IF NOT EXISTS) ---
        # Postgres skips index creation when the column has only NULLs — but the
        # statement is safe to run repeatedly. After backfill these become active.
        with self._cursor() as cur:
            try:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_embeddings_v2_hnsw
                    ON embeddings USING hnsw (embedding_v2 vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ep_emb_v2_hnsw
                    ON episode_embeddings USING hnsw (embedding_v2 vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunk_emb_v2_hnsw
                    ON chunk_embeddings USING hnsw (embedding_v2 vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_proc_emb_v2_hnsw
                    ON procedure_embeddings USING hnsw (embedding_v2 vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """)
            except Exception as e:
                # Indexes can fail to build on empty/all-NULL columns in some pgvector versions
                logger.warning(f"v2 HNSW index creation deferred: {e}")
        logger.info("✅ Migration complete (v2.21: HNSW indexes on embedding_v2)")

        # ---- v2.22: Memory Health monitoring (per-search retrieval quality) ----
        # Tracks cosine score + detected language per search query so we can
        # surface "memory health" to customers (silent-churn detection).
        # Inspired by feedback from @brianchase2882 on Reddit — most users
        # don't complain when retrieval is bad, they just stop using it.
        with self._cursor() as cur:
            cur.execute("""
                ALTER TABLE usage_log
                ADD COLUMN IF NOT EXISTS query_score FLOAT,
                ADD COLUMN IF NOT EXISTS query_language VARCHAR(8)
            """)
            # query_score mixes two scales (cosine ~0.3-0.9 vs RRF ~0.017-0.05
            # depending on code path) — result_quality is the scale-aware label
            # (strong/weak/no_match) so analytics never misread RRF as failure.
            cur.execute("""
                ALTER TABLE usage_log
                ADD COLUMN IF NOT EXISTS result_quality VARCHAR(10)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_usage_search_health
                ON usage_log(user_id, created_at DESC)
                WHERE action = 'search' AND query_score IS NOT NULL
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_health (
                    user_id UUID PRIMARY KEY,
                    computed_at TIMESTAMPTZ DEFAULT NOW(),
                    overall_status VARCHAR(16),
                    details JSONB,
                    recommendations TEXT[],
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_health_status_time
                ON memory_health(overall_status, computed_at DESC)
            """)
        logger.info("✅ Migration complete (v2.22: memory health tracking)")

        with self._cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(42)")

    # ---- Job tracking (PostgreSQL-backed, survives restarts) ----

    def create_job(self, user_id: str, job_type: str = "add") -> str:
        """Create a background job in PostgreSQL, return job_id."""
        job_id = f"job-{secrets.token_urlsafe(12)}"
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO jobs (id, user_id, job_type, status)
                   VALUES (%s, %s, %s, 'processing')""",
                (job_id, user_id, job_type)
            )
        # Cleanup old jobs (>1h) periodically
        self._cleanup_jobs()
        return job_id

    def complete_job(self, job_id: str, result: dict = None):
        """Mark job as completed in PostgreSQL."""
        with self._cursor() as cur:
            cur.execute(
                """UPDATE jobs SET status = 'completed', result = %s
                   WHERE id = %s""",
                (json.dumps(result, default=str) if result else None, job_id)
            )

    def fail_job(self, job_id: str, error: str):
        """Mark job as failed in PostgreSQL."""
        with self._cursor() as cur:
            cur.execute(
                """UPDATE jobs SET status = 'failed', error = %s
                   WHERE id = %s""",
                (error, job_id)
            )

    def get_job(self, job_id: str, user_id: str) -> Optional[dict]:
        """Get job status from PostgreSQL (only if owned by user)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, status, job_type, result, error
                   FROM jobs WHERE id = %s AND user_id = %s""",
                (job_id, user_id)
            )
            row = cur.fetchone()
            if not row:
                return None
            result = row["result"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    pass
            return {
                "job_id": row["id"],
                "status": row["status"],
                "type": row["job_type"],
                "result": result,
                "error": row["error"],
            }

    def _cleanup_jobs(self):
        """Mark stuck jobs as failed (>5 min processing) and remove old completed jobs (>1h)."""
        try:
            with self._cursor() as cur:
                # Mark stuck processing jobs as failed
                cur.execute("""
                    UPDATE jobs SET status = 'failed', error = 'Timed out (server restart or processing exceeded 5 min)'
                    WHERE status = 'processing' AND created_at < NOW() - INTERVAL '5 minutes'
                """)
                stuck = cur.rowcount
                if stuck > 0:
                    logger.info(f"♻️ Recovered {stuck} stuck jobs (marked as failed)")
                # Remove old completed/failed jobs
                cur.execute(
                    "DELETE FROM jobs WHERE created_at < NOW() - INTERVAL '1 hour'"
                )
        except Exception:
            pass

    def close(self):
        if self._pool:
            self._pool.closeall()
            logger.info("Connection pool closed")
        if self.conn:
            self.conn.close()
