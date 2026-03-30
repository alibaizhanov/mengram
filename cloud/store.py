"""
Mengram Cloud Storage — PostgreSQL backend.

Replaces VaultManager (local .md files) with PostgreSQL + pgvector.
Same interface, different storage.

Usage:
    store = CloudStore(database_url="postgresql://...")
    store.save_entity("PostgreSQL", "technology", facts=[...], relations=[...], knowledge=[...])
    results = store.search("database pool", user_id="...", top_k=5)
"""

import datetime
import hashlib
import json
import logging
import math
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger("mengram")


def _safe_parse_json(raw: str, fallback=None):
    """Parse JSON from LLM output with multiple fallback strategies."""
    clean = raw.strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Strip markdown fences (handles text before ```)
    if "```" in clean:
        start = clean.find("```")
        end = clean.rfind("```")
        if start != end:
            inner = clean[start:end]
            lines = inner.split("\n", 1)
            if len(lines) > 1:
                try:
                    result = json.loads(lines[1])
                    logger.debug("JSON parsed via markdown fence stripping")
                    return result
                except (json.JSONDecodeError, ValueError):
                    pass

    # Strategy 3: Find outermost { } or [ ]
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = clean.find(open_ch)
        end = clean.rfind(close_ch)
        if start >= 0 and end > start:
            try:
                result = json.loads(clean[start:end + 1])
                logger.debug("JSON parsed via bracket extraction")
                return result
            except (json.JSONDecodeError, ValueError):
                pass

    logger.warning(f"⚠️ All JSON parse strategies failed, returning fallback (input length: {len(raw)})")
    return fallback


# ---- TTL Cache (Redis or in-memory) ----
class TTLCache:
    """Thread-safe cache with TTL. Uses Redis if available, falls back to in-memory.
    In-memory fallback has max-size eviction to prevent unbounded growth."""
    MAX_MEMORY_KEYS = 10_000  # Evict oldest entries when exceeded

    def __init__(self, default_ttl: int = 60, redis_url: str = None):
        self._store = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self._redis = None

        if redis_url:
            try:
                import redis as _redis
                self._redis = _redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                logger.info("Redis cache connected")
            except Exception as e:
                logger.warning(f"Redis unavailable, falling back to in-memory cache: {e}")
                self._redis = None

    def get(self, key: str):
        if self._redis:
            try:
                val = self._redis.get(f"mc:{key}")
                return json.loads(val) if val else None
            except Exception:
                pass
        with self._lock:
            item = self._store.get(key)
            if item and item["expires"] > time.time():
                return item["value"]
            if item:
                del self._store[key]
            return None

    def set(self, key: str, value, ttl: int = None):
        ttl = ttl or self.default_ttl
        if self._redis:
            try:
                self._redis.setex(f"mc:{key}", ttl, json.dumps(value, default=str))
                return
            except Exception:
                pass
        with self._lock:
            # Evict expired + oldest if over limit
            if len(self._store) >= self.MAX_MEMORY_KEYS:
                now = time.time()
                # First pass: remove expired
                expired = [k for k, v in self._store.items() if v["expires"] <= now]
                for k in expired:
                    del self._store[k]
                # Second pass: evict oldest 20% if still over limit
                if len(self._store) >= self.MAX_MEMORY_KEYS:
                    sorted_keys = sorted(self._store, key=lambda k: self._store[k]["expires"])
                    for k in sorted_keys[:len(sorted_keys) // 5]:
                        del self._store[k]
            self._store[key] = {
                "value": value,
                "expires": time.time() + ttl
            }

    def invalidate(self, prefix: str = ""):
        if self._redis:
            try:
                # Use SCAN instead of KEYS to avoid blocking Redis
                pattern = "mc:*" if not prefix else f"mc:{prefix}*"
                cursor = 0
                while True:
                    cursor, keys = self._redis.scan(cursor, match=pattern, count=200)
                    if keys:
                        self._redis.delete(*keys)
                    if cursor == 0:
                        break
                return
            except Exception:
                pass
        with self._lock:
            if not prefix:
                self._store.clear()
            else:
                keys = [k for k in self._store if k.startswith(prefix)]
                for k in keys:
                    del self._store[k]

    def stats(self) -> dict:
        if self._redis:
            try:
                info = self._redis.info("keyspace")
                db_keys = 0
                for db_info in info.values():
                    if isinstance(db_info, dict):
                        db_keys += db_info.get("keys", 0)
                return {"total_keys": db_keys, "alive": db_keys, "backend": "redis"}
            except Exception:
                pass
        with self._lock:
            now = time.time()
            alive = sum(1 for v in self._store.values() if v["expires"] > now)
            return {"total_keys": len(self._store), "alive": alive, "backend": "memory"}


@dataclass
class CloudEntity:
    id: str
    name: str
    type: str
    facts: list[str]
    relations: list[dict]
    knowledge: list[dict]
    metadata: dict = None


class CloudStore:
    """
    PostgreSQL storage backend for Mengram Cloud.
    
    Features:
    - Connection pooling (ThreadedConnectionPool) for concurrent requests
    - TTL cache for frequent reads (stats, entities, insights)
    - Auto-reconnect on connection failures
    - Proper logging
    """

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
                        -- Move relations (source)
                        UPDATE relations SET source_id = r.canonical_id
                        WHERE source_id = ANY(r.ids[2:])
                        AND NOT EXISTS (
                            SELECT 1 FROM relations r2
                            WHERE r2.source_id = r.canonical_id AND r2.target_id = relations.target_id AND r2.type = relations.type
                        );
                        DELETE FROM relations WHERE source_id = ANY(r.ids[2:]);
                        -- Move relations (target)
                        UPDATE relations SET target_id = r.canonical_id
                        WHERE target_id = ANY(r.ids[2:])
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

    # ---- Auth ----

    def create_user(self, email: str) -> str:
        """Create user, return user_id."""
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO users (email) VALUES (%s) RETURNING id",
                (email,)
            )
            return str(cur.fetchone()[0])

    def get_user_by_email(self, email: str) -> Optional[str]:
        """Get user_id by email."""
        with self._cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            return str(row[0]) if row else None

    def get_user_email(self, user_id: str) -> Optional[str]:
        """Get email by user_id."""
        with self._cursor() as cur:
            cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def create_api_key(self, user_id: str, name: str = "default") -> str:
        """Generate API key, store hash, return raw key."""
        raw_key = f"om-{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:10]

        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO api_keys (user_id, key_hash, key_prefix, name)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, key_hash, key_prefix, name)
            )
        return raw_key

    def verify_api_key(self, raw_key: str) -> Optional[str]:
        """Verify API key, return user_id or None. Cached 60s."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        # Check cache first
        cache_key = f"auth:{key_hash[:16]}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        with self._cursor() as cur:
            cur.execute(
                """SELECT user_id FROM api_keys 
                   WHERE key_hash = %s AND is_active = TRUE""",
                (key_hash,)
            )
            row = cur.fetchone()
            if row:
                user_id = str(row[0])
                # Cache the result for 60s
                self.cache.set(cache_key, user_id, ttl=60)
                # Update last_used (non-blocking, skip if fails)
                try:
                    cur.execute(
                        "UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = %s",
                        (key_hash,)
                    )
                except Exception:
                    pass
                return user_id
            # Cache negative result too (prevents brute force DB hits)
            self.cache.set(cache_key, False, ttl=30)
            return None

    def list_api_keys(self, user_id: str) -> list:
        """List all API keys for a user (without hashes)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, key_prefix, is_active, created_at, last_used_at
                   FROM api_keys WHERE user_id = %s
                   ORDER BY created_at DESC""",
                (user_id,)
            )
            return [{
                "id": r["id"],
                "name": r["name"],
                "prefix": r["key_prefix"],
                "active": r["is_active"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_used": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            } for r in cur.fetchall()]

    def revoke_api_key(self, user_id: str, key_id: str) -> bool:
        """Revoke a specific API key by ID."""
        with self._cursor() as cur:
            cur.execute(
                """UPDATE api_keys SET is_active = FALSE
                   WHERE id = %s AND user_id = %s AND is_active = TRUE
                   RETURNING key_hash""",
                (key_id, user_id)
            )
            row = cur.fetchone()
            if row:
                # Invalidate cache for this key
                self.cache.invalidate(f"auth:{row[0][:16]}")
                return True
            return False

    def rename_api_key(self, user_id: str, key_id: str, new_name: str) -> bool:
        """Rename an API key."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET name = %s WHERE id = %s AND user_id = %s",
                (new_name, key_id, user_id)
            )
            return cur.rowcount > 0

    def reset_api_key(self, user_id: str) -> str:
        """Deactivate all old keys and create a new one."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET is_active = FALSE WHERE user_id = %s",
                (user_id,)
            )
        # Invalidate all auth cache
        self.cache.invalidate("auth:")
        return self.create_api_key(user_id)

    # ---- OAuth ----

    def save_email_code(self, email: str, code: str):
        """Save email verification code (expires in 10 min)."""
        with self._cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS email_codes (
                    email TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
            cur.execute(
                """INSERT INTO email_codes (email, code, created_at) 
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (email) DO UPDATE SET code = %s, created_at = NOW()""",
                (email, code, code)
            )

    def verify_email_code(self, email: str, code: str) -> bool:
        """Verify email code (valid for 10 min)."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT 1 FROM email_codes 
                   WHERE email = %s AND code = %s 
                   AND created_at > NOW() - INTERVAL '10 minutes'""",
                (email, code)
            )
            if cur.fetchone():
                cur.execute("DELETE FROM email_codes WHERE email = %s", (email,))
                return True
            return False

    # ---- Drip Emails ----

    def ensure_drip_emails_table(self):
        """Create drip_emails table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS drip_emails (
                    id SERIAL PRIMARY KEY,
                    email TEXT NOT NULL,
                    user_id UUID,
                    drip_type VARCHAR(30) NOT NULL,
                    sent_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(email, drip_type)
                )
            """)

    def try_record_drip(self, email: str, drip_type: str, user_id: str = None) -> bool:
        """Record a drip email send. Returns True if recorded (=should send), False if duplicate."""
        self.ensure_drip_emails_table()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO drip_emails (email, user_id, drip_type)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (email, drip_type) DO NOTHING
                   RETURNING id""",
                (email, user_id, drip_type)
            )
            return cur.fetchone() is not None

    def get_inactive_completed_signups(self, hours: int, drip_type: str) -> list:
        """Find completed signups with no API activity after N hours.
        Only considers users who signed up within the last 30 days."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN api_keys ak ON ak.user_id = u.id
                   WHERE u.created_at < NOW() - make_interval(hours => %s)
                     AND u.created_at > NOW() - INTERVAL '30 days'
                     AND ak.last_used_at IS NULL
                     AND ak.is_active = TRUE
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )""",
                (hours, drip_type)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def get_incomplete_signups_for_drip(self, hours: int, drip_type: str) -> list:
        """Find incomplete signups (pending verification) after N hours.
        Only considers codes created within the last 7 days to avoid spamming old entries."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT ec.email
                   FROM email_codes ec
                   WHERE ec.created_at < NOW() - make_interval(hours => %s)
                     AND ec.created_at > NOW() - INTERVAL '7 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM users u WHERE u.email = ec.email
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = ec.email AND de.drip_type = %s
                     )""",
                (hours, drip_type)
            )
            return [{"email": r["email"]} for r in cur.fetchall()]

    def is_email_unsubscribed(self, email: str) -> bool:
        """Check if an email has unsubscribed from drip emails."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT 1 FROM drip_emails
                   WHERE email = %s AND drip_type = 'unsubscribed'""",
                (email,)
            )
            return cur.fetchone() is not None

    def unsubscribe_email(self, email: str) -> bool:
        """Unsubscribe an email from drip emails. Returns True if newly unsubscribed."""
        self.ensure_drip_emails_table()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO drip_emails (email, drip_type)
                   VALUES (%s, 'unsubscribed')
                   ON CONFLICT (email, drip_type) DO NOTHING
                   RETURNING id""",
                (email,)
            )
            return cur.fetchone() is not None

    def get_users_added_no_search(self, min_adds: int = 3, drip_type: str = "added_no_search") -> list:
        """Find users who added memories but never searched (likely don't know search exists)."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN usage_log ac ON ac.user_id = u.id
                   WHERE u.created_at > NOW() - INTERVAL '30 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )
                   GROUP BY u.id, u.email
                   HAVING count(*) FILTER (WHERE ac.action = 'add') >= %s
                      AND count(*) FILTER (WHERE ac.action IN ('search', 'search_all')) = 0
                      AND max(ac.created_at) < NOW() - INTERVAL '24 hours'""",
                (drip_type, min_adds)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def get_users_searched_no_add(self, min_searches: int = 3, drip_type: str = "searched_no_add") -> list:
        """Find users who searched but never added memories (empty search results)."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN usage_log ac ON ac.user_id = u.id
                   WHERE u.created_at > NOW() - INTERVAL '30 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )
                   GROUP BY u.id, u.email
                   HAVING count(*) FILTER (WHERE ac.action IN ('search', 'search_all')) >= %s
                      AND count(*) FILTER (WHERE ac.action = 'add') = 0
                      AND max(ac.created_at) < NOW() - INTERVAL '24 hours'""",
                (drip_type, min_searches)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def get_churned_active_users(self, min_actions: int = 10, inactive_hours: int = 168, drip_type: str = "churned_7d") -> list:
        """Find users who were actively using the API but stopped for 7+ days."""
        self.ensure_drip_emails_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT u.id, u.email
                   FROM users u
                   JOIN usage_log ac ON ac.user_id = u.id
                   WHERE u.created_at > NOW() - INTERVAL '90 days'
                     AND NOT EXISTS (
                         SELECT 1 FROM drip_emails de
                         WHERE de.email = u.email AND de.drip_type = %s
                     )
                   GROUP BY u.id, u.email
                   HAVING count(*) FILTER (WHERE ac.action IN ('add', 'search', 'search_all')) >= %s
                      AND max(ac.created_at) < NOW() - make_interval(hours => %s)""",
                (drip_type, min_actions, inactive_hours)
            )
            return [{"id": str(r["id"]), "email": r["email"]} for r in cur.fetchall()]

    def save_oauth_code(self, code: str, user_id: str, redirect_uri: str, state: str):
        """Save OAuth authorization code (expires in 5 min)."""
        with self._cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS oauth_codes (
                    code TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    redirect_uri TEXT,
                    state TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
            cur.execute(
                """INSERT INTO oauth_codes (code, user_id, redirect_uri, state)
                   VALUES (%s, %s, %s, %s)""",
                (code, user_id, redirect_uri, state)
            )

    def verify_oauth_code(self, code: str) -> Optional[dict]:
        """Verify and consume OAuth code. Returns {user_id, redirect_uri, state} or None."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT user_id, redirect_uri, state FROM oauth_codes
                   WHERE code = %s AND created_at > NOW() - INTERVAL '5 minutes'""",
                (code,)
            )
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM oauth_codes WHERE code = %s", (code,))
                return {"user_id": str(row["user_id"]), "redirect_uri": row["redirect_uri"], "state": row["state"]}
            return None

    # ---- Entities ----

    def _find_primary_person(self, user_id: str, sub_user_id: str = "default") -> Optional[tuple]:
        """Find the primary person entity for this user.
        Prefers: full name (has space) > most facts > most recent."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT e.id, e.name, COUNT(f.id) as fact_count,
                          CASE WHEN e.name LIKE '%% %%' THEN 1 ELSE 0 END as has_full_name
                   FROM entities e
                   LEFT JOIN facts f ON f.entity_id = e.id AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.type = 'person' AND LOWER(e.name) != 'user'
                   GROUP BY e.id, e.name
                   ORDER BY has_full_name DESC, fact_count DESC, e.updated_at DESC
                   LIMIT 1""",
                (user_id, sub_user_id)
            )
            row = cur.fetchone()
            if row:
                return (str(row[0]), row[1])
            return None

    def find_duplicate(self, user_id: str, name: str, sub_user_id: str = "default") -> Optional[tuple]:
        """Find existing entity that matches this name.
        Only matches if: same type context AND one name is a complete word prefix/suffix of the other.
        Returns (entity_id, canonical_name) or None."""
        name_lower = name.strip().lower()
        if not name_lower or len(name_lower) < 3:
            return None

        with self._cursor() as cur:
            # Find entities where one name starts with the other + space
            # e.g. "Ali" matches "Ali Baizhanov" but "Rust" does NOT match "Rustem"
            cur.execute(
                """SELECT id, name, type FROM entities
                   WHERE user_id = %s AND sub_user_id = %s AND name != %s
                   AND (
                       LOWER(name) LIKE %s || ' %%'
                       OR %s LIKE LOWER(name) || ' %%'
                       OR LOWER(name) = %s
                   )""",
                (user_id, sub_user_id, name, name_lower, name_lower, name_lower)
            )
            matches = cur.fetchall()
            if not matches:
                return None

            # Pick the longest name as canonical
            best = max(matches, key=lambda m: len(m[1]))
            canonical_name = best[1] if len(best[1]) >= len(name) else name
            return (str(best[0]), canonical_name)

    def merge_entities(self, user_id: str, source_id: str, target_id: str,
                       target_name: str):
        """Merge source entity into target. Moves facts, relations, knowledge, embeddings."""
        with self._cursor() as cur:
            # Move facts (skip duplicates)
            cur.execute(
                """INSERT INTO facts (entity_id, content)
                   SELECT %s, content FROM facts WHERE entity_id = %s
                   ON CONFLICT (entity_id, content) DO NOTHING""",
                (target_id, source_id)
            )

            # Move knowledge (skip duplicates)
            cur.execute(
                """INSERT INTO knowledge (entity_id, type, title, content, artifact)
                   SELECT %s, type, title, content, artifact FROM knowledge WHERE entity_id = %s
                   ON CONFLICT (entity_id, title) DO NOTHING""",
                (target_id, source_id)
            )

            # Move relations — update source_id references
            cur.execute(
                """UPDATE relations SET source_id = %s 
                   WHERE source_id = %s 
                   AND NOT EXISTS (
                       SELECT 1 FROM relations r2 
                       WHERE r2.source_id = %s AND r2.target_id = relations.target_id AND r2.type = relations.type
                   )""",
                (target_id, source_id, target_id)
            )
            cur.execute(
                """UPDATE relations SET target_id = %s 
                   WHERE target_id = %s 
                   AND NOT EXISTS (
                       SELECT 1 FROM relations r2 
                       WHERE r2.source_id = relations.source_id AND r2.target_id = %s AND r2.type = relations.type
                   )""",
                (target_id, source_id, target_id)
            )

            # Move embeddings
            cur.execute(
                "UPDATE embeddings SET entity_id = %s WHERE entity_id = %s",
                (target_id, source_id)
            )

            # Delete leftover relations and source entity
            cur.execute("DELETE FROM relations WHERE source_id = %s OR target_id = %s", (source_id, source_id))
            cur.execute("DELETE FROM facts WHERE entity_id = %s", (source_id,))
            cur.execute("DELETE FROM knowledge WHERE entity_id = %s", (source_id,))
            cur.execute("DELETE FROM entities WHERE id = %s", (source_id,))

        logger.info(f"🔀 Merged entity {source_id} into {target_id} ({target_name})")

    def _auto_merge_duplicate_entities(self, user_id: str, sub_user_id: str = "default") -> int:
        """Find and merge case-insensitive duplicate entities. Returns merge count."""
        merged = 0
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT LOWER(name) as lname,
                       array_agg(id ORDER BY length(name) DESC, updated_at DESC) as ids,
                       array_agg(name ORDER BY length(name) DESC, updated_at DESC) as names
                FROM entities
                WHERE user_id = %s AND sub_user_id = %s
                GROUP BY LOWER(name)
                HAVING count(*) > 1
            """, (user_id, sub_user_id))
            dupes = cur.fetchall()

        for dupe in dupes:
            canonical_id = str(dupe["ids"][0])
            canonical_name = dupe["names"][0]
            for dup_id in dupe["ids"][1:]:
                try:
                    self.merge_entities(user_id, str(dup_id), canonical_id, canonical_name)
                    merged += 1
                except Exception as e:
                    logger.warning(f"⚠️ Entity merge failed {dup_id} → {canonical_id}: {e}")
        return merged

    def save_entity(self, user_id: str, name: str, type: str,
                    facts: list[str] = None,
                    relations: list[dict] = None,
                    knowledge: list[dict] = None,
                    metadata: dict = None,
                    expires_at: str = None,
                    sub_user_id: str = "default",
                    fact_dates: dict = None) -> str:
        """
        Create or update entity with facts, relations, knowledge.
        Auto-deduplicates: merges if similar entity exists.
        Returns entity_id.
        """
        # Normalize: if name is ALL CAPS and >3 chars, title-case it
        if name == name.upper() and len(name) > 3 and ' ' not in name:
            name = name.capitalize()

        meta_json = json.dumps(metadata) if metadata else '{}'

        # ---- "User" resolution: merge into primary person entity ----
        if name.lower() == "user" and type == "person":
            primary = self._find_primary_person(user_id, sub_user_id=sub_user_id)
            if primary:
                entity_id, canonical_name = primary
                logger.info(f"🔀 User → '{canonical_name}' (id: {entity_id})")
                with self._cursor() as cur:
                    cur.execute("UPDATE entities SET updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s", (meta_json, entity_id))
                self._add_facts_knowledge_relations(entity_id, user_id, canonical_name, facts, relations, knowledge, expires_at=expires_at, fact_dates=fact_dates, sub_user_id=sub_user_id, metadata=metadata)
                return entity_id

        # Check for case-insensitive exact match first
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, name FROM entities WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)",
                (user_id, sub_user_id, name)
            )
            exact = cur.fetchone()
            if exact:
                entity_id = str(exact[0])
                existing_name = exact[1]
                # Upgrade type if currently unknown and we have a real type
                cur.execute("SELECT type FROM entities WHERE id = %s", (entity_id,))
                current_type = cur.fetchone()[0]
                should_update_type = (current_type == 'unknown' and type and type != 'unknown')
                # Keep the more "normal" casing (not all-caps)
                if existing_name != name:
                    better_name = name if name != name.upper() else existing_name
                    if better_name != existing_name:
                        try:
                            if should_update_type:
                                cur.execute(
                                    "UPDATE entities SET name = %s, type = %s, updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s",
                                    (better_name, type, meta_json, entity_id)
                                )
                            else:
                                cur.execute(
                                    "UPDATE entities SET name = %s, updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s",
                                    (better_name, meta_json, entity_id)
                                )
                        except (psycopg2.IntegrityError, psycopg2.errors.UniqueViolation):
                            logger.info(f"🔀 Entity rename skipped (conflict): '{existing_name}' → '{better_name}'")
                    elif should_update_type:
                        cur.execute("UPDATE entities SET type = %s, updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s", (type, meta_json, entity_id))
                    else:
                        cur.execute("UPDATE entities SET updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s", (meta_json, entity_id))
                elif should_update_type:
                    cur.execute("UPDATE entities SET type = %s, updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s", (type, meta_json, entity_id))
                else:
                    cur.execute("UPDATE entities SET updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s", (meta_json, entity_id))

                # Add facts, knowledge, relations below
                self._add_facts_knowledge_relations(entity_id, user_id, name, facts, relations, knowledge, expires_at=expires_at, fact_dates=fact_dates, sub_user_id=sub_user_id, metadata=metadata)
                return entity_id

        # Check for duplicate entity (word-boundary match)
        duplicate = self.find_duplicate(user_id, name, sub_user_id=sub_user_id)
        if duplicate:
            existing_id, canonical_name = duplicate
            if len(name) > len(canonical_name):
                canonical_name = name
                try:
                    with self._cursor() as cur:
                        cur.execute(
                            "UPDATE entities SET name = %s, type = %s, updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s",
                            (canonical_name, type, meta_json, existing_id)
                        )
                except (psycopg2.IntegrityError, psycopg2.errors.UniqueViolation):
                    logger.info(f"🔀 Dedup rename conflict: '{name}' already exists, using existing")
                    with self._cursor() as cur:
                        cur.execute(
                            "SELECT id FROM entities WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)",
                            (user_id, sub_user_id, name)
                        )
                        row = cur.fetchone()
                        if row:
                            existing_id = str(row[0])
            else:
                with self._cursor() as cur:
                    cur.execute(
                        "UPDATE entities SET type = %s, updated_at = NOW(), metadata = metadata || %s::jsonb WHERE id = %s",
                        (type, meta_json, existing_id)
                    )
            entity_id = existing_id
            logger.info(f"🔀 Dedup: '{name}' → '{canonical_name}' (id: {entity_id})")
        else:
            try:
                with self._cursor() as cur:
                    cur.execute(
                        """INSERT INTO entities (user_id, sub_user_id, name, type, metadata)
                           VALUES (%s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT ON CONSTRAINT uq_entities_user_sub_name
                           DO UPDATE SET type = EXCLUDED.type, updated_at = NOW(),
                              metadata = entities.metadata || EXCLUDED.metadata
                           RETURNING id""",
                        (user_id, sub_user_id, name, type, meta_json)
                    )
                    entity_id = str(cur.fetchone()[0])
            except (psycopg2.IntegrityError, psycopg2.errors.UniqueViolation):
                # Race condition: concurrent thread inserted same entity
                logger.info(f"🔀 Entity race condition resolved: '{name}' for user {user_id[:8]}")
                with self._cursor() as cur:
                    cur.execute(
                        "SELECT id FROM entities WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)",
                        (user_id, sub_user_id, name)
                    )
                    row = cur.fetchone()
                    if row:
                        entity_id = str(row[0])
                    else:
                        raise

        self._add_facts_knowledge_relations(entity_id, user_id, name, facts, relations, knowledge, expires_at=expires_at, fact_dates=fact_dates, sub_user_id=sub_user_id, metadata=metadata)
        return entity_id

    @staticmethod
    def estimate_importance(fact: str) -> float:
        """Estimate fact importance 0.0-1.0 based on content patterns."""
        if not isinstance(fact, str):
            fact = str(fact)
        f = fact.lower().strip()

        # Identity / role — highest
        if any(p in f for p in [
            'is a ', 'works as', 'works at', 'ceo of', 'founder of',
            'created by', 'built by', 'lives in', 'born in', 'age ',
            'studies at', 'graduated from', 'native language',
            'citizenship', 'nationality'
        ]):
            return 0.9

        # Skills / tech stack — high
        if any(p in f for p in [
            'uses ', 'primary language', 'tech stack', 'proficient in',
            'expert in', 'main database', 'built with', 'powered by',
            'written in', 'developed in', 'architecture'
        ]):
            return 0.8

        # Long-term preferences — medium-high
        if any(p in f for p in [
            'prefers ', 'always ', 'never ', 'favorite', 'hates',
            'allergic', 'dietary', 'philosophy', 'likes ', 'loves ',
            'enjoys ', 'dislikes ', 'avoids '
        ]):
            return 0.7

        # Goals / plans — medium
        if any(p in f for p in [
            'wants to', 'plans to', 'goal', 'learning', 'interested in',
            'considering', 'thinking about', 'exploring'
        ]):
            return 0.6

        # Current state — medium-low
        if any(p in f for p in [
            'currently', 'right now', 'working on', 'building',
            'deployed', 'version', 'released'
        ]):
            return 0.5

        # Default
        return 0.5

    def _add_facts_knowledge_relations(self, entity_id: str, user_id: str, name: str,
                                        facts: list[str] = None,
                                        relations: list[dict] = None,
                                        knowledge: list[dict] = None,
                                        expires_at: str = None,
                                        fact_dates: dict = None,
                                        sub_user_id: str = "default",
                                        metadata: dict = None):
        """Add facts, knowledge, and relations to an entity."""
        added_facts = []
        meta_json = json.dumps(metadata) if metadata else '{}'
        with self._cursor() as cur:
            for fact in (facts or []):
                importance = self.estimate_importance(fact)
                event_date = (fact_dates or {}).get(fact)
                if expires_at:
                    cur.execute(
                        """INSERT INTO facts (entity_id, content, importance, expires_at, event_date, metadata)
                           VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (entity_id, content) DO UPDATE SET
                               event_date = COALESCE(EXCLUDED.event_date, facts.event_date),
                               metadata = facts.metadata || EXCLUDED.metadata
                           RETURNING content""",
                        (entity_id, fact, importance, expires_at, event_date, meta_json)
                    )
                else:
                    cur.execute(
                        """INSERT INTO facts (entity_id, content, importance, event_date, metadata)
                           VALUES (%s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (entity_id, content) DO UPDATE SET
                               event_date = COALESCE(EXCLUDED.event_date, facts.event_date),
                               metadata = facts.metadata || EXCLUDED.metadata
                           RETURNING content""",
                        (entity_id, fact, importance, event_date, meta_json)
                    )
                row = cur.fetchone()
                if row:
                    added_facts.append(fact)
            for k in (knowledge or []):
                cur.execute(
                    """INSERT INTO knowledge (entity_id, type, title, content, artifact)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (entity_id, title) DO NOTHING""",
                    (entity_id, k.get("type", "insight"), k.get("title", ""),
                     k.get("content", ""), k.get("artifact"))
                )

        for rel in (relations or []):
            self._save_relation(user_id, entity_id, name, rel, sub_user_id=sub_user_id)

        # Fire webhooks for new facts
        if added_facts:
            self.fire_webhooks(user_id, "memory_add", {
                "entity": name,
                "facts": added_facts,
                "count": len(added_facts)
            })

        self._schedule_matview_refresh()
        return entity_id

    def _save_relation(self, user_id: str, source_entity_id: str,
                       source_name: str, rel: dict, sub_user_id: str = "default"):
        """Save relation, creating target entity if needed."""
        target_name = rel.get("target", "")
        if not target_name:
            return

        with self._cursor() as cur:
            # Ensure target entity exists
            try:
                cur.execute(
                    """INSERT INTO entities (user_id, sub_user_id, name, type)
                       VALUES (%s, %s, %s, 'unknown')
                       ON CONFLICT ON CONSTRAINT uq_entities_user_sub_name DO NOTHING""",
                    (user_id, sub_user_id, target_name)
                )
            except (psycopg2.IntegrityError, psycopg2.errors.UniqueViolation):
                pass  # Entity already exists, that's fine
        with self._cursor() as cur:
            cur.execute(
                "SELECT id FROM entities WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)",
                (user_id, sub_user_id, target_name)
            )
            row = cur.fetchone()
            if not row:
                return
            target_id = str(row[0])

            direction = rel.get("direction", "outgoing")
            if direction == "outgoing":
                src, tgt = source_entity_id, target_id
            else:
                src, tgt = target_id, source_entity_id

            # Prevent self-referential relations
            if src == tgt:
                return

            rel_type = rel.get("type", "related_to")

            # Prevent circular A→B + B→A with same type
            cur.execute(
                "SELECT 1 FROM relations WHERE source_id = %s AND target_id = %s AND type = %s",
                (tgt, src, rel_type)
            )
            if cur.fetchone():
                return

            cur.execute(
                """INSERT INTO relations (source_id, target_id, type, description)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (source_id, target_id, type) DO NOTHING""",
                (src, tgt, rel_type, rel.get("description", ""))
            )

        # Invalidate caches after write
        self.cache.invalidate(f"stats:{user_id}")

    def get_entity_id(self, user_id: str, name: str, sub_user_id: str = "default") -> Optional[str]:
        """Get entity ID by name."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id FROM entities WHERE user_id = %s AND sub_user_id = %s AND name = %s",
                (user_id, sub_user_id, name)
            )
            row = cur.fetchone()
            return str(row[0]) if row else None

    def get_entity(self, user_id: str, name: str, sub_user_id: str = "default") -> Optional[CloudEntity]:
        """Get entity with all data."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT id, name, type, metadata FROM entities WHERE user_id = %s AND sub_user_id = %s AND name = %s",
                (user_id, sub_user_id, name)
            )
            row = cur.fetchone()
            if not row:
                return None

            entity_id = str(row["id"])

            # Facts (exclude archived)
            cur.execute("SELECT content FROM facts WHERE entity_id = %s AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())", (entity_id,))
            facts = [r["content"] for r in cur.fetchall()]

            # Relations
            cur.execute(
                """SELECT r.type, 'outgoing' as direction, e.name as target, r.description
                   FROM relations r
                   JOIN entities e ON e.id = r.target_id
                   WHERE r.source_id = %s
                   UNION ALL
                   SELECT r.type, 'incoming' as direction, e.name as target, r.description
                   FROM relations r
                   JOIN entities e ON e.id = r.source_id
                   WHERE r.target_id = %s""",
                (entity_id, entity_id)
            )
            relations = [dict(r) for r in cur.fetchall()]

            # Knowledge
            cur.execute(
                "SELECT type, title, content, artifact FROM knowledge WHERE entity_id = %s",
                (entity_id,)
            )
            knowledge = [dict(r) for r in cur.fetchall()]

            return CloudEntity(
                id=entity_id,
                name=row["name"],
                type=row["type"],
                facts=facts,
                relations=relations,
                knowledge=knowledge,
                metadata=row.get("metadata") or {},
            )

    def get_all_entities(self, user_id: str, sub_user_id: str = "default",
                         limit: int = None, offset: int = 0) -> list[dict] | tuple[list[dict], int]:
        """List all entities with counts (excludes internal entities).
        If limit is provided, returns (entities, total) tuple with SQL-side pagination."""
        with self._cursor(dict_cursor=True) as cur:
            if limit is not None:
                cur.execute(
                    "SELECT COUNT(*) FROM entities WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%'",
                    (user_id, sub_user_id)
                )
                total = cur.fetchone()[0]
                cur.execute(
                    """SELECT name, type, facts_count, knowledge_count, relations_count
                       FROM entity_overview WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%'
                       ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                    (user_id, sub_user_id, limit, offset)
                )
                return [dict(r) for r in cur.fetchall()], total
            cur.execute(
                """SELECT name, type, facts_count, knowledge_count, relations_count
                   FROM entity_overview WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%'
                   ORDER BY updated_at DESC""",
                (user_id, sub_user_id)
            )
            return [dict(r) for r in cur.fetchall()]

    def get_all_entities_full(self, user_id: str, sub_user_id: str = "default") -> list[dict]:
        """Get ALL entities with full facts, relations, knowledge in 4 queries total."""
        with self._cursor(dict_cursor=True) as cur:
            # 1. Get all entities
            cur.execute(
                "SELECT id, name, type FROM entities WHERE user_id = %s AND sub_user_id = %s ORDER BY updated_at DESC",
                (user_id, sub_user_id)
            )
            entities = cur.fetchall()
            if not entities:
                return []

            entity_ids = [str(e["id"]) for e in entities]
            entity_map = {str(e["id"]): {
                "entity": e["name"],
                "type": e["type"],
                "facts": [],
                "relations": [],
                "knowledge": [],
            } for e in entities}

            # 2. Batch all facts (exclude archived)
            cur.execute(
                "SELECT entity_id, content FROM facts WHERE entity_id = ANY(%s::uuid[]) AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())",
                (entity_ids,)
            )
            for row in cur.fetchall():
                eid = str(row["entity_id"])
                if eid in entity_map:
                    entity_map[eid]["facts"].append(row["content"])

            # 3. Batch all relations
            cur.execute(
                """SELECT r.source_id, r.target_id, r.type, r.description,
                          se.name as source_name, te.name as target_name
                   FROM relations r
                   JOIN entities se ON se.id = r.source_id
                   JOIN entities te ON te.id = r.target_id
                   WHERE r.source_id = ANY(%s::uuid[]) OR r.target_id = ANY(%s::uuid[])""",
                (entity_ids, entity_ids)
            )
            for row in cur.fetchall():
                src_id = str(row["source_id"])
                tgt_id = str(row["target_id"])
                rel = {"type": row["type"], "detail": row["description"] or ""}
                if src_id in entity_map:
                    entity_map[src_id]["relations"].append(
                        {**rel, "direction": "outgoing", "target": row["target_name"]})
                if tgt_id in entity_map and tgt_id != src_id:
                    entity_map[tgt_id]["relations"].append(
                        {**rel, "direction": "incoming", "target": row["source_name"]})

            # 4. Batch all knowledge
            cur.execute(
                "SELECT entity_id, type, title, content, artifact FROM knowledge WHERE entity_id = ANY(%s::uuid[])",
                (entity_ids,)
            )
            for row in cur.fetchall():
                eid = str(row["entity_id"])
                if eid in entity_map:
                    entity_map[eid]["knowledge"].append({
                        "type": row["type"],
                        "title": row["title"],
                        "content": row["content"],
                        "artifact": row["artifact"],
                    })

            return [entity_map[str(e["id"])] for e in entities]

    def get_existing_context(self, user_id: str, max_entities: int = 40, max_facts_per: int = 10, sub_user_id: str = "default") -> str:
        """Get compact summary of existing entities for extraction context.
        Resolves 'User' to primary person name.
        Returns a string like:
          The user's name is Ali Baizhanov. Always use this name instead of "User".
          - Ali Baizhanov (person): works as developer, uses Python, lives in Almaty
          - Mengram (project): AI memory protocol, built with FastAPI
        """
        # Find primary person name
        primary = self._find_primary_person(user_id, sub_user_id=sub_user_id)
        primary_name = primary[1] if primary else None

        with self._cursor(dict_cursor=True) as cur:
            # Get top entities by recent activity
            cur.execute(
                """SELECT e.id, e.name, e.type
                   FROM entities e
                   WHERE e.user_id = %s AND e.sub_user_id = %s
                   ORDER BY e.updated_at DESC NULLS LAST
                   LIMIT %s""",
                (user_id, sub_user_id, max_entities)
            )
            entities = cur.fetchall()
            if not entities:
                if primary_name:
                    return f'The user\'s name is "{primary_name}". Always use this name instead of "User".'
                return ""

            entity_ids = [str(e["id"]) for e in entities]

            # Get top facts per entity (by importance)
            cur.execute(
                """SELECT DISTINCT ON (entity_id, content) entity_id, content, importance
                   FROM facts 
                   WHERE entity_id = ANY(%s::uuid[]) AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY entity_id, content, importance DESC""",
                (entity_ids,)
            )
            facts_by_entity = {}
            for row in cur.fetchall():
                eid = str(row["entity_id"])
                if eid not in facts_by_entity:
                    facts_by_entity[eid] = []
                facts_by_entity[eid].append((row["content"], float(row["importance"] or 0.5)))

            # Sort each entity's facts by importance, take top N
            for eid in facts_by_entity:
                facts_by_entity[eid].sort(key=lambda x: x[1], reverse=True)
                facts_by_entity[eid] = facts_by_entity[eid][:max_facts_per]

            lines = []
            # Add name hint if known
            if primary_name:
                lines.append(f'The user\'s name is "{primary_name}". Always use "{primary_name}" instead of "User".')

            for e in entities:
                eid = str(e["id"])
                name = e["name"]
                # Skip "User" and "_reflections" from context
                if name.lower() in ("user", "_reflections"):
                    continue
                facts = facts_by_entity.get(eid, [])
                if facts:
                    fact_strs = ", ".join(f[0] for f in facts)
                    lines.append(f"- {name} ({e['type']}): {fact_strs}")
                else:
                    lines.append(f"- {name} ({e['type']})")

            # Add top reflections for richer context
            reflections = self.get_reflections(user_id, sub_user_id=sub_user_id)
            if reflections:
                top_refs = [r for r in reflections if r["confidence"] >= 0.7][:3]
                if top_refs:
                    lines.append("\nAI-generated insights (use for context, don't re-extract):")
                    for r in top_refs:
                        lines.append(f"  [{r['scope']}] {r['content'][:200]}")

            return "\n".join(lines)

    # ---- Materialized View Refresh ----

    def refresh_entity_overview(self):
        """Refresh materialized view concurrently (non-blocking reads during refresh)."""
        try:
            with self._cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY entity_overview")
            logger.debug("Refreshed entity_overview matview")
        except Exception as e:
            logger.warning(f"Failed to refresh entity_overview: {e}")

    def _schedule_matview_refresh(self):
        """Schedule a debounced refresh of entity_overview (max once per 5s)."""
        cache_key = "matview_refresh_pending"
        if self.cache.get(cache_key):
            return  # Already scheduled recently
        self.cache.set(cache_key, True, ttl=5)
        import threading
        threading.Thread(target=self.refresh_entity_overview, daemon=True).start()

    def delete_entity(self, user_id: str, name: str, sub_user_id: str = "default") -> bool:
        """Delete entity and all related data."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM entities WHERE user_id = %s AND sub_user_id = %s AND name = %s RETURNING id",
                (user_id, sub_user_id, name)
            )
            deleted = cur.fetchone() is not None
        if deleted:
            self.cache.invalidate(f"stats:{user_id}")
            self._schedule_matview_refresh()
        return deleted

    def delete_all_entities(self, user_id: str, sub_user_id: str = "default") -> int:
        """Delete ALL entities (and cascade to facts, relations, knowledge, embeddings)."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM entities WHERE user_id = %s AND sub_user_id = %s RETURNING id",
                (user_id, sub_user_id)
            )
            count = cur.rowcount
        self.cache.invalidate(f"stats:{user_id}")
        self.cache.invalidate(f"graph:{user_id}:{sub_user_id}:150")
        self._schedule_matview_refresh()
        return count

    # ---- MMR Diversification ----

    def _mmr_select(self, candidates: list[tuple], entity_info: dict,
                    top_k: int, lambda_param: float = 0.7) -> list[tuple]:
        """Maximal Marginal Relevance: select results that are relevant AND diverse.
        candidates: [(entity_id, score), ...] sorted by score descending.
        entity_info: {eid: (name, type, updated_at)}.
        """
        if len(candidates) <= top_k:
            return candidates
        selected = [candidates[0]]
        remaining = list(candidates[1:])
        while len(selected) < top_k and remaining:
            best_idx, best_mmr = 0, float('-inf')
            for i, (eid, score) in enumerate(remaining):
                etype = entity_info.get(eid, ("?", "?", None, {}))[1]
                ename = entity_info.get(eid, ("?", "?", None, {}))[0].lower()
                max_sim = 0
                for sel_id, _ in selected:
                    sel_type = entity_info.get(sel_id, ("?", "?", None, {}))[1]
                    sel_name = entity_info.get(sel_id, ("?", "?", None, {}))[0].lower()
                    type_sim = 0.5 if etype == sel_type else 0
                    name_sim = 0.5 if (ename in sel_name or sel_name in ename) else 0
                    max_sim = max(max_sim, type_sim + name_sim)
                mmr = lambda_param * score - (1 - lambda_param) * max_sim
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i
            selected.append(remaining.pop(best_idx))
        return selected

    # ---- Graph Traversal ----

    def _graph_expand(self, cur, user_id: str, seed_ids: list[str],
                      max_hops: int = 2, max_rrf: float = 0.01,
                      sub_user_id: str = "default") -> dict:
        """
        Multi-hop graph traversal from seed entities via relations.
        Returns: {entity_id: {"name", "type", "updated_at", "score", "hop", "via_relation"}}
        """
        if not seed_ids or max_hops < 1:
            return {}

        visited = set(seed_ids)
        graph_entities = {}
        current_seeds = seed_ids

        for hop in range(1, max_hops + 1):
            if not current_seeds:
                break

            hop_score = max_rrf * (0.5 ** hop)

            cur.execute(
                """SELECT
                       CASE WHEN r.source_id = ANY(%s::uuid[]) THEN r.target_id ELSE r.source_id END AS related_id,
                       CASE WHEN r.source_id = ANY(%s::uuid[]) THEN te.name ELSE se.name END AS related_name,
                       CASE WHEN r.source_id = ANY(%s::uuid[]) THEN te.type ELSE se.type END AS related_type,
                       CASE WHEN r.source_id = ANY(%s::uuid[]) THEN te.updated_at ELSE se.updated_at END AS related_updated,
                       r.type AS rel_type
                   FROM relations r
                   JOIN entities se ON se.id = r.source_id
                   JOIN entities te ON te.id = r.target_id
                   WHERE (r.source_id = ANY(%s::uuid[]) OR r.target_id = ANY(%s::uuid[]))
                     AND se.user_id = %s AND se.sub_user_id = %s""",
                (current_seeds, current_seeds, current_seeds, current_seeds,
                 current_seeds, current_seeds, user_id, sub_user_id)
            )

            next_seeds = []
            for row in cur.fetchall():
                rid = str(row["related_id"])
                if rid in visited:
                    continue
                visited.add(rid)

                if rid not in graph_entities:
                    graph_entities[rid] = {
                        "name": row["related_name"],
                        "type": row["related_type"],
                        "updated_at": row["related_updated"],
                        "score": hop_score,
                        "hop": hop,
                        "via_relation": row["rel_type"],
                    }
                    next_seeds.append(rid)

                # Hard cap: don't expand beyond 50 graph entities total
                if len(graph_entities) >= 50:
                    break

            current_seeds = next_seeds[:15]

        return graph_entities

    # ---- Search ----

    def search_vector(self, user_id: str, embedding: list[float],
                      top_k: int = 5, min_score: float = 0.2,
                      query_text: str = "",
                      graph_depth: int = 2,
                      sub_user_id: str = "default",
                      meta_filters: dict = None) -> list[dict]:
        """
        Hybrid search: vector + BM25 text + graph expansion.

        Pipeline:
        1. Vector search (semantic similarity via pgvector)
        2. BM25 text search (exact keyword match via tsvector)
        3. Reciprocal Rank Fusion to merge results
        4. Graph expansion: follow relations to find connected entities
        5. Recency boost + dedup + limit
        """
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"

        # Build metadata filter clause
        meta_clause = ""
        meta_params = []
        if meta_filters:
            meta_clause = " AND e.metadata @> %s::jsonb"
            meta_params = [json.dumps(meta_filters)]

        with self._cursor(dict_cursor=True) as cur:

            # ========== STAGE 1: Vector search ==========
            cur.execute(
                f"""SELECT DISTINCT ON (e.id)
                       e.id, e.name, e.type,
                       1 - (emb.embedding <=> %s::vector) AS score,
                       e.updated_at, e.metadata
                   FROM embeddings emb
                   JOIN entities e ON e.id = emb.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s
                     AND 1 - (emb.embedding <=> %s::vector) > %s
                     {meta_clause}
                   ORDER BY e.id, score DESC""",
                (embedding_str, user_id, sub_user_id, embedding_str, min_score, *meta_params)
            )
            vector_rows = cur.fetchall()
            # Rank by score
            vector_rows.sort(key=lambda r: float(r["score"]), reverse=True)

            # Cosine floor: if best vector result < 0.25, query is unrelated to anything in memory
            if vector_rows and float(vector_rows[0]["score"]) < 0.25:
                return []

            vector_ranked = {str(r["id"]): (i + 1, r) for i, r in enumerate(vector_rows[:40])}

            # ========== STAGE 2: BM25 text search ==========
            bm25_ranked = {}
            if query_text:
                # Build tsquery: split words, join with &
                words = [w.strip() for w in query_text.split() if len(w.strip()) >= 2]
                if words:
                    # Use plainto_tsquery for robustness (handles any language)
                    cur.execute(
                        f"""SELECT DISTINCT ON (e.id)
                               e.id, e.name, e.type,
                               ts_rank_cd(emb.tsv, plainto_tsquery('english', %s), 32) AS rank,
                               e.updated_at, e.metadata
                           FROM embeddings emb
                           JOIN entities e ON e.id = emb.entity_id
                           WHERE e.user_id = %s AND e.sub_user_id = %s
                             AND emb.tsv @@ plainto_tsquery('english', %s)
                             {meta_clause}
                           ORDER BY e.id, rank DESC""",
                        (query_text, user_id, sub_user_id, query_text, *meta_params)
                    )
                    bm25_rows = cur.fetchall()
                    bm25_rows.sort(key=lambda r: float(r["rank"]), reverse=True)
                    bm25_ranked = {str(r["id"]): (i + 1, r) for i, r in enumerate(bm25_rows[:20])}

                    # Also search entity names directly (ILIKE)
                    cur.execute(
                        """SELECT id, name, type, updated_at, metadata
                           FROM entities
                           WHERE user_id = %s AND sub_user_id = %s AND (
                               name ILIKE %s OR name ILIKE %s
                           )""",
                        (user_id, sub_user_id, f"%{query_text}%", f"%{'%'.join(words)}%")
                    )
                    for i, row in enumerate(cur.fetchall()):
                        eid = str(row["id"])
                        if eid not in bm25_ranked:
                            bm25_ranked[eid] = (i + 1, row)

            # ========== STAGE 3: Reciprocal Rank Fusion ==========
            k = 60  # RRF constant
            all_entity_ids = set(vector_ranked.keys()) | set(bm25_ranked.keys())
            
            rrf_scores = {}
            entity_info = {}  # id -> (name, type, updated_at, metadata)
            
            for eid in all_entity_ids:
                score = 0.0
                if eid in vector_ranked:
                    rank, row = vector_ranked[eid]
                    score += 1.0 / (k + rank)
                    entity_info[eid] = (row["name"], row["type"], row.get("updated_at"), row.get("metadata") or {})
                if eid in bm25_ranked:
                    rank, row = bm25_ranked[eid]
                    score += 1.0 / (k + rank)
                    if eid not in entity_info:
                        entity_info[eid] = (row["name"], row["type"], row.get("updated_at"), row.get("metadata") or {})
                rrf_scores[eid] = score

            # ========== STAGE 4: Graph expansion (multi-hop) ==========
            sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            seed_ids = [eid for eid, _ in sorted_rrf[:8]]
            max_rrf = max(rrf_scores.values()) if rrf_scores else 0.01

            graph_entities = self._graph_expand(
                cur, user_id, seed_ids, max_hops=graph_depth, max_rrf=max_rrf,
                sub_user_id=sub_user_id
            )
            graph_expanded_ids = set()
            for eid, info in graph_entities.items():
                rrf_scores[eid] = info["score"]
                entity_info[eid] = (info["name"], info["type"], info.get("updated_at"), {})
                graph_expanded_ids.add(eid)

            # ========== STAGE 5: Recency boost + build results ==========
            now = datetime.datetime.now(datetime.timezone.utc)
            final_scores = {}
            for eid, base_score in rrf_scores.items():
                score = base_score
                if eid in entity_info:
                    updated_at = entity_info[eid][2]
                    if updated_at:
                        try:
                            age_days = (now - updated_at.replace(tzinfo=datetime.timezone.utc)).days
                            recency_boost = 1.0 + 0.3 * math.exp(-0.05 * age_days)
                            score *= recency_boost
                        except Exception:
                            pass
                final_scores[eid] = score

            # Sort, filter by minimum RRF score, and diversify via MMR
            # Direct matches: fixed floor 0.01. Graph-expanded: stricter adaptive floor.
            sorted_final = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
            top_score = sorted_final[0][1] if sorted_final else 0
            min_rrf_graph = max(0.01, top_score * 0.4)
            filtered = [(eid, score) for eid, score in sorted_final
                        if (eid in graph_expanded_ids and score >= min_rrf_graph) or
                           (eid not in graph_expanded_ids and score >= 0.01)]
            top_entities = self._mmr_select(filtered, entity_info, top_k)

            if not top_entities:
                return []

            # ========== STAGE 6: Batch load details ==========
            entity_ids = [eid for eid, _ in top_entities]
            entity_map = {}
            for eid, score in top_entities:
                name, etype, _, emeta = entity_info.get(eid, ("?", "?", None, {}))
                entity_map[eid] = {
                    "entity": name,
                    "type": etype,
                    "score": round(score, 4),
                    "metadata": emeta or {},
                    "facts": [],
                    "relations": [],
                    "knowledge": [],
                    "_graph": eid in graph_expanded_ids,
                }

            # Batch facts (exclude archived) — sorted by importance
            cur.execute(
                """SELECT id, entity_id, content, importance, access_count, last_accessed, event_date
                   FROM facts WHERE entity_id = ANY(%s::uuid[]) AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY importance DESC""",
                (entity_ids,)
            )
            fact_ids_accessed = []
            for row in cur.fetchall():
                eid = str(row["entity_id"])
                if eid in entity_map:
                    # Apply Ebbinghaus decay: importance * e^(-0.03 * days_since_access)
                    base_imp = float(row["importance"] or 0.5)
                    if row["last_accessed"]:
                        try:
                            days_since = (now - row["last_accessed"].replace(
                                tzinfo=datetime.timezone.utc)).days
                            decay = math.exp(-0.03 * days_since)
                        except Exception:
                            decay = 1.0
                    else:
                        decay = 0.8  # never accessed = slight penalty
                    # Access frequency boost: log(1 + access_count) * 0.05
                    access_boost = math.log1p(row["access_count"] or 0) * 0.05
                    effective_imp = min(base_imp * decay + access_boost, 1.0)

                    entity_map[eid]["facts"].append({
                        "content": row["content"],
                        "importance": round(effective_imp, 3),
                        "event_date": row.get("event_date"),
                    })
                    fact_ids_accessed.append(str(row["id"]))

            # Fact-level relevance: rank facts by embedding similarity to query
            chunk_relevance = {}  # eid → {fact_content → relevance_score}
            if entity_ids:
                cur.execute(
                    """SELECT entity_id, chunk_text,
                              1 - (embedding <=> %s::vector) AS relevance
                       FROM embeddings
                       WHERE entity_id = ANY(%s::uuid[])""",
                    (embedding_str, entity_ids)
                )
                for row in cur.fetchall():
                    eid = str(row["entity_id"])
                    text = row["chunk_text"]
                    rel = float(row["relevance"])
                    fact_text = text.split(": ", 1)[1] if ": " in text else text
                    if eid not in chunk_relevance:
                        chunk_relevance[eid] = {}
                    chunk_relevance[eid][fact_text] = max(
                        chunk_relevance[eid].get(fact_text, 0), rel
                    )

            # Sort facts by combined relevance + importance, adaptive cap (10-20)
            max_entity_score = max((entity_map[eid]["score"] for eid in entity_map), default=1.0)
            for eid in entity_map:
                relevances = chunk_relevance.get(eid, {})
                score_ratio = entity_map[eid]["score"] / max_entity_score if max_entity_score > 0 else 0
                max_facts = 10 + int(10 * score_ratio)  # top entity: 20, weakest: ~10
                sorted_facts = sorted(
                    entity_map[eid]["facts"],
                    key=lambda f: (
                        0.7 * relevances.get(f["content"], 0) +
                        0.3 * f["importance"]
                    ),
                    reverse=True
                )
                # Filter out facts with low relevance to the query (reduce junk)
                sorted_facts = [
                    f for f in sorted_facts
                    if relevances.get(f["content"], 0) >= 0.15
                ][:max_facts]
                entity_map[eid]["facts"] = [
                    f"[{f['event_date']}] {f['content']}" if f.get("event_date")
                    else f["content"]
                    for f in sorted_facts
                ]

            # Track fact access — update access_count and last_accessed
            if fact_ids_accessed:
                cur.execute(
                    """UPDATE facts 
                       SET access_count = access_count + 1, last_accessed = NOW()
                       WHERE id = ANY(%s::uuid[])""",
                    (fact_ids_accessed,)
                )

            # Batch relations
            cur.execute(
                """SELECT r.source_id, r.target_id, r.type, r.description,
                          se.name as source_name, te.name as target_name
                   FROM relations r
                   JOIN entities se ON se.id = r.source_id
                   JOIN entities te ON te.id = r.target_id
                   WHERE r.source_id = ANY(%s::uuid[]) OR r.target_id = ANY(%s::uuid[])""",
                (entity_ids, entity_ids)
            )
            for row in cur.fetchall():
                src_id = str(row["source_id"])
                tgt_id = str(row["target_id"])
                rel = {
                    "type": row["type"],
                    "detail": row["description"] or "",
                }
                if src_id in entity_map:
                    rel_out = {**rel, "direction": "outgoing", "target": row["target_name"]}
                    entity_map[src_id]["relations"].append(rel_out)
                if tgt_id in entity_map and tgt_id != src_id:
                    rel_in = {**rel, "direction": "incoming", "target": row["source_name"]}
                    entity_map[tgt_id]["relations"].append(rel_in)

            # Batch knowledge
            cur.execute(
                "SELECT entity_id, type, title, content, artifact FROM knowledge WHERE entity_id = ANY(%s::uuid[])",
                (entity_ids,)
            )
            for row in cur.fetchall():
                eid = str(row["entity_id"])
                if eid in entity_map:
                    entity_map[eid]["knowledge"].append({
                        "type": row["type"],
                        "title": row["title"],
                        "content": row["content"],
                        "artifact": row["artifact"],
                    })

            # Return in score order
            return [entity_map[eid] for eid, _ in top_entities if eid in entity_map]

    def search_text(self, user_id: str, query: str, top_k: int = 5,
                    sub_user_id: str = "default") -> list[dict]:
        """Fallback text search (ILIKE)."""
        pattern = f"%{query}%"
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT DISTINCT e.name, e.type
                   FROM entities e
                   LEFT JOIN facts f ON f.entity_id = e.id
                   LEFT JOIN knowledge k ON k.entity_id = e.id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND (
                       e.name ILIKE %s
                       OR f.content ILIKE %s
                       OR k.content ILIKE %s
                       OR k.title ILIKE %s
                   )
                   LIMIT %s""",
                (user_id, sub_user_id, pattern, pattern, pattern, pattern, top_k)
            )
            results = []
            for row in cur.fetchall():
                entity = self.get_entity(user_id, row["name"], sub_user_id=sub_user_id)
                if entity:
                    results.append({
                        "entity": entity.name,
                        "type": entity.type,
                        "score": 0.5,
                        "metadata": entity.metadata or {},
                        "facts": entity.facts,
                        "relations": [r for r in entity.relations],
                        "knowledge": [k for k in entity.knowledge],
                    })
            return results

    def search_temporal(self, user_id: str, after: str = None, before: str = None,
                        top_k: int = 20, sub_user_id: str = "default") -> list[dict]:
        """Search facts by time range. Returns entities with facts created in the window.
        Uses event_date for temporal queries (actual event time, not ingestion time).
        Falls back to created_at only if no event_date data exists."""
        with self._cursor(dict_cursor=True) as cur:
            conditions = ["e.user_id = %s", "e.sub_user_id = %s", "f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())",
                          "f.event_date IS NOT NULL"]
            params = [user_id, sub_user_id]

            if after:
                conditions.append("f.event_date >= %s")
                params.append(after)
            if before:
                conditions.append("f.event_date <= %s")
                params.append(before)

            where = " AND ".join(conditions)
            cur.execute(
                f"""SELECT e.name, e.type, f.content, f.event_date, f.created_at
                    FROM facts f
                    JOIN entities e ON e.id = f.entity_id
                    WHERE {where}
                    ORDER BY f.event_date DESC
                    LIMIT %s""",
                (*params, top_k)
            )

            # Group by entity
            entity_map = {}
            for row in cur.fetchall():
                name = row["name"]
                if name not in entity_map:
                    entity_map[name] = {
                        "entity": name,
                        "type": row["type"],
                        "facts": [],
                    }
                entity_map[name]["facts"].append({
                    "content": row["content"],
                    "event_date": row["event_date"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })

            return list(entity_map.values())

    def archive_contradicted_facts(self, entity_id: str, new_facts: list[str],
                                    llm_client) -> list[str]:
        """Use LLM to find old facts contradicted by new ones. Archive them.
        Returns list of archived fact contents."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT content FROM facts WHERE entity_id = %s AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())",
                (entity_id,)
            )
            old_facts = [r["content"] for r in cur.fetchall()]

        if not old_facts or not new_facts:
            return []

        # Ask LLM which old facts are contradicted
        prompt = f"""Given these EXISTING facts about an entity:
{json.dumps(old_facts)}

And these NEW facts being added:
{json.dumps(new_facts)}

Which existing facts should be REMOVED? Remove facts that are:
1. CONTRADICTED by new facts (e.g. old: "lives in Almaty" vs new: "relocated to Dubai")
2. OBVIOUSLY WRONG given the new context (e.g. "is a programming language" on a person entity that has fact "is a colleague")
3. DUPLICATED or redundant with new facts

Return ONLY JSON: {{"remove": ["old fact 1", "old fact 2"]}} or {{"remove": []}} if none.
No markdown, no explanation."""

        try:
            contradicted = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict) and "remove" in result:
                    contradicted = result["remove"]
                    if isinstance(contradicted, list):
                        break
                elif isinstance(result, list):
                    # Fallback: LLM returned bare array (non-OpenAI providers)
                    contradicted = result
                    break
            if not isinstance(contradicted, list):
                return []
        except Exception as e:
            logger.error(f"⚠️ Conflict resolution failed: {e}")
            return []

        # Archive contradicted facts
        archived = []
        for old_fact in contradicted:
            if old_fact in old_facts:
                with self._cursor() as cur:
                    # Find matching new fact for superseded_by
                    superseded_by = new_facts[0] if new_facts else None
                    cur.execute(
                        """UPDATE facts SET archived = TRUE, superseded_by = %s
                           WHERE entity_id = %s AND content = %s AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())""",
                        (superseded_by, entity_id, old_fact)
                    )
                archived.append(old_fact)
                logger.info(f"📦 Archived: '{old_fact}' → superseded by '{superseded_by}'")

        return archived

    def dedup_entity_facts(self, entity_id: str, entity_name: str, llm_client) -> dict:
        """Use LLM to deduplicate facts on an entity. 
        Groups similar facts, keeps the best one, archives the rest.
        Returns {kept: [...], archived: [...]}"""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT content FROM facts WHERE entity_id = %s AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW()) ORDER BY importance DESC, created_at DESC",
                (entity_id,)
            )
            facts = [r["content"] for r in cur.fetchall()]

        if len(facts) < 3:
            return {"kept": facts, "archived": []}

        prompt = f"""You are a fact deduplication system.

Entity: "{entity_name}"
Facts:
{json.dumps(facts, ensure_ascii=False)}

Many of these facts say the SAME thing in different words. Your job:
1. Group duplicate/redundant facts together
2. For each group, pick the SINGLE BEST version (most concise, accurate, normalized)
3. Return JSON with facts to KEEP and facts to ARCHIVE

Rules for picking the best:
- Shorter and more specific beats longer and vague
- "specializes in Java/Spring Boot" beats "specializes in Java" + "specializes in Spring Boot" (combined is better)
- If one fact is strictly more informative, keep that one
- "works in Almaty, Kazakhstan" beats "works in Almaty" (more context)
- Remove truly obsolete facts only if a newer one clearly replaces it

Return ONLY this JSON (no markdown):
{{
  "keep": ["fact1", "fact2", ...],
  "archive": ["redundant1", "redundant2", ...]
}}"""

        try:
            result = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict) and "archive" in result:
                    break
                logger.warning(f"⚠️ Dedup JSON invalid (attempt {attempt + 1}/2), retrying...")
            if not isinstance(result, dict) or "archive" not in result:
                logger.error("⚠️ Dedup failed after 2 attempts")
                return {"kept": facts, "archived": []}
        except Exception as e:
            logger.error(f"⚠️ Dedup failed: {e}")
            return {"kept": facts, "archived": []}

        archived = []
        to_archive = result.get("archive", [])
        for fact in to_archive:
            if fact in facts:
                with self._cursor() as cur:
                    cur.execute(
                        """UPDATE facts SET archived = TRUE, superseded_by = 'dedup'
                           WHERE entity_id = %s AND content = %s AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())""",
                        (entity_id, fact)
                    )
                    if cur.rowcount > 0:
                        archived.append(fact)

        kept = result.get("keep", [])
        logger.info(f"🧹 Dedup '{entity_name}': {len(facts)} → {len(facts)-len(archived)} facts ({len(archived)} archived)")
        return {"kept": kept, "archived": archived}

    # ---- Reflection Engine ----

    REFLECTION_PROMPT = """You are a cognitive memory system that synthesizes insights from raw facts.

ENTITIES AND FACTS:
{facts_text}

EXISTING REFLECTIONS (update if stale):
{prev_reflections}

Generate reflections in 3 categories:

1. ENTITY REFLECTIONS — for entities with 3+ facts, write a 2-3 sentence summary.
   Focus: what/who it is, relation to the user, current status.

2. CROSS-ENTITY PATTERNS — patterns across multiple entities.
   Focus: career direction, tech preferences, behavioral patterns, relationships.

3. TEMPORAL — what changed recently based on fact timestamps.
   Focus: new interests, shifting priorities, recent activity.

Rate confidence 0.0-1.0 based on how well-supported by facts.

Return ONLY JSON (no markdown):
{{
  "entity_reflections": [
    {{"entity": "EntityName", "title": "short title", "reflection": "2-3 sentences", "confidence": 0.9, "key_facts": ["fact1", "fact2"]}}
  ],
  "cross_entity": [
    {{"entities": ["E1", "E2"], "title": "short title", "reflection": "2-3 sentences", "confidence": 0.85}}
  ],
  "temporal": [
    {{"period": "recent", "title": "short title", "reflection": "2-3 sentences", "confidence": 0.8}}
  ]
}}"""

    def get_reflection_stats(self, user_id: str, sub_user_id: str = "default") -> dict:
        """Get stats to decide if reflection is needed."""
        with self._cursor(dict_cursor=True) as cur:
            # Count new facts since last reflection
            cur.execute(
                """SELECT MAX(refreshed_at) as last_reflection
                   FROM knowledge k
                   JOIN entities e ON e.id = k.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND k.scope IN ('entity', 'cross', 'temporal')""",
                (user_id, sub_user_id)
            )
            row = cur.fetchone()
            last_reflection = row["last_reflection"] if row and row["last_reflection"] else None

            if last_reflection:
                cur.execute(
                    """SELECT COUNT(*) as cnt FROM facts f
                       JOIN entities e ON e.id = f.entity_id
                       WHERE e.user_id = %s AND e.sub_user_id = %s AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())
                       AND f.created_at > %s""",
                    (user_id, sub_user_id, last_reflection)
                )
                new_facts = cur.fetchone()["cnt"]
                hours_since = (datetime.datetime.now(datetime.timezone.utc) -
                              last_reflection.replace(tzinfo=datetime.timezone.utc)).total_seconds() / 3600
            else:
                # Never reflected — count all facts
                cur.execute(
                    """SELECT COUNT(*) as cnt FROM facts f
                       JOIN entities e ON e.id = f.entity_id
                       WHERE e.user_id = %s AND e.sub_user_id = %s AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())""",
                    (user_id, sub_user_id)
                )
                new_facts = cur.fetchone()["cnt"]
                hours_since = 999

            return {
                "new_facts_since_last": new_facts,
                "hours_since_last": round(hours_since, 1),
                "last_reflection": last_reflection.isoformat() if last_reflection else None,
            }

    def should_reflect(self, user_id: str, sub_user_id: str = "default") -> bool:
        """Check if reflection is needed based on triggers."""
        stats = self.get_reflection_stats(user_id, sub_user_id=sub_user_id)
        # Trigger 1: 10+ new facts since last reflection
        if stats["new_facts_since_last"] >= 10:
            return True
        # Trigger 2: 24h+ since last reflection AND has new facts
        if stats["hours_since_last"] >= 24 and stats["new_facts_since_last"] >= 3:
            return True
        return False

    def generate_reflections(self, user_id: str, llm_client, sub_user_id: str = "default") -> dict:
        """Generate all 3 types of reflections using LLM."""
        # Gather facts grouped by entity
        entities = self.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        if not entities:
            return {"entity_reflections": [], "cross_entity": [], "temporal": []}

        # Build facts text
        facts_lines = []
        for e in entities:
            if not e["facts"]:
                continue
            facts_str = ", ".join(e["facts"][:15])  # cap at 15 per entity
            facts_lines.append(f"- {e['entity']} ({e['type']}): {facts_str}")
        facts_text = "\n".join(facts_lines)

        # Get previous reflections
        prev = self.get_reflections(user_id, sub_user_id=sub_user_id)
        prev_text = ""
        if prev:
            prev_lines = []
            for r in prev[:10]:
                prev_lines.append(f"- [{r['scope']}] {r['title']}: {r['content'][:200]}")
            prev_text = "\n".join(prev_lines)
        if not prev_text:
            prev_text = "(none yet)"

        prompt = self.REFLECTION_PROMPT.format(
            facts_text=facts_text,
            prev_reflections=prev_text
        )

        try:
            result = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict):
                    break
                logger.warning(f"⚠️ Reflection JSON invalid (attempt {attempt + 1}/2), retrying...")
            if not isinstance(result, dict):
                logger.error("⚠️ Reflection generation failed after 2 attempts")
                return {"entity_reflections": [], "cross_entity": [], "temporal": []}
        except Exception as e:
            logger.error(f"⚠️ Reflection generation failed: {e}")
            return {"entity_reflections": [], "cross_entity": [], "temporal": []}

        # Save reflections
        saved = {"entity_reflections": 0, "cross_entity": 0, "temporal": 0}

        for r in result.get("entity_reflections", []):
            entity_name = r.get("entity", "")
            entity_id = self.get_entity_id(user_id, entity_name, sub_user_id=sub_user_id) if entity_name else None
            self._save_reflection(
                user_id=user_id,
                entity_id=entity_id,
                scope="entity",
                title=r.get("title", f"{entity_name} profile"),
                content=r.get("reflection", ""),
                confidence=r.get("confidence", 0.8),
                based_on=r.get("key_facts", []),
                sub_user_id=sub_user_id
            )
            saved["entity_reflections"] += 1

        for r in result.get("cross_entity", []):
            self._save_reflection(
                user_id=user_id,
                entity_id=None,
                scope="cross",
                title=r.get("title", "Cross-entity pattern"),
                content=r.get("reflection", ""),
                confidence=r.get("confidence", 0.8),
                based_on=[],
                sub_user_id=sub_user_id
            )
            saved["cross_entity"] += 1

        for r in result.get("temporal", []):
            self._save_reflection(
                user_id=user_id,
                entity_id=None,
                scope="temporal",
                title=r.get("title", "Recent changes"),
                content=r.get("reflection", ""),
                confidence=r.get("confidence", 0.8),
                based_on=[],
                sub_user_id=sub_user_id
            )
            saved["temporal"] += 1

        logger.info(f"🧠 Reflections generated for {user_id}: {saved}")
        return result

    def _save_reflection(self, user_id: str, entity_id: Optional[str],
                         scope: str, title: str, content: str,
                         confidence: float = 0.8, based_on: list = None,
                         sub_user_id: str = "default"):
        """Save or update a reflection with semantic dedup (word overlap)."""
        target_id = entity_id
        if not target_id:
            target_id = self._get_or_create_global_entity(user_id, sub_user_id=sub_user_id)

        # Semantic dedup: check existing reflections for >60% word overlap
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, title, content FROM knowledge
                   WHERE entity_id = %s AND type = 'reflection' AND scope = %s""",
                (target_id, scope)
            )
            existing = cur.fetchall()

            new_words = set(content.lower().split())
            for ex in existing:
                ex_words = set(ex["content"].lower().split())
                if not new_words or not ex_words:
                    continue
                overlap = len(new_words & ex_words) / max(len(new_words), len(ex_words))
                if overlap > 0.8:
                    # Update existing reflection instead of creating a duplicate
                    # Keep original title to avoid unique constraint violation
                    cur.execute(
                        """UPDATE knowledge SET content = %s, confidence = %s,
                           based_on_facts = %s, refreshed_at = NOW()
                           WHERE id = %s""",
                        (content, confidence, based_on or [], ex["id"])
                    )
                    return

        # No similar existing — upsert by entity + title
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO knowledge (entity_id, user_id, sub_user_id, type, title, content, scope, confidence, based_on_facts, refreshed_at)
                   VALUES (%s, %s, %s, 'reflection', %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (entity_id, title)
                   DO UPDATE SET content = EXCLUDED.content,
                                 confidence = EXCLUDED.confidence,
                                 based_on_facts = EXCLUDED.based_on_facts,
                                 refreshed_at = NOW()""",
                (target_id, user_id, sub_user_id, title, content, scope, confidence, based_on or [])
            )

    def _get_or_create_global_entity(self, user_id: str, sub_user_id: str = "default") -> str:
        """Get or create a special _reflections entity for cross/temporal reflections."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO entities (user_id, sub_user_id, name, type)
                   VALUES (%s, %s, '_reflections', 'concept')
                   ON CONFLICT ON CONSTRAINT uq_entities_user_sub_name DO UPDATE SET updated_at = NOW()
                   RETURNING id""",
                (user_id, sub_user_id)
            )
            return str(cur.fetchone()[0])

    def get_reflections(self, user_id: str, scope: str = None, sub_user_id: str = "default") -> list[dict]:
        """Get all reflections for a user. Cached 120s."""
        cache_key = f"reflections:{user_id}:{sub_user_id}:{scope or 'all'}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._get_reflections_uncached(user_id, scope, sub_user_id=sub_user_id)
        self.cache.set(cache_key, result, ttl=120)
        return result

    def _get_reflections_uncached(self, user_id: str, scope: str = None, sub_user_id: str = "default") -> list[dict]:
        """Get all reflections for a user (uncached)."""
        with self._cursor(dict_cursor=True) as cur:
            if scope:
                cur.execute(
                    """SELECT k.title, k.content, k.scope, k.confidence, k.refreshed_at,
                              e.name as entity_name
                       FROM knowledge k
                       JOIN entities e ON e.id = k.entity_id
                       WHERE k.user_id = %s AND e.sub_user_id = %s AND k.scope = %s AND k.type = 'reflection'
                       ORDER BY k.confidence DESC, k.refreshed_at DESC""",
                    (user_id, sub_user_id, scope)
                )
            else:
                cur.execute(
                    """SELECT k.title, k.content, k.scope, k.confidence, k.refreshed_at,
                              e.name as entity_name
                       FROM knowledge k
                       JOIN entities e ON e.id = k.entity_id
                       WHERE k.user_id = %s AND e.sub_user_id = %s AND k.type = 'reflection'
                       ORDER BY k.scope, k.confidence DESC, k.refreshed_at DESC""",
                    (user_id, sub_user_id)
                )
            return [{
                "title": r["title"],
                "content": r["content"],
                "scope": r["scope"],
                "confidence": float(r["confidence"] or 0.8),
                "entity": r["entity_name"],
                "refreshed_at": r["refreshed_at"].isoformat() if r["refreshed_at"] else None,
            } for r in cur.fetchall()]

    def get_insights(self, user_id: str, sub_user_id: str = "default") -> dict:
        """Get formatted insights for dashboard — profile, weekly, network, patterns."""
        reflections = self.get_reflections(user_id, sub_user_id=sub_user_id)
        if not reflections:
            return {"has_insights": False, "profile": None, "weekly": None, "network": None, "patterns": None}

        profile = next((r for r in reflections if r["scope"] == "entity" and "profile" in r["title"].lower()), None)
        # Fallback: first entity reflection for primary person
        if not profile:
            primary = self._find_primary_person(user_id, sub_user_id=sub_user_id)
            if primary:
                profile = next((r for r in reflections if r["scope"] == "entity" and r["entity"] == primary[1]), None)

        weekly = next((r for r in reflections if r["scope"] == "temporal"), None)
        network = next((r for r in reflections if r["scope"] == "cross" and 
                        any(w in r["title"].lower() for w in ["network", "colleague", "team"])), None)
        patterns = next((r for r in reflections if r["scope"] == "cross" and r != network), None)

        return {
            "has_insights": True,
            "profile": profile,
            "weekly": weekly,
            "network": network,
            "patterns": patterns,
            "all_reflections": reflections,
        }

    # ---- Embeddings ----

    def save_embedding(self, entity_id: str, chunk_text: str,
                       embedding: list[float]):
        """Store vector embedding for an entity chunk."""
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO embeddings (entity_id, chunk_text, embedding, tsv)
                   VALUES (%s, %s, %s::vector, to_tsvector('english', %s))""",
                (entity_id, chunk_text, embedding_str, chunk_text)
            )

    def delete_embeddings(self, entity_id: str):
        """Remove all embeddings for entity (before reindex)."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM embeddings WHERE entity_id = %s",
                (entity_id,)
            )

    # ---- Stats ----

    def get_stats(self, user_id: str, sub_user_id: str = "default") -> dict:
        """User's vault statistics. Cached for 30s."""
        cache_key = f"stats:{user_id}:{sub_user_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        result = self._get_stats_uncached(user_id, sub_user_id=sub_user_id)
        self.cache.set(cache_key, result, ttl=30)
        return result

    # ---- Cognitive Profile ----

    def get_profile(self, user_id: str, force: bool = False, sub_user_id: str = "default") -> dict:
        """Generate a cognitive profile — a ready-to-use system prompt from all user memory.
        Cached for 1 hour unless force=True."""
        cache_key = f"profile:{user_id}:{sub_user_id}"
        if not force:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        # 1. Gather all facts
        entities = self.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        if not entities:
            return {
                "user_id": user_id,
                "system_prompt": "",
                "facts_used": 0,
                "last_updated": None,
                "status": "no_data"
            }

        # 2. Build fact summary for LLM
        sections = []
        total_facts = 0
        for ent in entities:
            if not ent.get("facts"):
                continue
            facts_str = "\n".join(f"  - {f}" for f in ent["facts"][:20])
            rels_str = ""
            if ent.get("relations"):
                rels_str = "\n  Relations: " + ", ".join(
                    f"{r.get('type', '')} → {r.get('target', '')}"
                    for r in ent["relations"][:5]
                )
            sections.append(f"{ent['entity']} ({ent['type']}):\n{facts_str}{rels_str}")
            total_facts += len(ent["facts"][:20])

        if not sections:
            return {
                "user_id": user_id,
                "system_prompt": "",
                "facts_used": 0,
                "last_updated": None,
                "status": "no_facts"
            }

        memory_dump = "\n\n".join(sections[:50])  # Cap at 50 entities

        # 2b. Gather episodic memories (recent events)
        recent_episodes = self.get_episodes(user_id, limit=10, sub_user_id=sub_user_id)
        episodes_text = ""
        if recent_episodes:
            ep_lines = []
            for ep in recent_episodes[:10]:
                line = f"  - {ep['summary']}"
                if ep.get("outcome"):
                    line += f" → {ep['outcome']}"
                ep_lines.append(line)
            episodes_text = "\n\nRecent events:\n" + "\n".join(ep_lines)

        # 2c. Gather procedural memories (known workflows)
        procedures = self.get_procedures(user_id, limit=10, sub_user_id=sub_user_id)
        procedures_text = ""
        if procedures:
            pr_lines = []
            for pr in procedures[:10]:
                steps_count = len(pr.get("steps", []))
                success = pr.get("success_count", 0)
                line = f"  - {pr['name']} ({steps_count} steps, used {success}x)"
                if pr.get("trigger_condition"):
                    line += f" — trigger: {pr['trigger_condition']}"
                pr_lines.append(line)
            procedures_text = "\n\nKnown workflows:\n" + "\n".join(pr_lines)

        # 3. Generate system prompt via LLM
        import os
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            return {"user_id": user_id, "system_prompt": "", "facts_used": total_facts,
                    "status": "no_llm_key"}

        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)

            prompt = f"""You are a profile generator. Based on the memory below about a user, 
create a concise system prompt that any AI assistant can use to personalize responses.

The system prompt should include:
- Who the user is (name, age, location, occupation if known)
- What they're currently working on or interested in
- Communication preferences (language, tone, level of detail)
- Key relationships and context
- Recent events and current focus (from episodic memory)
- Known workflows and habits (from procedural memory)
- What to emphasize and what to avoid
- Any patterns in behavior or preferences

Write ONLY the system prompt text. No preamble, no explanation. 
Make it 150-250 words, natural and useful.
If user data is in a non-English language, write the profile in that language.

SEMANTIC MEMORY (facts about the user):
{memory_dump}
{episodes_text}
{procedures_text}"""

            resp = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=500,
                temperature=1,
            )

            system_prompt = resp.choices[0].message.content.strip()

            from datetime import datetime, timezone
            result = {
                "user_id": user_id,
                "system_prompt": system_prompt,
                "facts_used": total_facts,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "status": "ok"
            }

            # Cache for 1 hour
            self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            logger.error(f"Profile generation failed: {e}")
            return {"user_id": user_id, "system_prompt": "", "facts_used": total_facts,
                    "status": "error", "error": str(e)}

    def generate_rules_file(self, user_id: str, format: str = "claude_md",
                            sub_user_id: str = "default") -> dict:
        """Generate a CLAUDE.md / .cursorrules / .windsurfrules file from user memory.
        Focuses on technical context (tech stack, conventions, workflows), not personality.
        Cached for 1 hour."""
        cache_key = f"rules:{user_id}:{sub_user_id}:{format}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        entities = self.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        if not entities:
            return {"content": "", "status": "no_data", "format": format}

        # Categorize entities
        tech_lines, project_lines, knowledge_lines = [], [], []
        for ent in entities:
            facts_str = "; ".join(ent.get("facts", [])[:10])
            if ent.get("type") == "technology":
                tech_lines.append(f"- {ent['entity']}: {facts_str}")
            elif ent.get("type") == "project":
                project_lines.append(f"- {ent['entity']}: {facts_str}")

            for k in ent.get("knowledge", []):
                k_type = k.get("type", "")
                if k_type in ("solution", "command", "decision", "snippet", "pattern", "reference"):
                    knowledge_lines.append(
                        f"- [{k_type}] {k.get('title', '')}: {k.get('content', '')[:200]}"
                    )

        tech_lines = tech_lines[:20]
        project_lines = project_lines[:10]
        knowledge_lines = knowledge_lines[:30]

        # Procedures
        procedures = self.get_procedures(user_id, limit=15, sub_user_id=sub_user_id)
        proc_lines = []
        for pr in procedures:
            steps_text = " -> ".join((s.get("action", "") if isinstance(s, dict) else str(s)) for s in pr.get("steps", []))
            proc_lines.append(f"- {pr['name']}: {steps_text}")

        # Reflections
        reflections = self.get_reflections(user_id, sub_user_id=sub_user_id)
        reflection_lines = []
        for r in reflections[:10]:
            reflection_lines.append(f"- [{r.get('scope', '')}] {r.get('title', '')}: {r.get('content', '')[:150]}")

        total_facts = sum(len(e.get("facts", [])) for e in entities)

        format_instructions = {
            "claude_md": "Format as a CLAUDE.md file (markdown used by Claude Code for project context).",
            "cursorrules": "Format as a .cursorrules file (used by Cursor IDE for AI coding rules).",
            "windsurf": "Format as a .windsurfrules file (used by Windsurf IDE for AI rules).",
        }

        prompt = f"""You are a developer tools configuration generator.
Based on the user's memory data below, generate a structured rules/context file
that an AI coding assistant can use to understand the user's projects and conventions.

{format_instructions.get(format, format_instructions['claude_md'])}

Include these sections (use markdown headers):
1. **Project Overview** — main projects and what they do
2. **Tech Stack** — technologies, frameworks, languages with specific versions/configs if known
3. **Coding Conventions** — patterns, preferences, style rules extracted from facts
4. **Workflows** — step-by-step procedures the user follows
5. **Known Issues & Solutions** — problems encountered and how they were solved
6. **Key Decisions** — architectural and design decisions with rationale
7. **Important Context** — anything else an AI assistant should know

Rules:
- Be concise and actionable — each item should help an AI write better code
- Use bullet points, not paragraphs
- Include specific values (port numbers, model names, config values) when available
- Skip sections that have no relevant data
- Do NOT include personal information (age, location, relationships) — focus on technical context
- Output ONLY the file content, no preamble

TECHNOLOGY ENTITIES:
{chr(10).join(tech_lines) or '(none)'}

PROJECT ENTITIES:
{chr(10).join(project_lines) or '(none)'}

KNOWLEDGE ITEMS:
{chr(10).join(knowledge_lines) or '(none)'}

PROCEDURES/WORKFLOWS:
{chr(10).join(proc_lines) or '(none)'}

REFLECTIONS/PATTERNS:
{chr(10).join(reflection_lines) or '(none)'}"""

        import os
        try:
            import openai as _openai
            client = _openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
            resp = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=2000,
                temperature=1,
            )
            content = resp.choices[0].message.content.strip()

            result = {
                "content": content,
                "format": format,
                "facts_used": total_facts,
                "procedures_used": len(procedures),
                "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": "ok",
            }
            self.cache.set(cache_key, result, ttl=3600)
            return result

        except Exception as e:
            logger.error(f"Rules file generation failed: {e}")
            return {"content": "", "status": "error", "error": str(e), "format": format}

    def _get_stats_uncached(self, user_id: str, sub_user_id: str = "default") -> dict:
        """User's vault statistics (uncached)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("SELECT COUNT(*) FROM entities WHERE user_id = %s AND sub_user_id = %s", (user_id, sub_user_id))
            entities = cur.fetchone()[0]

            cur.execute(
                """SELECT e.type, COUNT(*) as cnt
                   FROM entities e WHERE e.user_id = %s AND e.sub_user_id = %s
                   GROUP BY e.type""",
                (user_id, sub_user_id)
            )
            by_type = {r["type"]: r["cnt"] for r in cur.fetchall()}

            cur.execute(
                """SELECT COUNT(*) FROM facts f
                   JOIN entities e ON e.id = f.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s""",
                (user_id, sub_user_id)
            )
            facts = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM knowledge k
                   JOIN entities e ON e.id = k.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s""",
                (user_id, sub_user_id)
            )
            knowledge = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM relations r
                   JOIN entities e ON e.id = r.source_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s""",
                (user_id, sub_user_id)
            )
            relations = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM embeddings emb
                   JOIN entities e ON e.id = emb.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s""",
                (user_id, sub_user_id)
            )
            embeddings = cur.fetchone()[0]

            return {
                "entities": entities,
                "by_type": by_type,
                "facts": facts,
                "knowledge": knowledge,
                "relations": relations,
                "embeddings": embeddings,
            }

    # ---- Usage tracking ----

    def log_usage(self, user_id: str, action: str, tokens: int = 0):
        """Log API usage."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO usage_log (user_id, action, tokens_used)
                   VALUES (%s, %s, %s)""",
                (user_id, action, tokens)
            )

    # ---- Subscriptions ----

    def get_subscription(self, user_id: str) -> dict:
        """Get user's subscription. Lazy-creates 'free' plan for existing users."""
        cache_key = f"sub:{user_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT * FROM subscriptions WHERE user_id = %s",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                result = dict(row)
                self.cache.set(cache_key, result, ttl=300)  # cache 5 min
                return result

            # Lazy-create free subscription for existing users
            cur.execute(
                """INSERT INTO subscriptions (user_id, plan, status)
                   VALUES (%s, 'free', 'active')
                   ON CONFLICT (user_id) DO NOTHING
                   RETURNING *""",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                # Race condition: another worker created it
                cur.execute(
                    "SELECT * FROM subscriptions WHERE user_id = %s",
                    (user_id,)
                )
                row = cur.fetchone()
            result = dict(row) if row else {"plan": "free", "status": "active"}
            self.cache.set(cache_key, result, ttl=300)
            return result

    def update_subscription(self, user_id: str, **kwargs) -> None:
        """Update subscription fields. Invalidates cache."""
        if not kwargs:
            return
        allowed = {"plan", "paddle_customer_id", "paddle_subscription_id",
                   "status", "current_period_start", "current_period_end",
                   "canceled_at"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return

        set_parts = [f"{k} = %s" for k in fields]
        set_parts.append("updated_at = NOW()")
        values = list(fields.values()) + [user_id]

        with self._cursor() as cur:
            # Ensure subscription row exists (lazy-create if needed)
            cur.execute(
                """INSERT INTO subscriptions (user_id, plan, status)
                   VALUES (%s, 'free', 'active')
                   ON CONFLICT (user_id) DO NOTHING""",
                (user_id,)
            )
            cur.execute(
                f"UPDATE subscriptions SET {', '.join(set_parts)} WHERE user_id = %s",
                values
            )
        self.cache.invalidate(f"sub:{user_id}")

    def get_user_by_paddle_customer(self, paddle_customer_id: str) -> Optional[str]:
        """Get user_id by Paddle customer ID (for webhook handling)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT user_id FROM subscriptions WHERE paddle_customer_id = %s",
                (paddle_customer_id,)
            )
            row = cur.fetchone()
            return str(row[0]) if row else None

    # ---- Usage Counters ----

    def increment_usage(self, user_id: str, action: str, count: int = 1) -> int:
        """Atomically increment usage counter for current billing period.
        Returns new count after increment."""
        column = f"{action}_count"
        # Validate column name to prevent SQL injection
        valid_columns = {"add_count", "search_count", "agent_count",
                        "reflect_count", "dedup_count", "reindex_count",
                        "rules_count"}
        if column not in valid_columns:
            raise ValueError(f"Invalid action: {action}")

        period_start = datetime.date.today().replace(day=1)
        with self._cursor() as cur:
            cur.execute(
                f"""INSERT INTO usage_counters (user_id, period_start, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, period_start)
                    DO UPDATE SET {column} = usage_counters.{column} + %s
                    RETURNING {column}""",
                (user_id, period_start, count, count)
            )
            result = cur.fetchone()[0]
        # Invalidate cached count
        self.cache.invalidate(f"usage:{user_id}:")
        return result

    def check_and_increment(self, user_id: str, action: str, max_allowed: int, count: int = 1) -> int:
        """Atomic check-and-increment: only increments if within quota.
        Returns new count after increment.
        Raises ValueError if quota would be exceeded."""
        if max_allowed == -1:
            return self.increment_usage(user_id, action, count)

        column = f"{action}_count"
        valid_columns = {"add_count", "search_count", "agent_count",
                        "reflect_count", "dedup_count", "reindex_count",
                        "rules_count"}
        if column not in valid_columns:
            raise ValueError(f"Invalid action: {action}")

        period_start = datetime.date.today().replace(day=1)
        with self._cursor() as cur:
            # Atomic: increment only if current value < max_allowed
            cur.execute(
                f"""INSERT INTO usage_counters (user_id, period_start, {column})
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, period_start)
                    DO UPDATE SET {column} = usage_counters.{column} + %s
                    WHERE usage_counters.{column} + %s <= %s
                    RETURNING {column}""",
                (user_id, period_start, count, count, count, max_allowed)
            )
            row = cur.fetchone()
            if row is None:
                # Quota exceeded — read current value for error message
                cur.execute(
                    f"SELECT {column} FROM usage_counters WHERE user_id = %s AND period_start = %s",
                    (user_id, period_start)
                )
                r = cur.fetchone()
                current = r[0] if r else 0
                raise ValueError(f"quota_exceeded:{action}:{current}:{max_allowed}")
            result = row[0]
        self.cache.invalidate(f"usage:{user_id}:")
        return result

    def count_distinct_sub_users(self, user_id: str) -> int:
        """Count distinct sub_user_ids used by this user (across entities table).
        Cached 60s to avoid repeated COUNT DISTINCT queries."""
        cache_key = f"sub_users:{user_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        with self._cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT sub_user_id) FROM entities
                WHERE user_id = %s AND sub_user_id != 'default'
            """, (user_id,))
            row = cur.fetchone()
            count = row[0] if row else 0
        self.cache.set(cache_key, count, ttl=60)
        return count

    def is_known_sub_user(self, user_id: str, sub_user_id: str) -> bool:
        """Check if a sub_user_id already exists for this user."""
        with self._cursor() as cur:
            cur.execute("""
                SELECT 1 FROM entities
                WHERE user_id = %s AND sub_user_id = %s
                LIMIT 1
            """, (user_id, sub_user_id))
            return cur.fetchone() is not None

    def get_usage_count(self, user_id: str, action: str) -> int:
        """Get current month's usage count for an action. Cached 10s."""
        column = f"{action}_count"
        valid_columns = {"add_count", "search_count", "agent_count",
                        "reflect_count", "dedup_count", "reindex_count",
                        "rules_count"}
        if column not in valid_columns:
            return 0

        cache_key = f"usage:{user_id}:{action}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        period_start = datetime.date.today().replace(day=1)
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {column} FROM usage_counters WHERE user_id = %s AND period_start = %s",
                (user_id, period_start)
            )
            row = cur.fetchone()
            val = row[0] if row else 0
        self.cache.set(cache_key, val, ttl=10)
        return val

    def get_all_usage_counts(self, user_id: str) -> dict:
        """Get all usage counters for current billing period."""
        period_start = datetime.date.today().replace(day=1)
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT add_count, search_count, agent_count,
                          reflect_count, dedup_count, reindex_count,
                          rules_count
                   FROM usage_counters
                   WHERE user_id = %s AND period_start = %s""",
                (user_id, period_start)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return {
                "add_count": 0, "search_count": 0, "agent_count": 0,
                "reflect_count": 0, "dedup_count": 0, "reindex_count": 0,
                "rules_count": 0,
            }

    # ---- Graph ----

    def get_graph(self, user_id: str, sub_user_id: str = "default", limit: int = 150) -> dict:
        """Get knowledge graph (nodes + edges) for visualization. Cached 30s."""
        cache_key = f"graph:{user_id}:{sub_user_id}:{limit}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            # Total node count (use base table, not the expensive VIEW)
            cur.execute(
                "SELECT COUNT(*) FROM entities WHERE user_id = %s AND sub_user_id = %s",
                (user_id, sub_user_id)
            )
            total_nodes = cur.fetchone()[0]

            # Top nodes by facts_count (most connected first)
            cur.execute(
                """SELECT name, type, facts_count, knowledge_count
                   FROM entity_overview WHERE user_id = %s AND sub_user_id = %s
                   ORDER BY facts_count DESC LIMIT %s""",
                (user_id, sub_user_id, limit)
            )
            nodes = [dict(r) for r in cur.fetchall()]
            node_names = {n["name"] for n in nodes}

            # Edges only between returned nodes
            cur.execute(
                """SELECT es.name as source, et.name as target, r.type, r.description
                   FROM relations r
                   JOIN entities es ON es.id = r.source_id
                   JOIN entities et ON et.id = r.target_id
                   WHERE es.user_id = %s AND es.sub_user_id = %s""",
                (user_id, sub_user_id)
            )
            edges = [dict(r) for r in cur.fetchall() if r["source"] in node_names and r["target"] in node_names]

            result = {"nodes": nodes, "edges": edges, "total_nodes": total_nodes}
            self.cache.set(cache_key, result, ttl=30)
            return result

    def get_feed(self, user_id: str, limit: int = 50, offset: int = 0, sub_user_id: str = "default") -> dict:
        """Get recent facts with entity info for Memory Feed. Cached 15s."""
        cache_key = f"feed:{user_id}:{sub_user_id}:{offset}:{limit}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT COUNT(*) FROM facts f
                   JOIN entities e ON e.id = f.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())""",
                (user_id, sub_user_id)
            )
            total = cur.fetchone()[0]

            cur.execute(
                """SELECT f.id, f.content, f.created_at, f.archived,
                          f.importance, f.access_count,
                          e.name as entity_name, e.type as entity_type
                   FROM facts f
                   JOIN entities e ON e.id = f.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())
                   ORDER BY f.created_at DESC
                   LIMIT %s OFFSET %s""",
                (user_id, sub_user_id, limit, offset)
            )
            items = []
            for row in cur.fetchall():
                items.append({
                    "id": str(row["id"]),
                    "fact": row["content"],
                    "entity": row["entity_name"],
                    "entity_type": row["entity_type"],
                    "importance": round(float(row["importance"] or 0.5), 2),
                    "access_count": row["access_count"] or 0,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })
            result = {"feed": items, "total": total}
            self.cache.set(cache_key, result, ttl=15)
            return result

    # =====================================================
    # EPISODIC MEMORY v2.5
    # =====================================================

    def save_episode(self, user_id: str, summary: str, context: str = None,
                     outcome: str = None, participants: list[str] = None,
                     emotional_valence: str = "neutral", importance: float = 0.5,
                     metadata: dict = None, expires_at: str = None,
                     linked_procedure_id: str = None,
                     failed_at_step: int = None,
                     sub_user_id: str = "default",
                     happened_at: str = None) -> str:
        """Save an episodic memory — a specific event or interaction."""
        meta_json = json.dumps(metadata) if metadata else '{}'
        parts = participants or []
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO episodes
                   (user_id, sub_user_id, summary, context, outcome, participants,
                    emotional_valence, importance, metadata, expires_at,
                    linked_procedure_id, failed_at_step, happened_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                   RETURNING id""",
                (user_id, sub_user_id, summary, context, outcome, parts,
                 emotional_valence, importance, meta_json,
                 expires_at, linked_procedure_id, failed_at_step, happened_at)
            )
            episode_id = str(cur.fetchone()[0])
        logger.info(f"📝 Episode saved: {summary[:60]}...")
        return episode_id

    def save_episode_embedding(self, episode_id: str, chunk_text: str, embedding: list[float]):
        """Save embedding for an episode."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO episode_embeddings (episode_id, chunk_text, embedding, tsv)
                   VALUES (%s, %s, %s::vector, to_tsvector('english', %s))""",
                (episode_id, chunk_text, embedding, chunk_text)
            )

    def delete_episode_embeddings(self, episode_id: str):
        """Delete all embeddings for an episode."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM episode_embeddings WHERE episode_id = %s", (episode_id,))

    # ---- Raw conversation chunks ----

    def save_conversation_chunk(self, user_id: str, content: str, sub_user_id: str = "default") -> str:
        """Save a raw conversation chunk for fallback retrieval."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO conversation_chunks (user_id, sub_user_id, content)
                   VALUES (%s, %s, %s) RETURNING id""",
                (user_id, sub_user_id, content)
            )
            return str(cur.fetchone()[0])

    def save_chunk_embedding(self, chunk_id: str, chunk_text: str, embedding: list[float]):
        """Save embedding for a conversation chunk."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO chunk_embeddings (chunk_id, embedding, tsv)
                   VALUES (%s, %s::vector, to_tsvector('english', %s))""",
                (chunk_id, f"[{','.join(str(x) for x in embedding)}]", chunk_text)
            )

    def search_chunks_vector(self, user_id: str, embedding: list[float],
                             query_text: str = "", top_k: int = 5,
                             min_score: float = 0.15,
                             sub_user_id: str = "default") -> list[dict]:
        """Search raw conversation chunks via hybrid vector+BM25."""
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        with self._cursor(dict_cursor=True) as cur:
            # Vector search
            cur.execute(
                """SELECT c.id, c.content, c.created_at,
                          1 - (ce.embedding <=> %s::vector) AS score
                   FROM chunk_embeddings ce
                   JOIN conversation_chunks c ON c.id = ce.chunk_id
                   WHERE c.user_id = %s AND c.sub_user_id = %s
                     AND 1 - (ce.embedding <=> %s::vector) > %s
                   ORDER BY score DESC
                   LIMIT %s""",
                (embedding_str, user_id, sub_user_id, embedding_str, min_score, top_k * 2)
            )
            vector_rows = cur.fetchall()

            # BM25 text search
            bm25_rows = []
            if query_text:
                cur.execute(
                    """SELECT c.id, c.content, c.created_at,
                              ts_rank_cd(ce.tsv, plainto_tsquery('english', %s), 32) AS rank
                       FROM chunk_embeddings ce
                       JOIN conversation_chunks c ON c.id = ce.chunk_id
                       WHERE c.user_id = %s AND c.sub_user_id = %s
                         AND ce.tsv @@ plainto_tsquery('english', %s)
                       ORDER BY rank DESC
                       LIMIT %s""",
                    (query_text, user_id, sub_user_id, query_text, top_k * 2)
                )
                bm25_rows = cur.fetchall()

            # Simple RRF merge
            k = 60
            scores = {}
            for i, row in enumerate(vector_rows):
                cid = str(row["id"])
                scores[cid] = scores.get(cid, 0) + 1.0 / (k + i + 1)
                scores[cid + "_data"] = row
            for i, row in enumerate(bm25_rows):
                cid = str(row["id"])
                scores[cid] = scores.get(cid, 0) + 1.0 / (k + i + 1)
                if cid + "_data" not in scores:
                    scores[cid + "_data"] = row

            # Sort by RRF score and return top_k
            ranked = sorted(
                [(cid, sc) for cid, sc in scores.items() if not cid.endswith("_data")],
                key=lambda x: x[1], reverse=True
            )[:top_k]

            results = []
            for cid, sc in ranked:
                data = scores.get(cid + "_data", {})
                results.append({
                    "id": cid,
                    "content": data.get("content", ""),
                    "score": round(sc, 4),
                    "created_at": data["created_at"].isoformat() if data.get("created_at") else None,
                })
            return results

    def get_episodes(self, user_id: str, limit: int = 20, after: str = None,
                     before: str = None, sub_user_id: str = "default") -> list[dict]:
        """Get episodes by time range."""
        query = """SELECT id, summary, context, outcome, participants,
                          emotional_valence, importance, metadata,
                          linked_procedure_id, failed_at_step, created_at,
                          happened_at
                   FROM episodes
                   WHERE user_id = %s AND sub_user_id = %s
                     AND (expires_at IS NULL OR expires_at > NOW())"""
        params = [user_id, sub_user_id]
        if after:
            query += " AND created_at >= %s"
            params.append(after)
        if before:
            query += " AND created_at <= %s"
            params.append(before)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(query, params)
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "summary": row["summary"],
                    "context": row["context"],
                    "outcome": row["outcome"],
                    "participants": row["participants"] or [],
                    "emotional_valence": row["emotional_valence"],
                    "importance": round(float(row["importance"] or 0.5), 2),
                    "metadata": row["metadata"] or {},
                    "linked_procedure_id": str(row["linked_procedure_id"]) if row["linked_procedure_id"] else None,
                    "failed_at_step": row["failed_at_step"],
                    "happened_at": row.get("happened_at"),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })
            return results

    def search_episodes_vector(self, user_id: str, embedding: list[float],
                               top_k: int = 5, after: str = None,
                               before: str = None, sub_user_id: str = "default",
                               query_text: str = "") -> list[dict]:
        """Hybrid search over episodic memory: vector + BM25 + RRF + temporal decay."""
        query = """
            SELECT ep.id, ep.summary, ep.context, ep.outcome, ep.participants,
                   ep.emotional_valence, ep.importance, ep.created_at,
                   ep.happened_at, ep.metadata,
                   1 - (ee.embedding <=> %s::vector) AS score
            FROM episode_embeddings ee
            JOIN episodes ep ON ep.id = ee.episode_id
            WHERE ep.user_id = %s AND ep.sub_user_id = %s
              AND (ep.expires_at IS NULL OR ep.expires_at > NOW())
              AND 1 - (ee.embedding <=> %s::vector) > 0.25
        """
        params = [embedding, user_id, sub_user_id, embedding]
        if after:
            query += " AND ep.created_at >= %s"
            params.append(after)
        if before:
            query += " AND ep.created_at <= %s"
            params.append(before)
        query += " ORDER BY ee.embedding <=> %s::vector LIMIT %s"
        params.extend([embedding, top_k * 4])

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(query, params)
            # Stage 1: Vector results — rank by position
            vec_rows = {}  # id -> (rank, row_dict)
            seen = set()
            for rank, row in enumerate(cur.fetchall()):
                eid = str(row["id"])
                if eid in seen:
                    continue
                seen.add(eid)
                vec_rows[eid] = (rank, row)

            # Stage 2: BM25 text results
            bm25_rows = {}  # id -> rank
            if query_text:
                cur.execute("""
                    SELECT DISTINCT ON (ep.id)
                           ep.id,
                           ts_rank_cd(ee.tsv, plainto_tsquery('english', %s), 32) AS rank
                    FROM episode_embeddings ee
                    JOIN episodes ep ON ep.id = ee.episode_id
                    WHERE ep.user_id = %s AND ep.sub_user_id = %s
                      AND (ep.expires_at IS NULL OR ep.expires_at > NOW())
                      AND ee.tsv @@ plainto_tsquery('english', %s)
                    ORDER BY ep.id, rank DESC
                """, (query_text, user_id, sub_user_id, query_text))
                bm25_list = cur.fetchall()
                bm25_list.sort(key=lambda r: float(r["rank"]), reverse=True)
                bm25_rows = {str(r["id"]): i for i, r in enumerate(bm25_list[:top_k * 4])}

                # Fetch full rows for BM25-only hits
                for eid in bm25_rows:
                    if eid not in vec_rows:
                        cur.execute("""
                            SELECT ep.id, ep.summary, ep.context, ep.outcome, ep.participants,
                                   ep.emotional_valence, ep.importance, ep.created_at, ep.happened_at, ep.metadata
                            FROM episodes ep WHERE ep.id = %s
                        """, (eid,))
                        r = cur.fetchone()
                        if r:
                            r = dict(r)
                            r["score"] = 0
                            vec_rows[eid] = (len(vec_rows), r)

            # Stage 3: RRF fusion (k=60)
            rrf_k = 60
            rrf_scores = {}
            for eid, (rank, _) in vec_rows.items():
                rrf_scores[eid] = 1.0 / (rrf_k + rank)
            for eid, rank in bm25_rows.items():
                rrf_scores[eid] = rrf_scores.get(eid, 0) + 1.0 / (rrf_k + rank)

            # Stage 4: Temporal decay + build results
            now = datetime.datetime.now(datetime.timezone.utc)
            results = []
            for eid in sorted(rrf_scores, key=rrf_scores.get, reverse=True):
                _, row = vec_rows[eid]
                ref_time = row.get("happened_at") or row.get("created_at")
                if ref_time:
                    try:
                        age_days = (now - ref_time.replace(tzinfo=datetime.timezone.utc)).days
                        decay = 0.7 + 0.3 * math.exp(-0.02 * age_days)
                    except Exception:
                        decay = 0.7
                else:
                    decay = 0.7
                final_score = round(rrf_scores[eid] * decay, 4)
                results.append({
                    "id": eid,
                    "summary": row["summary"],
                    "context": row["context"],
                    "outcome": row["outcome"],
                    "participants": row.get("participants") or [],
                    "emotional_valence": row.get("emotional_valence"),
                    "importance": round(float(row.get("importance") or 0.5), 2),
                    "score": final_score,
                    "happened_at": row.get("happened_at"),
                    "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "episodic",
                })
            results.sort(key=lambda r: r["score"], reverse=True)
            results = results[:top_k]
            # Normalize scores to 0-1 range (RRF scores are tiny, clients expect 0-1)
            # Only normalize when 2+ results; single result keeps raw score to avoid false 1.0
            if len(results) >= 2:
                max_s = max(r["score"] for r in results)
                if max_s > 0:
                    for r in results:
                        r["score"] = round(r["score"] / max_s, 4)
            return results

    def search_episodes_text(self, user_id: str, query: str,
                             top_k: int = 5, sub_user_id: str = "default") -> list[dict]:
        """BM25 text search over episodic memory."""
        sql = """
            SELECT ep.id, ep.summary, ep.context, ep.outcome, ep.participants,
                   ep.emotional_valence, ep.importance, ep.created_at,
                   ep.happened_at, ep.metadata,
                   ts_rank_cd(ee.tsv, plainto_tsquery('english', %s), 32) AS score
            FROM episode_embeddings ee
            JOIN episodes ep ON ep.id = ee.episode_id
            WHERE ep.user_id = %s AND ep.sub_user_id = %s
              AND (ep.expires_at IS NULL OR ep.expires_at > NOW())
              AND ee.tsv @@ plainto_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(sql, (query, user_id, sub_user_id, query, top_k))
            results = []
            seen = set()
            for row in cur.fetchall():
                eid = str(row["id"])
                if eid in seen:
                    continue
                seen.add(eid)
                results.append({
                    "id": eid,
                    "summary": row["summary"],
                    "context": row["context"],
                    "outcome": row["outcome"],
                    "participants": row["participants"] or [],
                    "emotional_valence": row["emotional_valence"],
                    "importance": round(float(row["importance"] or 0.5), 2),
                    "score": round(float(row["score"]), 4),
                    "happened_at": row.get("happened_at"),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "episodic",
                })
            return results

    # =====================================================
    # PROCEDURAL MEMORY v2.5
    # =====================================================

    def save_procedure(self, user_id: str, name: str, trigger_condition: str = None,
                       steps: list[dict] = None, entity_names: list[str] = None,
                       source_episode_ids: list[str] = None,
                       metadata: dict = None, expires_at: str = None,
                       version: int = 1, parent_version_id: str = None,
                       evolved_from_episode: str = None,
                       is_current: bool = True,
                       sub_user_id: str = "default") -> str:
        """Save or update a procedural memory — learned workflow/skill."""
        meta_json = json.dumps(metadata) if metadata else '{}'
        steps_json = json.dumps(steps or [])
        entities = entity_names or []
        ep_ids = source_episode_ids or []

        # Case-insensitive lookup: use existing name to trigger ON CONFLICT correctly
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT name FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)
                   AND version = %s LIMIT 1""",
                (user_id, sub_user_id, name, version)
            )
            existing = cur.fetchone()
            if existing:
                name = existing["name"]  # Use canonical casing

        try:
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO procedures
                       (user_id, sub_user_id, name, trigger_condition, steps, entity_names,
                        source_episode_ids, metadata, expires_at,
                        version, parent_version_id, evolved_from_episode, is_current)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::uuid[], %s::jsonb, %s,
                               %s, %s, %s, %s)
                       ON CONFLICT ON CONSTRAINT uq_procedures_user_sub_name_ver
                       DO UPDATE SET
                           trigger_condition = COALESCE(EXCLUDED.trigger_condition, procedures.trigger_condition),
                           steps = EXCLUDED.steps,
                           entity_names = EXCLUDED.entity_names,
                           updated_at = NOW()
                       RETURNING id""",
                    (user_id, sub_user_id, name, trigger_condition, steps_json, entities,
                     ep_ids if ep_ids else None, meta_json, expires_at,
                     version, parent_version_id, evolved_from_episode, is_current)
                )
                proc_id = str(cur.fetchone()[0])
        except (psycopg2.IntegrityError, psycopg2.errors.UniqueViolation):
            # Race condition: CI index caught a case-different duplicate
            with self._cursor(dict_cursor=True) as cur:
                cur.execute(
                    """SELECT id FROM procedures
                       WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s) AND version = %s""",
                    (user_id, sub_user_id, name, version)
                )
                row = cur.fetchone()
                if row:
                    proc_id = str(row["id"])
                else:
                    raise
        logger.info(f"⚙️ Procedure saved: {name} v{version}")
        return proc_id

    def save_procedure_embedding(self, procedure_id: str, chunk_text: str, embedding: list[float]):
        """Save embedding for a procedure."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO procedure_embeddings (procedure_id, chunk_text, embedding, tsv)
                   VALUES (%s, %s, %s::vector, to_tsvector('english', %s))""",
                (procedure_id, chunk_text, embedding, chunk_text)
            )

    def delete_procedure_embeddings(self, procedure_id: str):
        """Delete all embeddings for a procedure."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM procedure_embeddings WHERE procedure_id = %s", (procedure_id,))

    def get_procedures(self, user_id: str, limit: int = 20,
                       sub_user_id: str = "default") -> list[dict]:
        """Get all current procedures for a user (latest versions only)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, trigger_condition, steps, entity_names,
                          success_count, fail_count, last_used, version,
                          created_at, updated_at, metadata
                   FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s
                     AND is_current = TRUE
                     AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY updated_at DESC
                   LIMIT %s""",
                (user_id, sub_user_id, limit)
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "name": row["name"],
                    "trigger_condition": row["trigger_condition"],
                    "steps": row["steps"] or [],
                    "entity_names": row["entity_names"] or [],
                    "success_count": row["success_count"] or 0,
                    "fail_count": row["fail_count"] or 0,
                    "version": row["version"] or 1,
                    "last_used": row["last_used"].isoformat() if row["last_used"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "procedural",
                })
            return results

    def search_procedures_vector(self, user_id: str, embedding: list[float],
                                 top_k: int = 5, sub_user_id: str = "default",
                                 query_text: str = "") -> list[dict]:
        """Hybrid search over procedural memory: vector + BM25 + RRF (current versions only)."""
        query = """
            SELECT p.id, p.name, p.trigger_condition, p.steps, p.entity_names,
                   p.success_count, p.fail_count, p.last_used, p.version, p.updated_at, p.metadata,
                   1 - (pe.embedding <=> %s::vector) AS score
            FROM procedure_embeddings pe
            JOIN procedures p ON p.id = pe.procedure_id
            WHERE p.user_id = %s AND p.sub_user_id = %s
              AND p.is_current = TRUE
              AND (p.expires_at IS NULL OR p.expires_at > NOW())
              AND 1 - (pe.embedding <=> %s::vector) > 0.25
            ORDER BY pe.embedding <=> %s::vector
            LIMIT %s
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(query, (embedding, user_id, sub_user_id, embedding, embedding, top_k * 4))
            # Stage 1: Vector results
            vec_rows = {}
            seen = set()
            for rank, row in enumerate(cur.fetchall()):
                pid = str(row["id"])
                if pid in seen:
                    continue
                seen.add(pid)
                vec_rows[pid] = (rank, row)

            # Stage 2: BM25 text results
            bm25_rows = {}
            if query_text:
                cur.execute("""
                    SELECT DISTINCT ON (p.id)
                           p.id,
                           ts_rank_cd(pe.tsv, plainto_tsquery('english', %s), 32) AS rank
                    FROM procedure_embeddings pe
                    JOIN procedures p ON p.id = pe.procedure_id
                    WHERE p.user_id = %s AND p.sub_user_id = %s
                      AND p.is_current = TRUE
                      AND (p.expires_at IS NULL OR p.expires_at > NOW())
                      AND pe.tsv @@ plainto_tsquery('english', %s)
                    ORDER BY p.id, rank DESC
                """, (query_text, user_id, sub_user_id, query_text))
                bm25_list = cur.fetchall()
                bm25_list.sort(key=lambda r: float(r["rank"]), reverse=True)
                bm25_rows = {str(r["id"]): i for i, r in enumerate(bm25_list[:top_k * 4])}

                # Fetch full rows for BM25-only hits
                for pid in bm25_rows:
                    if pid not in vec_rows:
                        cur.execute("""
                            SELECT p.id, p.name, p.trigger_condition, p.steps, p.entity_names,
                                   p.success_count, p.fail_count, p.last_used, p.version, p.updated_at, p.metadata
                            FROM procedures p WHERE p.id = %s
                        """, (pid,))
                        r = cur.fetchone()
                        if r:
                            r = dict(r)
                            r["score"] = 0
                            vec_rows[pid] = (len(vec_rows), r)

            # Stage 3: RRF fusion (k=60)
            rrf_k = 60
            rrf_scores = {}
            for pid, (rank, _) in vec_rows.items():
                rrf_scores[pid] = 1.0 / (rrf_k + rank)
            for pid, rank in bm25_rows.items():
                rrf_scores[pid] = rrf_scores.get(pid, 0) + 1.0 / (rrf_k + rank)

            # Build results sorted by RRF score
            results = []
            for pid in sorted(rrf_scores, key=rrf_scores.get, reverse=True):
                _, row = vec_rows[pid]
                results.append({
                    "id": pid,
                    "name": row["name"],
                    "trigger_condition": row.get("trigger_condition"),
                    "steps": row.get("steps") or [],
                    "entity_names": row.get("entity_names") or [],
                    "success_count": row.get("success_count") or 0,
                    "fail_count": row.get("fail_count") or 0,
                    "version": row.get("version") or 1,
                    "score": round(rrf_scores[pid], 4),
                    "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "procedural",
                })
            results = results[:top_k]
            # Normalize scores to 0-1 range (RRF scores are tiny, clients expect 0-1)
            # Only normalize when 2+ results; single result keeps raw score to avoid false 1.0
            if len(results) >= 2:
                max_s = max(r["score"] for r in results)
                if max_s > 0:
                    for r in results:
                        r["score"] = round(r["score"] / max_s, 4)
            return results

    def search_procedures_text(self, user_id: str, query: str,
                               top_k: int = 5, sub_user_id: str = "default") -> list[dict]:
        """BM25 text search over procedural memory (current versions only)."""
        sql = """
            SELECT p.id, p.name, p.trigger_condition, p.steps, p.entity_names,
                   p.success_count, p.fail_count, p.version, p.updated_at, p.metadata,
                   ts_rank_cd(pe.tsv, plainto_tsquery('english', %s), 32) AS score
            FROM procedure_embeddings pe
            JOIN procedures p ON p.id = pe.procedure_id
            WHERE p.user_id = %s AND p.sub_user_id = %s
              AND p.is_current = TRUE
              AND (p.expires_at IS NULL OR p.expires_at > NOW())
              AND pe.tsv @@ plainto_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(sql, (query, user_id, sub_user_id, query, top_k))
            results = []
            seen = set()
            for row in cur.fetchall():
                pid = str(row["id"])
                if pid in seen:
                    continue
                seen.add(pid)
                results.append({
                    "id": pid,
                    "name": row["name"],
                    "trigger_condition": row["trigger_condition"],
                    "steps": row["steps"] or [],
                    "entity_names": row["entity_names"] or [],
                    "success_count": row["success_count"] or 0,
                    "fail_count": row["fail_count"] or 0,
                    "version": row["version"] or 1,
                    "score": round(float(row["score"]), 4),
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "procedural",
                })
            return results

    def procedure_feedback(self, user_id: str, procedure_id: str, success: bool, sub_user_id: str = "default") -> dict:
        """Record success/failure feedback for a procedure."""
        col = "success_count" if success else "fail_count"
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                f"""UPDATE procedures
                    SET {col} = {col} + 1, last_used = NOW(), updated_at = NOW()
                    WHERE id = %s AND user_id = %s AND sub_user_id = %s
                    RETURNING id, name, success_count, fail_count""",
                (procedure_id, user_id, sub_user_id)
            )
            row = cur.fetchone()
            if not row:
                return {"error": "procedure not found"}
            return {
                "id": str(row["id"]),
                "name": row["name"],
                "success_count": row["success_count"],
                "fail_count": row["fail_count"],
                "feedback": "success" if success else "failure",
            }

    # =====================================================
    # EXPERIENCE-DRIVEN PROCEDURES v2.7
    # =====================================================

    def get_procedure_by_id(self, user_id: str, procedure_id: str, sub_user_id: str = "default") -> dict | None:
        """Get a single procedure by ID."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, trigger_condition, steps, entity_names,
                          success_count, fail_count, version, parent_version_id,
                          evolved_from_episode, is_current, last_used,
                          created_at, updated_at
                   FROM procedures
                   WHERE id = %s AND user_id = %s AND sub_user_id = %s""",
                (procedure_id, user_id, sub_user_id)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": str(row["id"]),
                "name": row["name"],
                "trigger_condition": row["trigger_condition"],
                "steps": row["steps"] or [],
                "entity_names": row["entity_names"] or [],
                "success_count": row["success_count"] or 0,
                "fail_count": row["fail_count"] or 0,
                "version": row["version"] or 1,
                "parent_version_id": str(row["parent_version_id"]) if row["parent_version_id"] else None,
                "evolved_from_episode": str(row["evolved_from_episode"]) if row["evolved_from_episode"] else None,
                "is_current": row["is_current"],
                "last_used": row["last_used"].isoformat() if row["last_used"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }

    def evolve_procedure(self, user_id: str, procedure_id: str,
                         new_steps: list[dict], new_trigger: str = None,
                         episode_id: str = None, change_type: str = "step_modified",
                         diff: dict = None, sub_user_id: str = "default") -> str:
        """Create a new version of a procedure (experience-driven evolution).

        Marks the old version as not current, creates a new row with version+1,
        and logs the evolution in procedure_evolution table.
        Returns the new procedure ID.
        """
        old = self.get_procedure_by_id(user_id, procedure_id, sub_user_id=sub_user_id)
        if not old:
            raise ValueError(f"Procedure {procedure_id} not found")

        old_version = old["version"]
        new_version = old_version + 1

        # Mark old version as not current
        with self._cursor() as cur:
            cur.execute(
                "UPDATE procedures SET is_current = FALSE, updated_at = NOW() WHERE id = %s",
                (procedure_id,)
            )

        # Create new version
        new_proc_id = self.save_procedure(
            user_id=user_id,
            name=old["name"],
            trigger_condition=new_trigger or old["trigger_condition"],
            steps=new_steps,
            entity_names=old["entity_names"],
            version=new_version,
            parent_version_id=procedure_id,
            evolved_from_episode=episode_id,
            is_current=True,
            sub_user_id=sub_user_id,
        )

        # Log evolution
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO procedure_evolution
                   (procedure_id, episode_id, change_type, diff,
                    version_before, version_after)
                   VALUES (%s, %s, %s, %s::jsonb, %s, %s)""",
                (new_proc_id, episode_id, change_type,
                 json.dumps(diff or {}), old_version, new_version)
            )

        logger.info(f"🔄 Procedure evolved: {old['name']} v{old_version} → v{new_version}")
        return new_proc_id

    def get_procedure_history(self, user_id: str, procedure_id: str, sub_user_id: str = "default") -> list[dict]:
        """Get all versions of a procedure by tracing the version chain.

        Finds the procedure name, then returns all versions ordered by version number.
        """
        # First get the name from the given procedure
        proc = self.get_procedure_by_id(user_id, procedure_id, sub_user_id=sub_user_id)
        if not proc:
            return []

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, trigger_condition, steps, entity_names,
                          success_count, fail_count, version, parent_version_id,
                          evolved_from_episode, is_current, created_at, updated_at
                   FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s AND name = %s
                   ORDER BY version ASC""",
                (user_id, sub_user_id, proc["name"])
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "name": row["name"],
                    "trigger_condition": row["trigger_condition"],
                    "steps": row["steps"] or [],
                    "entity_names": row["entity_names"] or [],
                    "success_count": row["success_count"] or 0,
                    "fail_count": row["fail_count"] or 0,
                    "version": row["version"] or 1,
                    "parent_version_id": str(row["parent_version_id"]) if row["parent_version_id"] else None,
                    "evolved_from_episode": str(row["evolved_from_episode"]) if row["evolved_from_episode"] else None,
                    "is_current": row["is_current"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                })
            return results

    def get_procedure_evolution(self, user_id: str, procedure_id: str, sub_user_id: str = "default") -> list[dict]:
        """Get the evolution log for a procedure (all versions)."""
        # Get all version IDs for this procedure name
        proc = self.get_procedure_by_id(user_id, procedure_id, sub_user_id=sub_user_id)
        if not proc:
            return []

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT pe.id, pe.procedure_id, pe.episode_id, pe.change_type,
                          pe.diff, pe.version_before, pe.version_after, pe.created_at
                   FROM procedure_evolution pe
                   JOIN procedures p ON p.id = pe.procedure_id
                   WHERE p.user_id = %s AND p.sub_user_id = %s AND p.name = %s
                   ORDER BY pe.created_at ASC""",
                (user_id, sub_user_id, proc["name"])
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "procedure_id": str(row["procedure_id"]),
                    "episode_id": str(row["episode_id"]) if row["episode_id"] else None,
                    "change_type": row["change_type"],
                    "diff": row["diff"] or {},
                    "version_before": row["version_before"],
                    "version_after": row["version_after"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })
            return results

    def get_unlinked_actionable_episodes(self, user_id: str, limit: int = 50, sub_user_id: str = "default") -> list[dict]:
        """Get recent episodes not linked to any procedure, excluding failures.

        Returns positive, neutral, and mixed episodes for pattern detection.
        Includes neutral episodes which represent the majority of user activity.
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, summary, context, outcome, participants,
                          emotional_valence, importance, created_at
                   FROM episodes
                   WHERE user_id = %s AND sub_user_id = %s
                     AND linked_procedure_id IS NULL
                     AND emotional_valence IN ('positive', 'neutral', 'mixed')
                     AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (user_id, sub_user_id, limit)
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "summary": row["summary"],
                    "context": row["context"],
                    "outcome": row["outcome"],
                    "participants": row["participants"] or [],
                    "emotional_valence": row["emotional_valence"],
                    "importance": round(float(row["importance"] or 0.5), 2),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })
            return results

    def link_episodes_to_procedure(self, episode_ids: list[str], procedure_id: str):
        """Link episodes to a procedure (after auto-creating from pattern)."""
        if not episode_ids:
            return
        with self._cursor() as cur:
            cur.execute(
                """UPDATE episodes SET linked_procedure_id = %s
                   WHERE id = ANY(%s::uuid[])""",
                (procedure_id, episode_ids)
            )

    # =====================================================
    # MEMORY AGENTS v2.0
    # =====================================================

    AGENT_CURATOR_PROMPT = """You are a Memory Curator Agent. Analyze this user's memory for quality issues.

ALL FACTS (grouped by entity):
{facts_text}

PROCEDURES (workflows/routines):
{procedures_text}

Find these issues:
1. CONTRADICTIONS — facts that conflict with each other (e.g., "lives in Almaty" vs "relocated to USA")
2. STALE FACTS — facts that are likely outdated based on context (old job titles, old plans, completed tasks)
3. LOW QUALITY — vague, trivial, or non-useful facts (e.g., "asked a question", "mentioned something")
4. DUPLICATES — facts that say the same thing differently across entities
5. ENTITY_MERGES — entities that clearly refer to the same real-world person/thing
   (e.g. "Mel" and "Melanie" are the same person, "Bob" and "Robert Smith" are the same person,
    "trans girl" and "Caroline" are the same person based on their facts)
6. PROCEDURE_DUPLICATES — procedures that describe the same workflow (same steps or same goal, different names).
   Keep the one with more steps or higher success_count.

Return JSON:
{{
  "contradictions": [
    {{"fact_a": "...", "fact_b": "...", "entity_a": "...", "entity_b": "...", "suggestion": "keep A / keep B / ask user"}}
  ],
  "stale": [
    {{"fact": "...", "entity": "...", "reason": "why it seems outdated", "confidence": 0.0-1.0}}
  ],
  "low_quality": [
    {{"fact": "...", "entity": "...", "reason": "why it's low quality"}}
  ],
  "duplicates": [
    {{"facts": ["fact1", "fact2"], "entities": ["entity1", "entity2"], "keep": "best version"}}
  ],
  "entity_merges": [
    {{"source": "entity to merge away", "target": "entity to keep (the canonical name)", "reason": "why they are the same"}}
  ],
  "procedure_duplicates": [
    {{"procedures": ["name1", "name2"], "keep": "name of the one to keep", "reason": "why they are duplicates"}}
  ],
  "health_score": 0.0-1.0,
  "summary": "One paragraph overview of memory health"
}}

Be thorough. Real problems only, not nitpicking. No markdown, just JSON."""

    AGENT_CONNECTOR_PROMPT = """You are a Memory Connector Agent. Your job is to find NON-OBVIOUS connections and patterns in this user's memory that they might not see themselves.

ALL FACTS (grouped by entity):
{facts_text}

EXISTING REFLECTIONS:
{reflections_text}

Find:
1. HIDDEN CONNECTIONS — entities that are related in ways not explicitly stated
2. BEHAVIORAL PATTERNS — recurring decision-making or work patterns
3. SKILL CLUSTERS — groups of related skills/knowledge that form expertise areas
4. STRATEGIC INSIGHTS — observations about trajectory, growth areas, blind spots
5. ACTIONABLE SUGGESTIONS — concrete things the user could do based on their memory

Return JSON:
{{
  "connections": [
    {{"entities": ["A", "B"], "connection": "how they're related", "strength": 0.0-1.0, "insight": "why this matters"}}
  ],
  "patterns": [
    {{"pattern": "description", "evidence": ["fact1", "fact2", "..."], "implication": "what this means"}}
  ],
  "skill_clusters": [
    {{"name": "cluster name", "skills": ["skill1", "skill2"], "level": "beginner/intermediate/expert", "growth_direction": "where this is heading"}}
  ],
  "strategic_insights": [
    {{"insight": "observation", "confidence": 0.0-1.0, "category": "career/technical/personal/project"}}
  ],
  "suggestions": [
    {{"action": "what to do", "reason": "why", "priority": "high/medium/low"}}
  ]
}}

Be insightful, not generic. Find things the user wouldn't notice themselves. No markdown, just JSON."""

    AGENT_DIGEST_PROMPT = """You are a Memory Digest Agent. Create a concise activity digest.

RECENT FACTS (last 7 days):
{recent_facts}

ALL-TIME STATS:
- Total entities: {total_entities}
- Total facts: {total_facts}
- Memory health score: {health_score}

RECENT AGENT FINDINGS:
{agent_findings}

Create a digest:
{{
  "headline": "One-line summary of this week's memory activity",
  "highlights": ["3-5 key things that happened in memory this week"],
  "trends": ["2-3 trends you notice"],
  "memory_grew": {{"entities_added": N, "facts_added": N, "facts_archived": N}},
  "focus_areas": ["what the user has been thinking about most"],
  "recommendation": "One actionable recommendation for the user"
}}

Be specific and personal, not generic. No markdown, just JSON."""

    def ensure_agents_table(self):
        """Create agent_runs table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    agent_type VARCHAR(50) NOT NULL,
                    status VARCHAR(20) DEFAULT 'completed',
                    result JSONB,
                    issues_found INTEGER DEFAULT 0,
                    actions_taken INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_runs_user 
                ON agent_runs(user_id, agent_type, created_at DESC)
            """)

    def run_curator_agent(self, user_id: str, llm_client, auto_fix: bool = False, sub_user_id: str = "default") -> dict:
        """Curator Agent — finds contradictions, stale facts, duplicates, low quality."""
        self.ensure_agents_table()

        entities = self.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        if not entities:
            return {"status": "empty", "message": "No memories to curate"}

        # Cap data to prevent LLM token overflow
        facts_lines = []
        total_facts = 0
        for e in entities[:50]:  # max 50 entities
            if not e["facts"]:
                continue
            total_facts += len(e["facts"])
            facts_str = ", ".join(e["facts"][:15])  # max 15 facts per entity
            facts_lines.append(f"- {e['entity']} ({e['type']}): {facts_str}")
        facts_text = "\n".join(facts_lines)
        # Hard cap on text size (~8K chars ≈ 2K tokens)
        if len(facts_text) > 8000:
            facts_text = facts_text[:8000] + "\n... (truncated)"

        # Fetch procedures for dedup analysis
        procedures = self.get_procedures(user_id, limit=50, sub_user_id=sub_user_id)
        proc_lines = []
        for p in procedures:
            steps_str = " → ".join(p["steps"][:10]) if p["steps"] else "(no steps)"
            proc_lines.append(f"- {p['name']} (success={p['success_count']}, steps={len(p['steps'])}): {steps_str}")
        procedures_text = "\n".join(proc_lines) if proc_lines else "(no procedures)"
        if len(procedures_text) > 3000:
            procedures_text = procedures_text[:3000] + "\n... (truncated)"

        prompt = self.AGENT_CURATOR_PROMPT.format(facts_text=facts_text, procedures_text=procedures_text)

        try:
            result = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict):
                    break
                logger.warning(f"⚠️ Curator JSON invalid (attempt {attempt + 1}/2), retrying...")
            if not isinstance(result, dict):
                logger.error("⚠️ Curator agent failed after 2 attempts")
                return {"status": "error", "message": "LLM returned invalid JSON after 2 attempts"}
        except Exception as e:
            logger.error(f"⚠️ Curator agent failed: {e}")
            return {"status": "error", "message": str(e)}

        issues_found = (
            len(result.get("contradictions", [])) +
            len(result.get("stale", [])) +
            len(result.get("low_quality", [])) +
            len(result.get("duplicates", [])) +
            len(result.get("entity_merges", [])) +
            len(result.get("procedure_duplicates", []))
        )

        actions_taken = 0
        # Auto-fix: archive low-quality facts with high confidence
        if auto_fix:
            for item in result.get("low_quality", []):
                entity_name = item.get("entity", "")
                fact = item.get("fact", "")
                entity_id = self.get_entity_id(user_id, entity_name, sub_user_id=sub_user_id)
                if entity_id and fact:
                    with self._cursor() as cur:
                        cur.execute(
                            "UPDATE facts SET archived = TRUE, superseded_by = 'curator: low quality' WHERE entity_id = %s AND content = %s AND archived = FALSE",
                            (entity_id, fact)
                        )
                        if cur.rowcount > 0:
                            actions_taken += 1

            # Auto-fix: archive stale facts with high confidence
            for item in result.get("stale", []):
                if item.get("confidence", 0) >= 0.85:
                    entity_name = item.get("entity", "")
                    fact = item.get("fact", "")
                    entity_id = self.get_entity_id(user_id, entity_name, sub_user_id=sub_user_id)
                    if entity_id and fact:
                        with self._cursor() as cur:
                            cur.execute(
                                "UPDATE facts SET archived = TRUE, superseded_by = 'curator: stale' WHERE entity_id = %s AND content = %s AND archived = FALSE",
                                (entity_id, fact)
                            )
                            if cur.rowcount > 0:
                                actions_taken += 1

            # Auto-fix: merge case-insensitive duplicate entities
            try:
                merged = self._auto_merge_duplicate_entities(user_id, sub_user_id)
                actions_taken += merged
            except Exception as e:
                logger.warning(f"⚠️ Auto entity merge failed: {e}")

            # Auto-fix: merge entities identified by LLM as same real-world entity
            for item in result.get("entity_merges", []):
                source_name = item.get("source", "")
                target_name = item.get("target", "")
                if not source_name or not target_name:
                    continue
                source_id = self.get_entity_id(user_id, source_name, sub_user_id=sub_user_id)
                target_id = self.get_entity_id(user_id, target_name, sub_user_id=sub_user_id)
                if source_id and target_id and source_id != target_id:
                    try:
                        self.merge_entities(user_id, source_id, target_id, target_name)
                        actions_taken += 1
                        logger.info(f"Curator merged '{source_name}' -> '{target_name}'")
                    except Exception as e:
                        logger.warning(f"Curator entity merge failed '{source_name}' -> '{target_name}': {e}")

            # Auto-fix: dedup facts on entities with many facts
            try:
                for e in entities[:20]:
                    if len(e.get("facts", [])) >= 5:
                        entity_name = e["entity"]
                        entity_id = self.get_entity_id(user_id, entity_name, sub_user_id=sub_user_id)
                        if entity_id:
                            try:
                                dedup_result = self.dedup_entity_facts(entity_id, entity_name, llm_client)
                                actions_taken += len(dedup_result.get("archived", []))
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"⚠️ Auto fact dedup failed: {e}")

            # Auto-fix: archive duplicate procedures (keep the better one)
            for item in result.get("procedure_duplicates", []):
                proc_names = item.get("procedures", [])
                keep_name = item.get("keep", "")
                if len(proc_names) < 2 or not keep_name:
                    continue
                # Archive all procedures that aren't the one to keep
                for pname in proc_names:
                    if pname.strip().lower() == keep_name.strip().lower():
                        continue
                    try:
                        with self._cursor() as cur:
                            cur.execute(
                                """UPDATE procedures SET is_current = FALSE, updated_at = NOW()
                                   WHERE user_id = %s AND sub_user_id = %s
                                     AND LOWER(name) = LOWER(%s) AND is_current = TRUE""",
                                (user_id, sub_user_id, pname)
                            )
                            if cur.rowcount > 0:
                                actions_taken += cur.rowcount
                                logger.info(f"Curator archived duplicate procedure '{pname}' (keeping '{keep_name}')")
                    except Exception as e:
                        logger.warning(f"Curator procedure dedup failed for '{pname}': {e}")

        # Save run
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO agent_runs (user_id, agent_type, result, issues_found, actions_taken) VALUES (%s, %s, %s, %s, %s)",
                (user_id, "curator", json.dumps(result), issues_found, actions_taken)
            )

        result["_meta"] = {
            "issues_found": issues_found,
            "actions_taken": actions_taken,
            "total_facts_scanned": total_facts,
            "auto_fix": auto_fix
        }

        logger.info(f"🧹 Curator agent: {issues_found} issues, {actions_taken} auto-fixed for {user_id}")
        return result

    @staticmethod
    def infer_entity_type(name: str, facts: list[str]) -> str:
        """Heuristic fallback for entity type when LLM returns 'unknown'."""
        name_lower = name.lower()
        facts_text = " ".join(str(f) for f in facts).lower() if facts else ""
        all_text = f"{name_lower} {facts_text}"

        # Technology indicators
        tech_kw = {"python", "javascript", "typescript", "react", "vue", "angular", "node",
                   "postgres", "postgresql", "redis", "docker", "kubernetes", "k8s", "aws",
                   "gcp", "azure", "api", "sdk", "framework", "library", "database", "linux",
                   "git", "github", "npm", "pip", "rust", "golang", "swift", "kotlin",
                   "terraform", "nginx", "graphql", "mongodb", "mysql", "sqlite", "kafka",
                   "elasticsearch", "supabase", "railway", "vercel", "netlify", "heroku"}
        if any(kw in name_lower for kw in tech_kw) or name_lower.endswith((".js", ".py", ".rs", ".go")):
            return "technology"

        # Company indicators
        company_kw = {"company", "startup", "corporation", "inc.", "ltd.", "founded", "headquartered",
                      "employees", "ceo", "revenue", "acquired", "ipo", "b2b", "b2c", "saas"}
        if any(kw in all_text for kw in company_kw):
            return "company"

        # Person indicators
        person_kw = {"works at", "lives in", "born", "developer", "engineer", "designer",
                     "manager", "founder", "cto", "ceo", "prefers", "enjoys", "studied",
                     "graduated", "speaks", "married", "colleague"}
        if any(kw in all_text for kw in person_kw):
            return "person"

        # Project indicators
        project_kw = {"project", "repository", "repo", "codebase", "app", "application",
                      "built with", "deployed", "launched", "version", "release"}
        if any(kw in all_text for kw in project_kw):
            return "project"

        # Place indicators
        place_kw = {"city", "country", "located in", "capital", "population", "state", "region"}
        if any(kw in all_text for kw in place_kw):
            return "place"

        return "unknown"

    def reclassify_unknown_entities(self, user_id: str, llm_client, sub_user_id: str = "default") -> dict:
        """Reclassify entities with type='unknown' using LLM batch classification.
        Processes in batches of 40 to stay within token limits."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT e.id, e.name,
                          (SELECT array_agg(sub.content) FROM (
                              SELECT f.content FROM facts f
                              WHERE f.entity_id = e.id AND f.archived = FALSE
                              ORDER BY f.importance DESC NULLS LAST LIMIT 5
                          ) sub) as sample_facts
                   FROM entities e
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.type = 'unknown'
                   ORDER BY (SELECT count(*) FROM facts WHERE entity_id = e.id AND archived = FALSE) DESC""",
                (user_id, sub_user_id)
            )
            unknowns = cur.fetchall()

        if not unknowns:
            return {"reclassified": 0, "total_unknown": 0}

        reclassified = 0
        batch_size = 40

        for i in range(0, len(unknowns), batch_size):
            batch = unknowns[i:i + batch_size]
            lines = []
            for ent in batch:
                facts = ent["sample_facts"] or []
                facts_str = "; ".join(str(f) for f in facts[:5] if f)
                lines.append(f"- {ent['name']}: {facts_str}" if facts_str else f"- {ent['name']}")

            prompt = f"""Classify each entity with the single most descriptive type.
Common types: person, project, technology, company, concept, place, activity.
You may use other types if they fit better (e.g. event, book, tool, food, pet, game, language, sport).
Use lowercase, single-word or hyphenated types.

ENTITIES:
{chr(10).join(lines)}

Return ONLY JSON (no markdown):
{{
  "classifications": [
    {{"name": "Entity Name", "type": "person"}},
    ...
  ]
}}"""

            try:
                result = None
                for attempt in range(2):
                    response = llm_client.complete(prompt, response_format={"type": "json_object"})
                    result = _safe_parse_json(response)
                    if isinstance(result, dict) and "classifications" in result:
                        break
                    logger.warning(f"⚠️ Reclassify JSON invalid (attempt {attempt + 1}/2), retrying...")
                if not isinstance(result, dict) or "classifications" not in result:
                    continue
            except Exception as e:
                logger.error(f"⚠️ Reclassify batch failed: {e}")
                continue

            name_to_id = {ent["name"].lower(): str(ent["id"]) for ent in batch}
            for item in result["classifications"]:
                ent_name = item.get("name", "")
                ent_type = item.get("type", "").lower().strip()
                if not ent_type or len(ent_type) > 50:
                    continue
                entity_id = name_to_id.get(ent_name.lower())
                if not entity_id:
                    continue
                with self._cursor() as cur:
                    cur.execute(
                        "UPDATE entities SET type = %s, updated_at = NOW() WHERE id = %s AND type = 'unknown'",
                        (ent_type, entity_id)
                    )
                    if cur.rowcount > 0:
                        reclassified += 1

        logger.info(f"🏷️ Reclassified {reclassified}/{len(unknowns)} unknown entities for {user_id}")
        return {"reclassified": reclassified, "total_unknown": len(unknowns)}

    def run_connector_agent(self, user_id: str, llm_client, sub_user_id: str = "default") -> dict:
        """Connector Agent — finds hidden connections, patterns, insights."""
        self.ensure_agents_table()

        entities = self.get_all_entities_full(user_id, sub_user_id=sub_user_id)
        if not entities:
            return {"status": "empty", "message": "No memories to analyze"}

        facts_lines = []
        for e in entities[:50]:  # max 50 entities
            if not e["facts"]:
                continue
            facts_str = ", ".join(e["facts"][:15])
            facts_lines.append(f"- {e['entity']} ({e['type']}): {facts_str}")
        facts_text = "\n".join(facts_lines)
        if len(facts_text) > 8000:
            facts_text = facts_text[:8000] + "\n... (truncated)"

        # Get existing reflections
        prev = self.get_reflections(user_id, sub_user_id=sub_user_id)
        reflections_text = "(none)"
        if prev:
            r_lines = [f"- [{r['scope']}] {r['title']}: {r['content'][:150]}" for r in prev[:8]]
            reflections_text = "\n".join(r_lines)

        prompt = self.AGENT_CONNECTOR_PROMPT.format(
            facts_text=facts_text,
            reflections_text=reflections_text
        )

        try:
            result = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict):
                    break
                logger.warning(f"⚠️ Connector JSON invalid (attempt {attempt + 1}/2), retrying...")
            if not isinstance(result, dict):
                logger.error("⚠️ Connector agent failed after 2 attempts")
                return {"status": "error", "message": "LLM returned invalid JSON after 2 attempts"}
        except Exception as e:
            logger.error(f"⚠️ Connector agent failed: {e}")
            return {"status": "error", "message": str(e)}

        issues_found = (
            len(result.get("connections", [])) +
            len(result.get("patterns", [])) +
            len(result.get("strategic_insights", [])) +
            len(result.get("suggestions", []))
        )

        # Save run
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO agent_runs (user_id, agent_type, result, issues_found) VALUES (%s, %s, %s, %s)",
                (user_id, "connector", json.dumps(result), issues_found)
            )

        logger.info(f"🔗 Connector agent: {issues_found} insights for {user_id}")
        return result

    def run_digest_agent(self, user_id: str, llm_client, sub_user_id: str = "default") -> dict:
        """Digest Agent — generates weekly activity summary."""
        self.ensure_agents_table()

        # Recent facts (last 7 days)
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT f.content, e.name as entity_name, f.created_at
                FROM facts f
                JOIN entities e ON e.id = f.entity_id
                WHERE e.user_id = %s AND e.sub_user_id = %s AND f.created_at > NOW() - INTERVAL '7 days'
                AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())
                ORDER BY f.created_at DESC LIMIT 50
            """, (user_id, sub_user_id))
            recent = cur.fetchall()

        recent_facts = "(no recent activity)"
        if recent:
            lines = [f"- [{r['entity_name']}] {r['content']} ({r['created_at'].strftime('%m/%d')})" for r in recent]
            recent_facts = "\n".join(lines)

        # Stats
        stats = self.get_stats(user_id, sub_user_id=sub_user_id)

        # Last curator/connector results
        agent_findings = "(none)"
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT agent_type, result, issues_found, created_at
                FROM agent_runs
                WHERE user_id = %s AND created_at > NOW() - INTERVAL '7 days'
                ORDER BY created_at DESC LIMIT 3
            """, (user_id,))
            runs = cur.fetchall()
            if runs:
                lines = []
                for r in runs:
                    res = r["result"] if isinstance(r["result"], dict) else json.loads(r["result"])
                    summary = res.get("summary", res.get("headline", f"{r['issues_found']} findings"))
                    lines.append(f"- {r['agent_type']}: {summary}")
                agent_findings = "\n".join(lines)

        # Get health score from last curator run
        health_score = "N/A"
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT result FROM agent_runs
                WHERE user_id = %s AND agent_type = 'curator'
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,))
            row = cur.fetchone()
            if row:
                res = row["result"] if isinstance(row["result"], dict) else json.loads(row["result"])
                health_score = str(res.get("health_score", "N/A"))

        prompt = self.AGENT_DIGEST_PROMPT.format(
            recent_facts=recent_facts,
            total_entities=stats.get("entities", 0),
            total_facts=stats.get("facts", 0),
            health_score=health_score,
            agent_findings=agent_findings
        )

        try:
            result = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict):
                    break
                logger.warning(f"⚠️ Digest JSON invalid (attempt {attempt + 1}/2), retrying...")
            if not isinstance(result, dict):
                logger.error("⚠️ Digest agent failed after 2 attempts")
                return {"status": "error", "message": "LLM returned invalid JSON after 2 attempts"}
        except Exception as e:
            logger.error(f"⚠️ Digest agent failed: {e}")
            return {"status": "error", "message": str(e)}

        # Save run
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO agent_runs (user_id, agent_type, result, issues_found) VALUES (%s, %s, %s, %s)",
                (user_id, "digest", json.dumps(result), len(result.get("highlights", [])))
            )

        logger.info(f"📰 Digest agent completed for {user_id}")
        return result

    def run_all_agents(self, user_id: str, llm_client, auto_fix: bool = False, sub_user_id: str = "default") -> dict:
        """Run all agents in sequence."""
        results = {}

        logger.info(f"🤖 Running all agents for {user_id}...")

        # 0. Reclassify unknown entities first (improves all downstream agent quality)
        results["reclassify"] = self.reclassify_unknown_entities(user_id, llm_client, sub_user_id=sub_user_id)

        # 1. Curator (clean up)
        results["curator"] = self.run_curator_agent(user_id, llm_client, auto_fix=auto_fix, sub_user_id=sub_user_id)

        # 2. Connector (find patterns in clean data)
        results["connector"] = self.run_connector_agent(user_id, llm_client, sub_user_id=sub_user_id)

        # 3. Digest (summarize everything)
        results["digest"] = self.run_digest_agent(user_id, llm_client, sub_user_id=sub_user_id)

        logger.info(f"✅ All agents completed for {user_id}")
        return results

    def get_agent_history(self, user_id: str, agent_type: str = None, limit: int = 10) -> list:
        """Get history of agent runs."""
        self.ensure_agents_table()
        with self._cursor(dict_cursor=True) as cur:
            if agent_type:
                cur.execute("""
                    SELECT agent_type, status, result, issues_found, actions_taken, created_at
                    FROM agent_runs WHERE user_id = %s AND agent_type = %s
                    ORDER BY created_at DESC LIMIT %s
                """, (user_id, agent_type, limit))
            else:
                cur.execute("""
                    SELECT agent_type, status, result, issues_found, actions_taken, created_at
                    FROM agent_runs WHERE user_id = %s
                    ORDER BY created_at DESC LIMIT %s
                """, (user_id, limit))
            rows = cur.fetchall()
            return [{
                "agent_type": r["agent_type"],
                "status": r["status"],
                "result": r["result"] if isinstance(r["result"], dict) else json.loads(r["result"]) if r["result"] else {},
                "issues_found": r["issues_found"],
                "actions_taken": r["actions_taken"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None
            } for r in rows]

    def should_run_agents(self, user_id: str, sub_user_id: str = "default") -> dict:
        """Check if agents should run. Returns which agents are due."""
        self.ensure_agents_table()
        due = {}
        with self._cursor(dict_cursor=True) as cur:
            for agent in ["curator", "connector", "digest"]:
                cur.execute("""
                    SELECT created_at FROM agent_runs
                    WHERE user_id = %s AND agent_type = %s
                    ORDER BY created_at DESC LIMIT 1
                """, (user_id, agent))
                row = cur.fetchone()
                if not row:
                    due[agent] = True
                else:
                    hours_since = (datetime.datetime.now(datetime.timezone.utc) - row["created_at"]).total_seconds() / 3600
                    # Curator: every 24h, Connector: every 48h, Digest: every 7 days
                    thresholds = {"curator": 24, "connector": 48, "digest": 168}
                    due[agent] = hours_since >= thresholds.get(agent, 24)
        return due

    # =====================================================
    # WEBHOOKS
    # =====================================================

    def ensure_webhooks_table(self):
        """Create webhooks table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    url TEXT NOT NULL,
                    name VARCHAR(255) DEFAULT '',
                    event_types JSONB DEFAULT '["memory_add","memory_update","memory_delete"]',
                    secret VARCHAR(255) DEFAULT '',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    last_triggered TIMESTAMPTZ,
                    trigger_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    consecutive_failures INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_webhooks_user
                ON webhooks(user_id, active)
            """)
            # Migration: add column for existing tables
            cur.execute("""
                ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS
                consecutive_failures INTEGER DEFAULT 0
            """)

    def create_webhook(self, user_id: str, url: str, name: str = "",
                       event_types: list = None, secret: str = "") -> dict:
        """Create a new webhook."""
        self.ensure_webhooks_table()
        if not event_types:
            event_types = ["memory_add", "memory_update", "memory_delete"]

        # Validate event types
        valid = {"memory_add", "memory_update", "memory_delete"}
        for et in event_types:
            if et not in valid:
                raise ValueError(f"Invalid event type: {et}. Valid: {', '.join(valid)}")

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                INSERT INTO webhooks (user_id, url, name, event_types, secret)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, user_id, url, name, event_types, secret, active, created_at
            """, (user_id, url, name, json.dumps(event_types), secret))
            row = cur.fetchone()
            return {
                "id": row["id"],
                "url": row["url"],
                "name": row["name"],
                "event_types": row["event_types"],
                "active": row["active"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None
            }

    def get_webhooks(self, user_id: str) -> list:
        """Get all webhooks for a user."""
        self.ensure_webhooks_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, url, name, event_types, active, created_at,
                       last_triggered, trigger_count, last_error
                FROM webhooks WHERE user_id = %s ORDER BY created_at DESC
            """, (user_id,))
            return [{
                "id": r["id"],
                "url": r["url"],
                "name": r["name"],
                "event_types": r["event_types"] if isinstance(r["event_types"], list) else json.loads(r["event_types"]),
                "active": r["active"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_triggered": r["last_triggered"].isoformat() if r["last_triggered"] else None,
                "trigger_count": r["trigger_count"],
                "last_error": r["last_error"]
            } for r in cur.fetchall()]

    def update_webhook(self, user_id: str, webhook_id: int,
                       url: str = None, name: str = None,
                       event_types: list = None, active: bool = None) -> dict:
        """Update a webhook."""
        self.ensure_webhooks_table()
        updates = []
        params = []
        if url is not None:
            updates.append("url = %s")
            params.append(url)
        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if event_types is not None:
            updates.append("event_types = %s")
            params.append(json.dumps(event_types))
        if active is not None:
            updates.append("active = %s")
            params.append(active)

        if not updates:
            return {"status": "no changes"}

        params.extend([webhook_id, user_id])
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE webhooks SET {', '.join(updates)} WHERE id = %s AND user_id = %s",
                params
            )
            return {"status": "updated", "id": webhook_id}

    def delete_webhook(self, user_id: str, webhook_id: int) -> bool:
        """Delete a webhook."""
        self.ensure_webhooks_table()
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM webhooks WHERE id = %s AND user_id = %s",
                (webhook_id, user_id)
            )
            return cur.rowcount > 0

    # Shared thread pool for webhook delivery (limits concurrent outbound connections)
    _webhook_pool = None

    def _get_webhook_pool(self):
        if self._webhook_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            self._webhook_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook")
        return self._webhook_pool

    def fire_webhooks(self, user_id: str, event_type: str, payload: dict):
        """Fire all active webhooks for this event type. Non-blocking, thread-pool limited."""
        self.ensure_webhooks_table()
        import urllib.request, urllib.error

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, url, secret FROM webhooks
                WHERE user_id = %s AND active = TRUE
                AND event_types ? %s
            """, (user_id, event_type))
            hooks = cur.fetchall()

        if not hooks:
            return

        data = json.dumps({
            "event": event_type,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": payload
        }).encode("utf-8")

        def _send(hook_id, url, secret):
            # Validate URL before sending (prevent SSRF via DNS rebinding)
            import urllib.parse
            import socket
            import ipaddress as _ipa
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.hostname or ""
            if hostname in ("localhost", "0.0.0.0", "metadata.google.internal") or hostname.endswith(".internal") or hostname.endswith(".local"):
                logger.warning(f"⚠️ Webhook {hook_id} blocked: internal hostname {hostname}")
                return
            try:
                resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for family, _, _, _, sockaddr in resolved:
                    ip = _ipa.ip_address(sockaddr[0])
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        logger.warning(f"⚠️ Webhook {hook_id} blocked: {hostname} → private IP {ip}")
                        return
            except (socket.gaierror, ValueError):
                pass
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"}
                )
                if secret:
                    import hmac as _hmac
                    sig = _hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
                    req.add_header("X-Mengram-Signature", sig)

                import time
                # Retry with exponential backoff on transient errors (429, 5xx)
                last_err = None
                for attempt in range(3):  # up to 3 attempts
                    try:
                        if attempt > 0:
                            time.sleep(min(2 ** attempt, 8))  # 2s, 4s backoff
                        urllib.request.urlopen(req, timeout=10)
                        # Success — record it
                        with self._cursor() as cur2:
                            cur2.execute("""
                                UPDATE webhooks SET last_triggered = NOW(),
                                trigger_count = trigger_count + 1,
                                last_error = NULL, consecutive_failures = 0
                                WHERE id = %s
                            """, (hook_id,))
                        return  # delivered
                    except urllib.error.HTTPError as he:
                        last_err = he
                        if he.code in (429, 500, 502, 503, 504):
                            continue  # transient — retry
                        break  # 4xx (not 429) — don't retry
                    except Exception as ex:
                        last_err = ex
                        break  # network error — don't retry

                # All retries failed
                err_msg = str(last_err)[:500]
                logger.error(f"⚠️ Webhook {hook_id} failed after retries: {last_err}")
                try:
                    with self._cursor() as cur2:
                        cur2.execute("""
                            UPDATE webhooks SET last_error = %s,
                            consecutive_failures = consecutive_failures + 1
                            WHERE id = %s
                        """, (err_msg, hook_id))
                        # Auto-disable after 20 consecutive failures
                        cur2.execute("""
                            UPDATE webhooks SET active = FALSE
                            WHERE id = %s AND consecutive_failures >= 20
                        """, (hook_id,))
                except Exception:
                    pass
            except Exception as exc:
                logger.error(f"⚠️ Webhook {hook_id} unexpected error: {exc}")

        def _send_all_sequential():
            """Send webhooks sequentially with 150ms delay to prevent rate limiting."""
            import time
            for i, hook in enumerate(hooks):
                if i > 0:
                    time.sleep(0.15)
                _send(hook["id"], hook["url"], hook["secret"] or "")

        pool = self._get_webhook_pool()
        pool.submit(_send_all_sequential)

        logger.info(f"🔔 Fired {len(hooks)} webhooks for {event_type} ({user_id})")

    # =====================================================
    # SHARED MEMORY — TEAMS
    # =====================================================

    def ensure_teams_table(self):
        """Create teams infrastructure."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT DEFAULT '',
                    invite_code VARCHAR(20) UNIQUE NOT NULL,
                    created_by VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_members (
                    id SERIAL PRIMARY KEY,
                    team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
                    user_id VARCHAR(255) NOT NULL,
                    role VARCHAR(20) DEFAULT 'member',
                    joined_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(team_id, user_id)
                )
            """)
            # Add team_id column to entities if not exists
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE entities ADD COLUMN team_id INTEGER REFERENCES teams(id);
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_entities_team
                ON entities(team_id) WHERE team_id IS NOT NULL
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_team_members_user
                ON team_members(user_id)
            """)

    def create_team(self, user_id: str, name: str, description: str = "") -> dict:
        """Create a new team. Creator becomes owner."""
        self.ensure_teams_table()
        invite_code = secrets.token_urlsafe(8)[:10]

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                INSERT INTO teams (name, description, invite_code, created_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, description, invite_code, created_at
            """, (name, description, invite_code, user_id))
            team = cur.fetchone()
            team_id = team["id"]

            # Creator is owner
            cur.execute("""
                INSERT INTO team_members (team_id, user_id, role)
                VALUES (%s, %s, 'owner')
            """, (team_id, user_id))

            self.cache.invalidate(f"teams:{user_id}")
            return {
                "id": team_id,
                "name": team["name"],
                "description": team["description"],
                "invite_code": team["invite_code"],
                "role": "owner",
                "created_at": team["created_at"].isoformat() if team["created_at"] else None
            }

    def join_team(self, user_id: str, invite_code: str) -> dict:
        """Join a team via invite code."""
        self.ensure_teams_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("SELECT id, name FROM teams WHERE invite_code = %s", (invite_code,))
            team = cur.fetchone()
            if not team:
                raise ValueError("Invalid invite code")

            try:
                cur.execute("""
                    INSERT INTO team_members (team_id, user_id, role)
                    VALUES (%s, %s, 'member')
                """, (team["id"], user_id))
            except psycopg2.errors.UniqueViolation:
                raise ValueError("Already a member of this team")

            self.cache.invalidate(f"teams:{user_id}")
            return {"team_id": team["id"], "team_name": team["name"], "role": "member"}

    def get_user_teams(self, user_id: str) -> list:
        """Get all teams user belongs to."""
        self.ensure_teams_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT t.id, t.name, t.description, t.invite_code,
                       tm.role, t.created_by, t.created_at,
                       (SELECT COUNT(*) FROM team_members WHERE team_id = t.id) as member_count,
                       (SELECT COUNT(*) FROM entities WHERE team_id = t.id) as shared_memories
                FROM teams t
                JOIN team_members tm ON tm.team_id = t.id
                WHERE tm.user_id = %s
                ORDER BY t.created_at DESC
            """, (user_id,))
            return [{
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "invite_code": r["invite_code"] if r["role"] == "owner" else None,
                "role": r["role"],
                "member_count": r["member_count"],
                "shared_memories": r["shared_memories"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None
            } for r in cur.fetchall()]

    def get_team_members(self, user_id: str, team_id: int) -> list:
        """Get members of a team (must be a member)."""
        self.ensure_teams_table()
        with self._cursor(dict_cursor=True) as cur:
            # Check membership
            cur.execute(
                "SELECT role FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id)
            )
            if not cur.fetchone():
                raise ValueError("Not a member of this team")

            cur.execute("""
                SELECT user_id, role, joined_at
                FROM team_members WHERE team_id = %s
                ORDER BY joined_at
            """, (team_id,))
            return [{
                "user_id": r["user_id"],
                "role": r["role"],
                "joined_at": r["joined_at"].isoformat() if r["joined_at"] else None
            } for r in cur.fetchall()]

    def leave_team(self, user_id: str, team_id: int) -> bool:
        """Leave a team."""
        self.ensure_teams_table()
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM team_members WHERE team_id = %s AND user_id = %s AND role != 'owner'",
                (team_id, user_id)
            )
            left = cur.rowcount > 0
        if left:
            self.cache.invalidate(f"teams:{user_id}")
        return left

    def delete_team(self, user_id: str, team_id: int) -> bool:
        """Delete a team (owner only). Shared entities become personal to their creators."""
        self.ensure_teams_table()
        with self._cursor() as cur:
            cur.execute(
                "SELECT role FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id)
            )
            row = cur.fetchone()
            if not row or row[0] != "owner":
                raise ValueError("Only the owner can delete a team")

            # Unshare all entities (they become personal again)
            cur.execute("UPDATE entities SET team_id = NULL WHERE team_id = %s", (team_id,))
            cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
            self.cache.invalidate(f"teams:{user_id}")
            return True

    def share_entity(self, user_id: str, entity_name: str, team_id: int, sub_user_id: str = "default") -> dict:
        """Share a personal entity with a team."""
        self.ensure_teams_table()
        # Verify membership
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                "SELECT 1 FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id)
            )
            if not cur.fetchone():
                raise ValueError("Not a member of this team")

            cur.execute(
                "UPDATE entities SET team_id = %s WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)",
                (team_id, user_id, sub_user_id, entity_name)
            )
            if cur.rowcount == 0:
                raise ValueError(f"Entity '{entity_name}' not found")
            return {"entity": entity_name, "team_id": team_id, "status": "shared"}

    def unshare_entity(self, user_id: str, entity_name: str, sub_user_id: str = "default") -> dict:
        """Make a shared entity personal again."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE entities SET team_id = NULL WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)",
                (user_id, sub_user_id, entity_name)
            )
            return {"entity": entity_name, "status": "personal"}

    def get_user_team_ids(self, user_id: str) -> list:
        """Get list of team IDs user belongs to. Cached 60s."""
        cache_key = f"teams:{user_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        self.ensure_teams_table()
        with self._cursor() as cur:
            cur.execute(
                "SELECT team_id FROM team_members WHERE user_id = %s", (user_id,)
            )
            result = [r[0] for r in cur.fetchall()]
        self.cache.set(cache_key, result, ttl=60)
        return result

    def search_vector_with_teams(self, user_id: str, embedding: list[float],
                                  top_k: int = 5, min_score: float = 0.3,
                                  query_text: str = "",
                                  graph_depth: int = 2,
                                  sub_user_id: str = "default",
                                  meta_filters: dict = None) -> list[dict]:
        """
        Same as search_vector but includes shared team memories.
        Results from team entities are marked with team_shared=True.
        Includes graph expansion and relations in results.
        """
        team_ids = self.get_user_team_ids(user_id)

        if not team_ids:
            # No teams — use normal search
            return self.search_vector(user_id, embedding, top_k, min_score, query_text, graph_depth, sub_user_id=sub_user_id, meta_filters=meta_filters)

        embedding_str = f"[{','.join(str(x) for x in embedding)}]"

        # Build metadata filter clause
        meta_clause = ""
        meta_params = []
        if meta_filters:
            meta_clause = " AND e.metadata @> %s::jsonb"
            meta_params = [json.dumps(meta_filters)]

        with self._cursor(dict_cursor=True) as cur:
            # Vector search: personal + team entities
            cur.execute(
                f"""SELECT DISTINCT ON (e.id)
                       e.id, e.name, e.type, e.user_id, e.team_id,
                       1 - (emb.embedding <=> %s::vector) AS score,
                       e.updated_at, e.metadata
                   FROM embeddings emb
                   JOIN entities e ON e.id = emb.entity_id
                   WHERE ((e.user_id = %s AND e.sub_user_id = %s) OR e.team_id = ANY(%s))
                     AND 1 - (emb.embedding <=> %s::vector) > %s
                     {meta_clause}
                   ORDER BY e.id, score DESC""",
                (embedding_str, user_id, sub_user_id, team_ids, embedding_str, min_score, *meta_params)
            )
            vector_rows = cur.fetchall()
            vector_rows.sort(key=lambda r: float(r["score"]), reverse=True)

            # Cosine floor: if best vector result < 0.25, query is unrelated to anything in memory
            if vector_rows and float(vector_rows[0]["score"]) < 0.25:
                return []

            vector_ranked = {str(r["id"]): (i + 1, r) for i, r in enumerate(vector_rows[:20])}

            # BM25 text search
            bm25_ranked = {}
            if query_text:
                words = [w.strip() for w in query_text.split() if len(w.strip()) >= 2]
                if words:
                    cur.execute(
                        f"""SELECT DISTINCT ON (e.id)
                               e.id, e.name, e.type, e.user_id, e.team_id,
                               ts_rank_cd(emb.tsv, plainto_tsquery('english', %s), 32) AS rank,
                               e.updated_at, e.metadata
                           FROM embeddings emb
                           JOIN entities e ON e.id = emb.entity_id
                           WHERE ((e.user_id = %s AND e.sub_user_id = %s) OR e.team_id = ANY(%s))
                             AND emb.tsv @@ plainto_tsquery('english', %s)
                             {meta_clause}
                           ORDER BY e.id, rank DESC""",
                        (query_text, user_id, sub_user_id, team_ids, query_text, *meta_params)
                    )
                    bm25_rows = cur.fetchall()
                    bm25_rows.sort(key=lambda r: float(r["rank"]), reverse=True)
                    bm25_ranked = {str(r["id"]): (i + 1, r) for i, r in enumerate(bm25_rows[:20])}

            # RRF merge
            k = 60
            all_entity_ids = set(vector_ranked.keys()) | set(bm25_ranked.keys())
            rrf_scores = {}
            entity_info = {}

            for eid in all_entity_ids:
                score = 0.0
                if eid in vector_ranked:
                    rank, row = vector_ranked[eid]
                    score += 1.0 / (k + rank)
                    entity_info[eid] = {
                        "name": row["name"], "type": row["type"],
                        "updated_at": row.get("updated_at"),
                        "metadata": row.get("metadata") or {},
                        "team_shared": row["team_id"] is not None and row["user_id"] != user_id
                    }
                if eid in bm25_ranked:
                    rank, row = bm25_ranked[eid]
                    score += 1.0 / (k + rank)
                    if eid not in entity_info:
                        entity_info[eid] = {
                            "name": row["name"], "type": row["type"],
                            "updated_at": row.get("updated_at"),
                            "metadata": row.get("metadata") or {},
                            "team_shared": row["team_id"] is not None and row["user_id"] != user_id
                        }
                rrf_scores[eid] = score

            # ========== Graph expansion (multi-hop) ==========
            sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            seed_ids = [eid for eid, _ in sorted_rrf[:8]]
            max_rrf_val = max(rrf_scores.values()) if rrf_scores else 0.01

            graph_entities = self._graph_expand(
                cur, user_id, seed_ids, max_hops=graph_depth, max_rrf=max_rrf_val,
                sub_user_id=sub_user_id
            )
            graph_expanded_ids = set()
            for eid, info in graph_entities.items():
                if eid not in rrf_scores:
                    rrf_scores[eid] = info["score"]
                    entity_info[eid] = {
                        "name": info["name"], "type": info["type"],
                        "updated_at": info.get("updated_at"),
                        "team_shared": False,
                    }
                    graph_expanded_ids.add(eid)

            # Sort, filter by minimum RRF score, and limit
            # Direct matches: fixed floor 0.01. Graph-expanded: stricter adaptive floor.
            sorted_final = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            top_score = sorted_final[0][1] if sorted_final else 0
            min_rrf_graph = max(0.01, top_score * 0.4)
            sorted_results = [(eid, score) for eid, score in sorted_final
                              if (eid in graph_expanded_ids and score >= min_rrf_graph) or
                                 (eid not in graph_expanded_ids and score >= 0.01)][:top_k]

            if not sorted_results:
                return []

            entity_ids = [eid for eid, _ in sorted_results]

            # Batch fetch facts
            cur.execute(
                """SELECT entity_id, content, importance, event_date FROM facts
                   WHERE entity_id = ANY(%s::uuid[]) AND archived = FALSE
                     AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY importance DESC, created_at DESC""",
                (entity_ids,)
            )
            facts_rows = cur.fetchall()  # Save before next query overwrites cursor

            # Fact-level relevance: rank facts by embedding similarity to query
            chunk_relevance = {}  # eid → {fact_content → relevance_score}
            if entity_ids:
                cur.execute(
                    """SELECT entity_id, chunk_text,
                              1 - (embedding <=> %s::vector) AS relevance
                       FROM embeddings
                       WHERE entity_id = ANY(%s::uuid[])""",
                    (embedding_str, entity_ids)
                )
                for row in cur.fetchall():
                    eid = str(row["entity_id"])
                    text = row["chunk_text"]
                    rel = float(row["relevance"])
                    fact_text = text.split(": ", 1)[1] if ": " in text else text
                    if eid not in chunk_relevance:
                        chunk_relevance[eid] = {}
                    chunk_relevance[eid][fact_text] = max(
                        chunk_relevance[eid].get(fact_text, 0), rel
                    )

            # Build facts with combined relevance + importance scoring
            facts_raw = {}  # eid → [(text, combined_score, relevance)]
            for r in facts_rows:
                eid = str(r["entity_id"])
                if eid not in facts_raw:
                    facts_raw[eid] = []
                relevances = chunk_relevance.get(eid, {})
                relevance = relevances.get(r["content"], 0)
                importance = float(r["importance"] or 0.5)
                combined = 0.7 * relevance + 0.3 * importance
                fact_str = f"[{r['event_date']}] {r['content']}" if r.get("event_date") else r["content"]
                facts_raw[eid].append((fact_str, combined, relevance))

            # Sort by combined score, filter low-relevance junk, keep top 15 per entity
            facts_map = {}
            for eid, facts_list in facts_raw.items():
                facts_list.sort(key=lambda x: x[1], reverse=True)
                facts_map[eid] = [f[0] for f in facts_list if f[2] >= 0.15][:15]

            # Batch fetch knowledge
            cur.execute(
                """SELECT entity_id, type, title, content, artifact FROM knowledge
                   WHERE entity_id = ANY(%s::uuid[])""",
                (entity_ids,)
            )
            knowledge_map = {}
            for r in cur.fetchall():
                eid = str(r["entity_id"])
                if eid not in knowledge_map:
                    knowledge_map[eid] = []
                if len(knowledge_map[eid]) < 5:
                    knowledge_map[eid].append({
                        "type": r["type"], "title": r["title"],
                        "content": r["content"], "artifact": r["artifact"],
                    })

            # Batch fetch relations
            cur.execute(
                """SELECT r.source_id, r.target_id, r.type, r.description,
                          se.name AS source_name, te.name AS target_name
                   FROM relations r
                   JOIN entities se ON se.id = r.source_id
                   JOIN entities te ON te.id = r.target_id
                   WHERE r.source_id = ANY(%s::uuid[]) OR r.target_id = ANY(%s::uuid[])""",
                (entity_ids, entity_ids)
            )
            relations_map = {}
            for r in cur.fetchall():
                src = str(r["source_id"])
                tgt = str(r["target_id"])
                if src in entity_ids:
                    if src not in relations_map:
                        relations_map[src] = []
                    relations_map[src].append({
                        "type": r["type"], "direction": "outgoing",
                        "target": r["target_name"], "detail": r["description"] or "",
                    })
                if tgt in entity_ids:
                    if tgt not in relations_map:
                        relations_map[tgt] = []
                    relations_map[tgt].append({
                        "type": r["type"], "direction": "incoming",
                        "target": r["source_name"], "detail": r["description"] or "",
                    })

            # Build results
            results = []
            for eid, score in sorted_results:
                info = entity_info[eid]
                results.append({
                    "entity": info["name"],
                    "type": info["type"],
                    "score": round(score, 4),
                    "metadata": info.get("metadata") or {},
                    "facts": facts_map.get(eid, []),
                    "relations": relations_map.get(eid, []),
                    "knowledge": knowledge_map.get(eid, []),
                    "team_shared": info.get("team_shared", False),
                    "_graph": eid in graph_expanded_ids,
                })

            return results

    # ============================================
    # Smart Memory Triggers (v2.6)
    # ============================================

    def ensure_triggers_table(self):
        """Create memory_triggers table if not exists."""
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memory_triggers (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    trigger_type VARCHAR(30) NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT,
                    source_type VARCHAR(30),
                    source_id UUID,
                    fire_at TIMESTAMPTZ,
                    fired BOOLEAN DEFAULT FALSE,
                    fired_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_triggers_pending
                ON memory_triggers(user_id, fired, fire_at)
                WHERE fired = FALSE
            """)

    def create_trigger(self, user_id: str, trigger_type: str, title: str,
                       detail: str = None, source_type: str = None,
                       source_id: str = None, fire_at=None,
                       sub_user_id: str = "default") -> int:
        """Create a new smart trigger."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            # Avoid duplicate triggers with same title for same user
            cur.execute("""
                SELECT id FROM memory_triggers
                WHERE user_id = %s AND sub_user_id = %s AND title = %s AND fired = FALSE
            """, (user_id, sub_user_id, title))
            if cur.fetchone():
                return -1  # Already exists

            cur.execute("""
                INSERT INTO memory_triggers
                    (user_id, sub_user_id, trigger_type, title, detail, source_type, source_id, fire_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (user_id, sub_user_id, trigger_type, title, detail, source_type,
                  source_id, fire_at))
            row = cur.fetchone()
            return row["id"] if row else -1

    def get_pending_triggers(self, user_id: str = None) -> list:
        """Get triggers that are ready to fire."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            if user_id:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE user_id = %s AND fired = FALSE
                    AND (fire_at IS NULL OR fire_at <= NOW())
                    ORDER BY created_at
                    LIMIT 50
                """, (user_id,))
            else:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE fired = FALSE
                    AND (fire_at IS NULL OR fire_at <= NOW())
                    ORDER BY created_at
                    LIMIT 100
                """)
            return [dict(r) for r in cur.fetchall()]

    def fire_trigger(self, trigger_id: int):
        """Mark trigger as fired and send via webhook."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                UPDATE memory_triggers SET fired = TRUE, fired_at = NOW()
                WHERE id = %s
                RETURNING *
            """, (trigger_id,))
            trigger = cur.fetchone()
            if not trigger:
                return None

        # Fire webhook
        self.fire_webhooks(trigger["user_id"], "smart_trigger", {
            "trigger_id": trigger["id"],
            "type": trigger["trigger_type"],
            "title": trigger["title"],
            "detail": trigger["detail"],
            "source_type": trigger.get("source_type"),
        })
        return dict(trigger)

    def get_triggers(self, user_id: str, include_fired: bool = False,
                     limit: int = 50, sub_user_id: str = "default") -> list:
        """Get triggers for a user."""
        self.ensure_triggers_table()
        with self._cursor(dict_cursor=True) as cur:
            if include_fired:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE user_id = %s AND sub_user_id = %s ORDER BY created_at DESC LIMIT %s
                """, (user_id, sub_user_id, limit))
            else:
                cur.execute("""
                    SELECT * FROM memory_triggers
                    WHERE user_id = %s AND sub_user_id = %s AND fired = FALSE
                    ORDER BY fire_at ASC NULLS FIRST, created_at DESC LIMIT %s
                """, (user_id, sub_user_id, limit))
            return [dict(r) for r in cur.fetchall()]

    def detect_reminder_triggers(self, user_id: str, sub_user_id: str = "default"):
        """Scan episodic memory for upcoming events and create reminders."""
        self.ensure_triggers_table()
        import re
        from datetime import timedelta

        with self._cursor(dict_cursor=True) as cur:
            # Find recent episodes that mention future times/dates
            cur.execute("""
                SELECT id, summary, context, outcome, metadata, created_at
                FROM episodes
                WHERE user_id = %s AND sub_user_id = %s
                AND created_at > NOW() - INTERVAL '7 days'
                ORDER BY created_at DESC LIMIT 20
            """, (user_id, sub_user_id))
            episodes = cur.fetchall()

        now = datetime.datetime.now(datetime.timezone.utc)
        created = 0

        for ep in episodes:
            summary = ep["summary"] or ""
            context = ep["context"] or ""
            text = f"{summary} {context}".lower()

            # Simple heuristics for time references
            # "tomorrow", "in X hours", "at HH:MM", weekday names
            fire_at = None

            if "tomorrow" in text:
                fire_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0)
            elif "today" in text and now.hour < 20:
                fire_at = now + timedelta(hours=1)
            elif "next week" in text:
                fire_at = (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0)

            # Match "at HH:MM" patterns
            time_match = re.search(r'(?:at|в)\s+(\d{1,2})[:\.](\d{2})', text)
            if time_match and not fire_at:
                hour, minute = int(time_match.group(1)), int(time_match.group(2))
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    candidate = now.replace(hour=hour, minute=minute, second=0)
                    if candidate < now:
                        candidate += timedelta(days=1)
                    # Only set reminder 1h before the event
                    fire_at = candidate - timedelta(hours=1)
                    if fire_at < now:
                        fire_at = now + timedelta(minutes=5)

            if fire_at and fire_at > now:
                title = f"Reminder: {summary[:100]}"
                detail = f"From your conversation: {summary}"
                if ep.get("outcome"):
                    detail += f"\nOutcome: {ep['outcome']}"
                tid = self.create_trigger(
                    user_id=user_id,
                    trigger_type="reminder",
                    title=title,
                    detail=detail,
                    source_type="episode",
                    source_id=str(ep["id"]),
                    fire_at=fire_at,
                    sub_user_id=sub_user_id,
                )
                if tid > 0:
                    created += 1

        return created

    def detect_contradiction_triggers(self, user_id: str, new_facts: list,
                                       entity_name: str, sub_user_id: str = "default"):
        """Check if new facts contradict existing ones. Uses simple keyword overlap."""
        if not new_facts:
            return 0

        with self._cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT f.content, e.name FROM facts f
                JOIN entities e ON f.entity_id = e.id
                WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name = %s
                AND f.archived = FALSE
            """, (user_id, sub_user_id, entity_name))
            existing = cur.fetchall()

        if not existing:
            return 0

        created = 0
        # Simple contradiction detection via negation patterns
        negation_pairs = [
            ("likes", "dislikes"), ("loves", "hates"),
            ("vegetarian", "meat"), ("vegan", "meat"),
            ("allergic", "enjoys"), ("can't", "can"),
            ("doesn't", "does"), ("never", "always"),
            ("dislikes", "likes"), ("does not eat", "eats"),
            ("allergic to", "likes"),
        ]

        for new_fact in new_facts:
            new_lower = new_fact.lower()
            for old in existing:
                old_lower = old["content"].lower()
                # Check negation patterns
                for pos, neg in negation_pairs:
                    if (pos in new_lower and neg in old_lower) or \
                       (neg in new_lower and pos in old_lower):
                        title = f"Possible contradiction about {entity_name}"
                        detail = f"New: \"{new_fact}\"\nExisting: \"{old['content']}\""
                        tid = self.create_trigger(
                            user_id=user_id,
                            trigger_type="contradiction",
                            title=title,
                            detail=detail,
                            source_type="fact",
                            sub_user_id=sub_user_id,
                        )
                        if tid > 0:
                            created += 1
                        break  # one contradiction per new fact is enough

        return created

    def detect_pattern_triggers(self, user_id: str, sub_user_id: str = "default"):
        """Detect patterns in procedural memory (high fail rates, recurring issues)."""
        with self._cursor(dict_cursor=True) as cur:
            # Procedures with more failures than successes
            cur.execute("""
                SELECT id, name, success_count, fail_count, steps
                FROM procedures
                WHERE user_id = %s AND sub_user_id = %s AND (success_count + fail_count) >= 3
                AND fail_count > success_count
            """, (user_id, sub_user_id))
            risky_procs = cur.fetchall()

        created = 0
        for proc in risky_procs:
            total = proc["success_count"] + proc["fail_count"]
            fail_rate = round(proc["fail_count"] / total * 100)
            title = f"Risky workflow: {proc['name']} ({fail_rate}% failure rate)"
            detail = (
                f"Workflow \"{proc['name']}\" has failed {proc['fail_count']} out of "
                f"{total} times ({fail_rate}% failure rate). Consider revising the approach."
            )
            tid = self.create_trigger(
                user_id=user_id,
                trigger_type="pattern",
                title=title,
                detail=detail,
                source_type="procedure",
                source_id=str(proc["id"]),
                sub_user_id=sub_user_id,
            )
            if tid > 0:
                created += 1

        # ---- At-risk procedures: 3+ failures in last 7 days ----
        try:
            with self._cursor(dict_cursor=True) as cur:
                cur.execute("""
                    SELECT p.id, p.name, p.success_count, p.fail_count,
                           COUNT(e.id) AS recent_failures
                    FROM procedures p
                    LEFT JOIN episodes e ON e.linked_procedure_id = p.id
                        AND e.emotional_valence = 'negative'
                        AND e.created_at > NOW() - INTERVAL '7 days'
                    WHERE p.user_id = %s AND p.sub_user_id = %s AND p.is_current = TRUE
                    GROUP BY p.id
                    HAVING COUNT(e.id) >= 3
                """, (user_id, sub_user_id))
                at_risk = cur.fetchall()
            for proc in at_risk:
                title = f"At-risk workflow: {proc['name']} ({proc['recent_failures']} failures this week)"
                detail = (
                    f"Workflow \"{proc['name']}\" has failed {proc['recent_failures']} times "
                    f"in the last 7 days. It may need a manual review or restructuring."
                )
                tid = self.create_trigger(
                    user_id=user_id,
                    trigger_type="procedure_at_risk",
                    title=title,
                    detail=detail,
                    source_type="procedure",
                    source_id=str(proc["id"]),
                    sub_user_id=sub_user_id,
                )
                if tid > 0:
                    created += 1
        except Exception as e:
            logger.error(f"⚠️ At-risk trigger detection failed: {e}")

        return created

    def create_procedure_evolved_trigger(self, user_id: str,
                                          procedure_name: str,
                                          old_version: int,
                                          new_version: int,
                                          change_description: str,
                                          procedure_id: str,
                                          sub_user_id: str = "default") -> int:
        """Create a trigger notifying the user that a procedure evolved."""
        title = f"Procedure updated: {procedure_name} v{old_version} → v{new_version}"
        detail = (
            f"Your workflow \"{procedure_name}\" was automatically improved based on "
            f"your recent experience.\n"
            f"What changed: {change_description}\n"
            f"Review it in your procedures list."
        )
        tid = self.create_trigger(
            user_id=user_id,
            trigger_type="procedure_evolved",
            title=title,
            detail=detail,
            source_type="procedure",
            source_id=procedure_id,
            sub_user_id=sub_user_id,
        )
        return 1 if tid > 0 else 0

    def create_procedure_suggestion_trigger(self, user_id: str,
                                             suggestion_name: str,
                                             suggestion_steps: list[dict],
                                             episode_count: int,
                                             confidence: float,
                                             sub_user_id: str = "default") -> int:
        """Create a trigger suggesting a procedure the user might want to formalize.

        Called when pattern detection finds a cluster with confidence 0.4-0.6
        (too low to auto-create, but worth surfacing to the user).
        """
        steps_desc = " → ".join((s.get("action", "") if isinstance(s, dict) else str(s)) for s in suggestion_steps[:5])
        title = f"Workflow detected: {suggestion_name}"
        detail = (
            f"Based on {episode_count} similar episodes, you may have a repeatable workflow:\n"
            f"Steps: {steps_desc}\n"
            f"Confidence: {confidence:.0%}\n"
            f"Say 'yes' to create this as a procedure, or dismiss."
        )
        tid = self.create_trigger(
            user_id=user_id,
            trigger_type="procedure_suggestion",
            title=title,
            detail=detail,
            source_type="episode",
            sub_user_id=sub_user_id,
        )
        return 1 if tid > 0 else 0

    def process_user_triggers(self, user_id: str) -> dict:
        """Process pending triggers for a specific user only. Returns stats."""
        pending = self.get_pending_triggers(user_id=user_id)
        fired = 0
        errors = 0
        for trigger in pending:
            try:
                self.fire_trigger(trigger["id"])
                fired += 1
            except Exception as e:
                logger.error(f"⚠️ Trigger {trigger['id']} failed: {e}")
                errors += 1
        return {"processed": len(pending), "fired": fired, "errors": errors}

    def process_all_triggers(self) -> dict:
        """Process all pending triggers across all users. Returns stats."""
        pending = self.get_pending_triggers()
        fired = 0
        errors = 0
        for trigger in pending:
            try:
                self.fire_trigger(trigger["id"])
                fired += 1
            except Exception as e:
                logger.error(f"⚠️ Trigger {trigger['id']} failed: {e}")
                errors += 1
        return {"processed": len(pending), "fired": fired, "errors": errors}
