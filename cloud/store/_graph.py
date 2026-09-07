"""Knowledge graph and activity feed.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""



class GraphMixin:
    """Knowledge graph and activity feed."""


    # ---- Graph ----

    def get_graph(self, user_id: str, sub_user_id: str = "default", limit: int = 150) -> dict:
        """Get knowledge graph (nodes + edges) for visualization. Cached 30s."""
        cache_key = f"graph:{user_id}:{sub_user_id}:{limit}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            # Total node count (use base table, not the expensive VIEW).
            # Exclude system '_'-prefixed entities to match the visible graph nodes.
            cur.execute(
                """SELECT COUNT(*) FROM entities
                   WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%'""",
                (user_id, sub_user_id)
            )
            total_nodes = cur.fetchone()[0]

            # Top nodes by facts_count (most connected first) — exclude system entities
            cur.execute(
                """SELECT name, type, facts_count, knowledge_count
                   FROM entity_overview WHERE user_id = %s AND sub_user_id = %s
                     AND name NOT LIKE '\\_%%'
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
