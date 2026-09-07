"""Hybrid vector + BM25 search, MMR, graph expansion, contradiction archive.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import json
import math
import time

from ._common import (  # noqa: F401
    logger, _safe_parse_json,
)


class SearchMixin:
    """Hybrid vector + BM25 search, MMR, graph expansion, contradiction archive."""


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

        Routes to embedding (1536-dim, OpenAI) or embedding_v2 (1024-dim,
        Cohere multilingual) based on the input vector size.
        """
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        emb_col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        # Postgres rejects NUL (0x00) bytes in TEXT params; strip them from caller input
        # (paste-from-PDF and some buggy SDK clients sneak them in).
        if query_text:
            query_text = query_text.replace("\x00", "")

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
                       1 - (emb.{emb_col} <=> %s::vector) AS score,
                       e.updated_at, e.metadata
                   FROM embeddings emb
                   JOIN entities e ON e.id = emb.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s
                     AND emb.{emb_col} IS NOT NULL
                     AND 1 - (emb.{emb_col} <=> %s::vector) > %s
                     AND LEFT(e.name, 1) != '_'
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
                             AND LEFT(e.name, 1) != '_'
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

            # Sort, filter by minimum RRF score, and diversify via MMR.
            # RRF score is 1/(60+rank): rank-1 single-index ≈ 0.0164. Real hits
            # land in BOTH the vector and BM25 indices, summing to ≈ 0.0328 at
            # rank-1, ≈ 0.043 with recency boost. Arithmetic noise (single-index
            # rank-1 with or without recency) clusters at 0.0164/0.0213.
            #
            # Threshold history:
            #   0.01  (original) — admitted everything, 63.8% of searches were
            #                       single-value noise like 0.0165 / 0.0213.
            #   0.15  (regression, 2026-06-04 → 06-06) — killed 99.3% of real
            #                       matches because 0.15 is unreachable for raw
            #                       RRF (max ~0.05). mnmilford (mean 0.97 pre)
            #                       flipped to critical with mean 0.0.
            #   0.025 (current) — cleanly separates noise (0.0164/0.0213) from
            #                       real two-index hits (≥ 0.0328) while still
            #                       admitting legitimate single-index strong
            #                       recency-boosted results.
            DIRECT_MATCH_FLOOR = 0.01
            sorted_final = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
            top_score = sorted_final[0][1] if sorted_final else 0
            min_rrf_graph = max(DIRECT_MATCH_FLOOR, top_score * 0.4)
            filtered = [(eid, score) for eid, score in sorted_final
                        if (eid in graph_expanded_ids and score >= min_rrf_graph) or
                           (eid not in graph_expanded_ids and score >= DIRECT_MATCH_FLOOR)]
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
                   WHERE e.user_id = %s AND e.sub_user_id = %s
                     AND LEFT(e.name, 1) != '_'
                     AND (
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

        # Ask LLM which old facts are DIRECTLY contradicted by new ones.
        # Dedup of similar-but-not-contradicting facts is handled separately in dedup_entity_facts.
        prompt = f"""You decide which EXISTING facts are DIRECTLY CONTRADICTED by NEW facts.

EXISTING facts:
{json.dumps(old_facts, ensure_ascii=False)}

NEW facts:
{json.dumps(new_facts, ensure_ascii=False)}

Rules:
- Flag an EXISTING fact ONLY if a NEW fact directly contradicts it
  (e.g. "lives in Almaty" vs "relocated to Dubai", "uses Python" vs "switched to Rust").
- DO NOT flag facts that are merely similar, overlapping, or about the same topic.
- DO NOT flag a detailed fact because a vaguer new fact covers the same subject.
- DO NOT flag duplicates or redundant facts — that is handled elsewhere.
- If unsure, keep the existing fact.

For each real contradiction return the EXACT old string (from EXISTING) and the EXACT new string (from NEW) that replaces it.

Return ONLY JSON:
{{"pairs": [{{"old": "<exact string from EXISTING>", "new": "<exact string from NEW>"}}]}}
or {{"pairs": []}} if no real contradictions.
No markdown, no explanation."""

        try:
            pairs = None
            for attempt in range(2):
                response = llm_client.complete(prompt, response_format={"type": "json_object"})
                result = _safe_parse_json(response)
                if isinstance(result, dict) and "pairs" in result and isinstance(result["pairs"], list):
                    pairs = result["pairs"]
                    break
                # Backward-compat fallback: older prompt returned {"remove": [...]}.
                # Treat as a list of olds with no explicit new; we will skip them below
                # because guards require an explicit new replacement.
                if isinstance(result, dict) and isinstance(result.get("remove"), list):
                    pairs = [{"old": o, "new": None} for o in result["remove"]]
                    break
            if not isinstance(pairs, list):
                return []
        except Exception as e:
            logger.error(f"⚠️ Conflict resolution failed: {e}")
            return []

        # Archive contradicted facts with explicit old->new mapping + guards
        archived = []
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            old_fact = pair.get("old")
            new_fact = pair.get("new")

            # Guard 1: must be an exact string from old_facts (no LLM paraphrase)
            if not isinstance(old_fact, str) or old_fact not in old_facts:
                logger.warning(f"⚠️ Supersede skipped: old_fact not in EXISTING: {str(old_fact)[:80]!r}")
                continue
            # Guard 2: must have an explicit new replacement that exists in new_facts
            if not isinstance(new_fact, str) or new_fact not in new_facts:
                logger.warning(f"⚠️ Supersede skipped: missing/invalid new_fact for old={old_fact[:80]!r}")
                continue
            # Guard 3: reject identical old==new (LLM hallucination / no-op)
            if old_fact.strip().lower() == new_fact.strip().lower():
                logger.warning(f"⚠️ Supersede skipped: identical old==new: {old_fact[:80]!r}")
                continue
            # Guard 4: reject severe truncation (new < 30% of old length) — prevents data loss.
            # 0.3 catches gross info loss (e.g. 326ch→46ch, 14%) while still allowing legitimate
            # concise updates (e.g. "lives at <full address>" → "relocated to Dubai").
            if len(new_fact) < len(old_fact) * 0.3:
                logger.warning(
                    f"⚠️ Supersede skipped: truncation (old={len(old_fact)}ch, new={len(new_fact)}ch): "
                    f"old={old_fact[:80]!r} new={new_fact[:80]!r}"
                )
                continue

            with self._cursor() as cur:
                cur.execute(
                    """UPDATE facts SET archived = TRUE, superseded_by = %s
                       WHERE entity_id = %s AND content = %s AND archived = FALSE AND (expires_at IS NULL OR expires_at > NOW())""",
                    (new_fact, entity_id, old_fact)
                )
            archived.append(old_fact)
            logger.info(f"📦 Archived: '{old_fact}' → superseded by '{new_fact}'")

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
