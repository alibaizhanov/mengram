"""Curator, connector and digest agents.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import json
import re

from ._common import (  # noqa: F401
    logger, _normalize_fact, _normalize_step, _safe_parse_json,
)


class AgentMixin:
    """Curator, connector and digest agents."""


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
            facts_str = ", ".join(_normalize_fact(f) for f in e["facts"][:15])  # max 15 facts per entity
            facts_lines.append(f"- {e['entity']} [type: {e['type']}]: {facts_str}")
        facts_text = "\n".join(facts_lines)
        # Hard cap on text size (~8K chars ≈ 2K tokens)
        if len(facts_text) > 8000:
            facts_text = facts_text[:8000] + "\n... (truncated)"

        # Fetch procedures for dedup analysis
        procedures = self.get_procedures(user_id, limit=50, sub_user_id=sub_user_id)
        proc_lines = []
        for p in procedures:
            steps_str = " → ".join(_normalize_step(s) for s in p["steps"][:10]) if p["steps"] else "(no steps)"
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
        facts_text = " ".join(_normalize_fact(f) for f in facts).lower() if facts else ""
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
                facts_str = "; ".join(_normalize_fact(f) for f in facts[:5] if f)
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
            facts_str = ", ".join(_normalize_fact(f) for f in e["facts"][:15])
            facts_lines.append(f"- {e['entity']} [type: {e['type']}]: {facts_str}")
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
