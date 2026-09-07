"""Reflection engine and insights.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
from typing import Optional

from ._common import (  # noqa: F401
    logger, _normalize_fact, _safe_parse_json,
)


class ReflectionMixin:
    """Reflection engine and insights."""


    # ---- Reflection Engine ----

    REFLECTION_PROMPT = """You are a cognitive memory system that synthesizes insights from raw facts.

ENTITIES AND FACTS:
{facts_text}

EXISTING REFLECTIONS (update if stale):
{prev_reflections}

ATTRIBUTION RULES (critical — violating these corrupts the user's memory):
- Facts listed under an entity belong to THAT entity ONLY. Never move, copy, or merge facts between entities.
- NEVER state or imply that two entities are the same person (no "X (Y)" aliasing, no "X, also known as Y")
  unless a listed fact explicitly says they are the same person.
- Do NOT attribute the user's traits, tools, or infrastructure to co-mentioned people
  (collaborators, PR reviewers, tool authors, repo owners) — and vice versa.
- An entity reflection may ONLY use facts listed under that same entity.

Generate reflections in 3 categories:

1. ENTITY REFLECTIONS — for entities with 3+ facts, write a 2-3 sentence summary.
   Focus: what/who it is, relation to the user, current status.
   Use ONLY that entity's own facts.

2. CROSS-ENTITY PATTERNS — patterns across multiple entities.
   Focus: career direction, tech preferences, behavioral patterns, relationships.
   Patterns may reference multiple entities but must keep each fact tied to its own entity.

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

    def get_users_due_for_reflection(self, max_users: int = 50,
                                     active_within_days: int = 30) -> list:
        """Bulk version of should_reflect — find (user, sub_user) pairs whose
        reflection layer is stale relative to their facts.

        Mirrors should_reflect's triggers exactly (10+ new facts, OR 24h+ since
        last reflection AND 3+ new facts), but adds an activity filter so the
        cron doesn't burn LLM calls on dormant accounts. Highest-signal users
        (most new facts) sort first so partial batches still cover the people
        with the most stale insight layers.
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """WITH last_reflection AS (
                       SELECT e.user_id, e.sub_user_id,
                              MAX(k.refreshed_at) AS last_at
                       FROM knowledge k
                       JOIN entities e ON e.id = k.entity_id
                       WHERE k.type = 'reflection'
                       GROUP BY e.user_id, e.sub_user_id
                   ),
                   fact_stats AS (
                       SELECT e.user_id, e.sub_user_id,
                              MAX(f.created_at) AS latest_fact,
                              lr.last_at AS last_reflected_at,
                              COUNT(f.id) FILTER (
                                  WHERE lr.last_at IS NULL
                                     OR f.created_at > lr.last_at
                              ) AS new_facts
                       FROM facts f
                       JOIN entities e ON e.id = f.entity_id
                       LEFT JOIN last_reflection lr
                           ON lr.user_id = e.user_id
                          AND lr.sub_user_id = e.sub_user_id
                       WHERE f.archived = FALSE
                         AND (f.expires_at IS NULL OR f.expires_at > NOW())
                       GROUP BY e.user_id, e.sub_user_id, lr.last_at
                   )
                   SELECT user_id::text AS user_id, sub_user_id,
                          new_facts, latest_fact, last_reflected_at
                   FROM fact_stats
                   WHERE latest_fact > NOW() - make_interval(days => %s)
                     AND (
                         new_facts >= 10
                         OR (
                             new_facts >= 3
                             AND (last_reflected_at IS NULL
                                  OR last_reflected_at < NOW() - INTERVAL '24 hours')
                         )
                     )
                   ORDER BY new_facts DESC
                   LIMIT %s""",
                (active_within_days, max_users)
            )
            return [dict(r) for r in cur.fetchall()]

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
            facts_str = ", ".join(_normalize_fact(f) for f in e["facts"][:15])  # cap at 15 per entity
            facts_lines.append(f"- {e['entity']} [type: {e['type']}]: {facts_str}")
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
                    """SELECT k.id, k.title, k.content, k.scope, k.confidence, k.refreshed_at,
                              e.name as entity_name
                       FROM knowledge k
                       JOIN entities e ON e.id = k.entity_id
                       WHERE k.user_id = %s AND e.sub_user_id = %s AND k.scope = %s AND k.type = 'reflection'
                       ORDER BY k.confidence DESC, k.refreshed_at DESC""",
                    (user_id, sub_user_id, scope)
                )
            else:
                cur.execute(
                    """SELECT k.id, k.title, k.content, k.scope, k.confidence, k.refreshed_at,
                              e.name as entity_name
                       FROM knowledge k
                       JOIN entities e ON e.id = k.entity_id
                       WHERE k.user_id = %s AND e.sub_user_id = %s AND k.type = 'reflection'
                       ORDER BY k.scope, k.confidence DESC, k.refreshed_at DESC""",
                    (user_id, sub_user_id)
                )
            return [{
                "id": str(r["id"]),
                "title": r["title"],
                "content": r["content"],
                "scope": r["scope"],
                "confidence": float(r["confidence"] or 0.8),
                "entity": r["entity_name"],
                "refreshed_at": r["refreshed_at"].isoformat() if r["refreshed_at"] else None,
            } for r in cur.fetchall()]

    def delete_reflection(self, user_id: str, reflection_id: str,
                          sub_user_id: str = "default") -> bool:
        """Delete a single reflection by id (issue #54 follow-up — polluted
        reflections couldn't be removed individually). Scoped to the owning
        user and sub_user; only rows with type='reflection' are deletable
        through this path."""
        with self._cursor() as cur:
            cur.execute(
                """DELETE FROM knowledge k
                   USING entities e
                   WHERE k.entity_id = e.id
                     AND k.id = %s AND k.user_id = %s
                     AND e.sub_user_id = %s AND k.type = 'reflection'
                   RETURNING k.id""",
                (reflection_id, user_id, sub_user_id)
            )
            deleted = cur.fetchone() is not None
        if deleted:
            for sc in ("entity", "cross", "temporal", "all"):
                self.cache.invalidate(f"reflections:{user_id}:{sub_user_id}:{sc}")
        return deleted

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
