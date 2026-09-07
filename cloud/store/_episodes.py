"""Episodic memory and raw conversation chunks.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import json
import math
import time

from ._common import (  # noqa: F401
    logger,
)


class EpisodeMixin:
    """Episodic memory and raw conversation chunks."""


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
        """Save embedding for an episode. Routes to embedding/embedding_v2 by size."""
        col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        with self._cursor() as cur:
            cur.execute(
                f"""INSERT INTO episode_embeddings (episode_id, chunk_text, {col}, tsv)
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
        """Save embedding for a conversation chunk. Routes by vector size."""
        col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        with self._cursor() as cur:
            cur.execute(
                f"""INSERT INTO chunk_embeddings (chunk_id, {col}, tsv)
                    VALUES (%s, %s::vector, to_tsvector('english', %s))""",
                (chunk_id, f"[{','.join(str(x) for x in embedding)}]", chunk_text)
            )

    def search_chunks_vector(self, user_id: str, embedding: list[float],
                             query_text: str = "", top_k: int = 5,
                             min_score: float = 0.15,
                             sub_user_id: str = "default") -> list[dict]:
        """Search raw conversation chunks via hybrid vector+BM25.
        Routes to embedding (1536) or embedding_v2 (1024) by query vector size."""
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        emb_col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        if query_text:
            query_text = query_text.replace("\x00", "")
        with self._cursor(dict_cursor=True) as cur:
            # Vector search
            cur.execute(
                f"""SELECT c.id, c.content, c.created_at,
                           1 - (ce.{emb_col} <=> %s::vector) AS score
                    FROM chunk_embeddings ce
                    JOIN conversation_chunks c ON c.id = ce.chunk_id
                    WHERE c.user_id = %s AND c.sub_user_id = %s
                      AND ce.{emb_col} IS NOT NULL
                      AND 1 - (ce.{emb_col} <=> %s::vector) > %s
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
                     before: str = None, sub_user_id: str = "default",
                     offset: int = 0) -> list[dict]:
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
        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

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

    def count_episodes(self, user_id: str, after: str = None,
                       before: str = None, sub_user_id: str = "default") -> int:
        """Count non-expired episodes (pagination total for /v1/episodes)."""
        query = """SELECT COUNT(*) FROM episodes
                   WHERE user_id = %s AND sub_user_id = %s
                     AND (expires_at IS NULL OR expires_at > NOW())"""
        params = [user_id, sub_user_id]
        if after:
            query += " AND created_at >= %s"
            params.append(after)
        if before:
            query += " AND created_at <= %s"
            params.append(before)
        with self._cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()[0]

    def search_episodes_vector(self, user_id: str, embedding: list[float],
                               top_k: int = 5, after: str = None,
                               before: str = None, sub_user_id: str = "default",
                               query_text: str = "") -> list[dict]:
        """Hybrid search over episodic memory: vector + BM25 + RRF + temporal decay
        + importance weighting. Routes by query vector size: 1024 → embedding_v2,
        else embedding. Importance comes from the LLM extractor (0.0–1.0); a
        major-event episode (0.9) outranks a trivial one (0.1) by ~38% at equal
        vector similarity."""
        emb_col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        if query_text:
            query_text = query_text.replace("\x00", "")
        query = f"""
            SELECT ep.id, ep.summary, ep.context, ep.outcome, ep.participants,
                   ep.emotional_valence, ep.importance, ep.created_at,
                   ep.happened_at, ep.metadata,
                   1 - (ee.{emb_col} <=> %s::vector) AS score
            FROM episode_embeddings ee
            JOIN episodes ep ON ep.id = ee.episode_id
            WHERE ep.user_id = %s AND ep.sub_user_id = %s
              AND (ep.expires_at IS NULL OR ep.expires_at > NOW())
              AND ee.{emb_col} IS NOT NULL
              AND 1 - (ee.{emb_col} <=> %s::vector) > 0.25
        """
        params = [embedding, user_id, sub_user_id, embedding]
        if after:
            query += " AND ep.created_at >= %s"
            params.append(after)
        if before:
            query += " AND ep.created_at <= %s"
            params.append(before)
        query += f" ORDER BY ee.{emb_col} <=> %s::vector LIMIT %s"
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

            # Stage 4: Temporal decay + importance weighting + build results.
            # Importance comes from the extractor's LLM scoring (0.0–1.0, 0.5 default).
            # We have 5k+ episodes scored >= 0.7 ("major events") that previously
            # weren't being surfaced ahead of trivial episodes in retrieval.
            # imp_boost = 0.8 + 0.4 * importance → range [0.8, 1.2]:
            #   importance 0.1 ("minor") → ×0.84 (de-prioritized)
            #   importance 0.5 ("neutral") → ×1.00 (no change vs old behavior)
            #   importance 0.9 ("major milestone") → ×1.16 (boosted)
            # Gentle enough that vector relevance still dominates; meaningful
            # enough that a major event tied 0.85 ≈ wins over a trivial 0.85.
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
                importance = float(row.get("importance") or 0.5)
                imp_boost = 0.8 + 0.4 * importance
                final_score = round(rrf_scores[eid] * decay * imp_boost, 4)
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
        if query:
            query = query.replace("\x00", "")
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
