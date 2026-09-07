"""Teams and shared entities.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import json
import secrets

from ._common import (  # noqa: F401
    psycopg2,
)


class TeamMixin:
    """Teams and shared entities."""


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
        emb_col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        if query_text:
            query_text = query_text.replace("\x00", "")

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
                       1 - (emb.{emb_col} <=> %s::vector) AS score,
                       e.updated_at, e.metadata
                   FROM embeddings emb
                   JOIN entities e ON e.id = emb.entity_id
                   WHERE ((e.user_id = %s AND e.sub_user_id = %s) OR e.team_id = ANY(%s))
                     AND emb.{emb_col} IS NOT NULL
                     AND 1 - (emb.{emb_col} <=> %s::vector) > %s
                     AND LEFT(e.name, 1) != '_'
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
                             AND LEFT(e.name, 1) != '_'
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

            # Sort, filter by minimum RRF score, and limit.
            # Threshold 0.025 — see search_vector above for the full reasoning
            # on noise (0.0164/0.0213) vs real-hit (≥ 0.0328) separation.
            DIRECT_MATCH_FLOOR = 0.01
            sorted_final = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            top_score = sorted_final[0][1] if sorted_final else 0
            min_rrf_graph = max(DIRECT_MATCH_FLOOR, top_score * 0.4)
            sorted_results = [(eid, score) for eid, score in sorted_final
                              if (eid in graph_expanded_ids and score >= min_rrf_graph) or
                                 (eid not in graph_expanded_ids and score >= DIRECT_MATCH_FLOOR)][:top_k]

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
                    f"""SELECT entity_id, chunk_text,
                               1 - ({emb_col} <=> %s::vector) AS relevance
                        FROM embeddings
                        WHERE entity_id = ANY(%s::uuid[])
                          AND {emb_col} IS NOT NULL""",
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
