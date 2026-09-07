"""Entities, facts, relations, identity, dedup, deletion.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import json
import re
import threading
from typing import Optional

from ._common import (  # noqa: F401
    psycopg2, logger, ENTITY_TYPES, CloudEntity,
)


class EntityMixin:
    """Entities, facts, relations, identity, dedup, deletion."""


    # ---- Entities ----

    def _find_primary_person(self, user_id: str, sub_user_id: str = "default") -> Optional[tuple]:
        """Find the primary person entity for this user.
        Prefers: explicitly pinned identity > most facts > full name (has space) > most recent.
        Fact count ranks above full name on purpose: a third-party with a fuller
        name (PR reviewer, tool author) must not beat the user's own entity —
        that misroutes every "User" fact onto the wrong person (issue #54)."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT e.id, e.name, COUNT(f.id) as fact_count,
                          CASE WHEN e.name LIKE '%% %%' THEN 1 ELSE 0 END as has_full_name,
                          CASE WHEN e.metadata->>'is_user_identity' = 'true' THEN 1 ELSE 0 END as pinned
                   FROM entities e
                   LEFT JOIN facts f ON f.entity_id = e.id AND f.archived = FALSE AND (f.expires_at IS NULL OR f.expires_at > NOW())
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.type = 'person' AND LOWER(e.name) != 'user'
                   GROUP BY e.id, e.name
                   ORDER BY pinned DESC, fact_count DESC, has_full_name DESC, e.updated_at DESC
                   LIMIT 1""",
                (user_id, sub_user_id)
            )
            row = cur.fetchone()
            if row:
                return (str(row[0]), row[1])
            return None

    def set_user_identity(self, user_id: str, entity_name: str, sub_user_id: str = "default") -> Optional[dict]:
        """Pin an entity as the user's own identity. _find_primary_person honors
        this flag above all heuristics, so extraction context, "User" merging and
        profile generation anchor to the right person (issue #54)."""
        entity_id = self.get_entity_id(user_id, entity_name, sub_user_id=sub_user_id)
        if not entity_id:
            return None
        with self._cursor() as cur:
            cur.execute(
                """UPDATE entities SET metadata = metadata - 'is_user_identity'
                   WHERE user_id = %s AND sub_user_id = %s
                     AND metadata->>'is_user_identity' = 'true' AND id != %s""",
                (user_id, sub_user_id, entity_id)
            )
            cur.execute(
                """UPDATE entities
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"is_user_identity": true}'::jsonb,
                       updated_at = NOW()
                   WHERE id = %s
                   RETURNING name""",
                (entity_id,)
            )
            canonical_name = cur.fetchone()[0]
        return {"entity_id": entity_id, "entity": canonical_name}

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

            # Move relations — update source_id references (skip self-relations)
            cur.execute(
                """UPDATE relations SET source_id = %s
                   WHERE source_id = %s
                   AND target_id != %s
                   AND NOT EXISTS (
                       SELECT 1 FROM relations r2
                       WHERE r2.source_id = %s AND r2.target_id = relations.target_id AND r2.type = relations.type
                   )""",
                (target_id, source_id, target_id, target_id)
            )
            cur.execute(
                """UPDATE relations SET target_id = %s
                   WHERE target_id = %s
                   AND source_id != %s
                   AND NOT EXISTS (
                       SELECT 1 FROM relations r2
                       WHERE r2.source_id = relations.source_id AND r2.target_id = %s AND r2.type = relations.type
                   )""",
                (target_id, source_id, target_id, target_id)
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
        # Strip "(type)" suffixes that LLM sometimes copies from context
        # e.g. "cyberfips (person) (person)" → "cyberfips"
        changed = True
        while changed:
            changed = False
            for t in ENTITY_TYPES:
                suffix = f" ({t})"
                if name.lower().endswith(suffix):
                    name = name[:len(name) - len(suffix)]
                    changed = True

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
                # A re-asserted fact is live again: the UNIQUE(entity_id, content)
                # index also covers archived rows, so without the reset a fact
                # that was superseded and later becomes true again stays
                # archived while RETURNING reports success. Safe against the
                # same add's contradiction pass — that runs before this upsert
                # and never archives a fact identical to an incoming one.
                if expires_at:
                    cur.execute(
                        """INSERT INTO facts (entity_id, content, importance, expires_at, event_date, metadata)
                           VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (entity_id, content) DO UPDATE SET
                               event_date = COALESCE(EXCLUDED.event_date, facts.event_date),
                               metadata = facts.metadata || EXCLUDED.metadata,
                               archived = FALSE,
                               superseded_by = NULL
                           RETURNING content""",
                        (entity_id, fact, importance, expires_at, event_date, meta_json)
                    )
                else:
                    cur.execute(
                        """INSERT INTO facts (entity_id, content, importance, event_date, metadata)
                           VALUES (%s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (entity_id, content) DO UPDATE SET
                               event_date = COALESCE(EXCLUDED.event_date, facts.event_date),
                               metadata = facts.metadata || EXCLUDED.metadata,
                               archived = FALSE,
                               superseded_by = NULL
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

            # The id rides along so callers can round-trip or re-query without
            # a second lookup by name — the export needs it in frontmatter.
            for eid, ent in entity_map.items():
                ent["id"] = eid
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
                    return (f'The user\'s name is probably "{primary_name}". Use this name instead of "User" '
                            f'ONLY if the conversation does not indicate the user is someone else — '
                            f'if it does, keep "User".')
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
                lines.append(f'The user\'s name is probably "{primary_name}". Use "{primary_name}" instead of "User" '
                             f'ONLY if the conversation does not indicate the user is someone else — '
                             f'if it does, keep "User".')

            for e in entities:
                eid = str(e["id"])
                name = e["name"]
                # Skip "User" and "_reflections" from context
                if name.lower() in ("user", "_reflections"):
                    continue
                facts = facts_by_entity.get(eid, [])
                if facts:
                    fact_strs = ", ".join(f[0] for f in facts)
                    lines.append(f"- {name} [type: {e['type']}]: {fact_strs}")
                else:
                    lines.append(f"- {name} [type: {e['type']}]")

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
        """Delete entity and all related data. Children are deleted explicitly —
        production tables lack the ON DELETE CASCADE that schema.sql declares
        (verified live during #39 e2e), so relying on cascades silently orphans
        facts/knowledge/embeddings/relations."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id FROM entities WHERE user_id = %s AND sub_user_id = %s AND name = %s",
                (user_id, sub_user_id, name)
            )
            row = cur.fetchone()
            if not row:
                return False
            eid = str(row[0])
            for child in ("facts", "knowledge", "embeddings"):
                cur.execute(f"DELETE FROM {child} WHERE entity_id = %s", (eid,))
            cur.execute("DELETE FROM relations WHERE source_id = %s OR target_id = %s", (eid, eid))
            cur.execute("DELETE FROM entities WHERE id = %s", (eid,))
        self.cache.invalidate(f"stats:{user_id}")
        self._schedule_matview_refresh()
        return True

    def _existing_tables(self, cur, names: tuple) -> set:
        """The subset of `names` that exists in this database.

        Several tables are created lazily on first use, so an install that has
        never sent a drip email or run an agent genuinely does not have them.
        Deleting from a missing one raises — and because connections run in
        autocommit, every delete before that point has already landed, leaving
        the data half-erased while the caller sees a 500.
        """
        cur.execute(
            "SELECT n FROM unnest(%s::text[]) AS t(n) "
            "WHERE to_regclass('public.' || t.n) IS NOT NULL",
            (list(names),)
        )
        return {r[0] for r in cur.fetchall()}

    def delete_all_memories(self, user_id: str, sub_user_id: str = "default") -> dict:
        """Erase everything remembered for one sub_user. Returns per-table counts.

        Everything the user can read back has to go. Deleting only entities
        left /v1/episodes and /v1/procedures still answering, and the verbatim
        conversation text still sitting in conversation_chunks, after the
        account had been told its memories were deleted.

        Children before parents throughout: production tables predate the
        ON DELETE CASCADE that schema.sql declares, so nothing is cascaded for
        us. `entities.user_id` is a uuid while the other roots store it as
        text, hence the two forms of the scope predicate.
        """
        counts = {}
        scope = (user_id, sub_user_id)

        with self._cursor() as cur:
            present = self._existing_tables(cur, (
                "facts", "knowledge", "embeddings", "relations", "entities",
                "episodes", "episode_embeddings",
                "procedures", "procedure_embeddings", "procedure_evolution",
                "conversation_chunks", "chunk_embeddings", "memory_triggers",
            ))

            def roots(table, uuid_owner=False):
                if table not in present:
                    return []
                owner = "user_id" if uuid_owner else "user_id::text"
                cur.execute(
                    f"SELECT id FROM {table} WHERE {owner} = %s AND sub_user_id = %s", scope)
                return [str(r[0]) for r in cur.fetchall()]

            def wipe(table, predicate, params):
                if table not in present:
                    return
                cur.execute(f"DELETE FROM {table} WHERE {predicate}", params)
                counts[table] = counts.get(table, 0) + cur.rowcount

            entity_ids = roots("entities", uuid_owner=True)
            episode_ids = roots("episodes")
            procedure_ids = roots("procedures")
            chunk_ids = roots("conversation_chunks")

            # Grandchildren: vectors and evolution history keyed off the roots.
            if episode_ids:
                wipe("episode_embeddings", "episode_id = ANY(%s::uuid[])", (episode_ids,))
                wipe("procedure_evolution", "episode_id = ANY(%s::uuid[])", (episode_ids,))
            if procedure_ids:
                wipe("procedure_embeddings", "procedure_id = ANY(%s::uuid[])", (procedure_ids,))
                wipe("procedure_evolution", "procedure_id = ANY(%s::uuid[])", (procedure_ids,))
            if chunk_ids:
                wipe("chunk_embeddings", "chunk_id = ANY(%s::uuid[])", (chunk_ids,))
            if entity_ids:
                for child in ("facts", "knowledge", "embeddings"):
                    wipe(child, "entity_id = ANY(%s::uuid[])", (entity_ids,))
                wipe("relations",
                     "source_id = ANY(%s::uuid[]) OR target_id = ANY(%s::uuid[])",
                     (entity_ids, entity_ids))

            # Roots.
            if entity_ids:
                wipe("entities", "id = ANY(%s::uuid[])", (entity_ids,))
            if episode_ids:
                wipe("episodes", "id = ANY(%s::uuid[])", (episode_ids,))
            if procedure_ids:
                wipe("procedures", "id = ANY(%s::uuid[])", (procedure_ids,))
            if chunk_ids:
                wipe("conversation_chunks", "id = ANY(%s::uuid[])", (chunk_ids,))
            wipe("memory_triggers", "user_id::text = %s AND sub_user_id = %s", scope)

        self.cache.invalidate(f"stats:{user_id}")
        self.cache.invalidate(f"graph:{user_id}:{sub_user_id}:150")
        self.cache.invalidate(f"profile:{user_id}")
        self._schedule_matview_refresh()
        return counts

    def delete_account(self, user_id: str) -> dict:
        """Permanently delete a user account and ALL associated data across
        every sub_user (issue #39). Irreversible.

        Every table is wiped explicitly (children before parents) in one
        transaction — no reliance on ON DELETE CASCADE, because production
        tables created by older schema versions lack those constraints.
        Returns per-table deleted counts."""
        email = self.get_user_email(user_id)
        counts = {}
        with self._cursor() as cur:
            # Collect API key hashes first — their 60s auth-cache entries must
            # be invalidated after deletion, or dead keys 500 (FK-less lazy
            # subscription insert) instead of 401 until the cache expires.
            cur.execute("SELECT key_hash FROM api_keys WHERE user_id::text = %s", (user_id,))
            key_hashes = [r[0] for r in cur.fetchall()]

            # EVERYTHING is deleted explicitly, children before parents.
            # Never rely on ON DELETE CASCADE here: schema.sql declares it,
            # but production tables created by older schemas/migrations lack
            # it (verified live during #39 e2e — entities/api_keys/usage_log
            # survived a users-row delete).

            # entities' children
            cur.execute("SELECT id FROM entities WHERE user_id = %s", (user_id,))
            entity_ids = [str(r[0]) for r in cur.fetchall()]
            if entity_ids:
                for child in ("facts", "knowledge", "embeddings"):
                    cur.execute(f"DELETE FROM {child} WHERE entity_id = ANY(%s::uuid[])", (entity_ids,))
                    counts[child] = cur.rowcount
                cur.execute(
                    "DELETE FROM relations WHERE source_id = ANY(%s::uuid[]) OR target_id = ANY(%s::uuid[])",
                    (entity_ids, entity_ids))
                counts["relations"] = cur.rowcount

            # episodes' / procedures' / chunks' children
            cur.execute("DELETE FROM episode_embeddings WHERE episode_id IN (SELECT id FROM episodes WHERE user_id = %s)", (user_id,))
            counts["episode_embeddings"] = cur.rowcount
            cur.execute("DELETE FROM procedure_embeddings WHERE procedure_id IN (SELECT id FROM procedures WHERE user_id = %s)", (user_id,))
            counts["procedure_embeddings"] = cur.rowcount
            cur.execute("DELETE FROM procedure_evolution WHERE procedure_id IN (SELECT id FROM procedures WHERE user_id = %s)", (user_id,))
            counts["procedure_evolution"] = cur.rowcount
            cur.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM conversation_chunks WHERE user_id = %s)", (user_id,))
            counts["chunk_embeddings"] = cur.rowcount

            # all user-keyed tables
            user_tables = [
                "entities", "episodes", "procedures", "conversation_chunks",
                "memory_triggers", "jobs", "memory_health", "drip_emails",
                "checkout_sessions", "webhooks", "agent_runs", "oauth_codes",
                "team_members", "api_keys", "usage_log", "subscriptions",
                "usage_counters",
            ]
            # Four of these are created lazily on first use, so an install
            # where nobody ever opened a checkout or ran an agent does not have
            # them. Skipping the absent ones keeps deletion from aborting
            # part-way and leaving a half-erased account behind.
            present = self._existing_tables(cur, tuple(user_tables) + ("teams", "email_codes"))
            for table in user_tables:
                if table not in present:
                    continue
                cur.execute(f"DELETE FROM {table} WHERE user_id::text = %s", (user_id,))  # noqa: S608 — fixed list above
                counts[table] = cur.rowcount
            if "teams" in present:
                cur.execute("DELETE FROM teams WHERE created_by = %s", (user_id,))
                counts["teams"] = cur.rowcount
            if email:
                if "email_codes" in present:
                    cur.execute("DELETE FROM email_codes WHERE email = %s", (email,))
                    counts["email_codes"] = cur.rowcount
                # drip_emails rows can predate signup (user_id NULL) — clear by email too
                if "drip_emails" in present:
                    cur.execute("DELETE FROM drip_emails WHERE email = %s", (email,))
                    counts["drip_emails"] = counts.get("drip_emails", 0) + cur.rowcount
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            counts["users"] = cur.rowcount
        for prefix in ("stats:", "profile:", "rules:", "graph:", "value_mirror:", "sub:"):
            self.cache.invalidate(f"{prefix}{user_id}")
        for kh in key_hashes:
            self.cache.invalidate(f"auth:{kh[:16]}")
        self._schedule_matview_refresh()
        return counts
