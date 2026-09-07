"""Stats, weekly stats, cognitive profile, rules file, intelligence dashboard.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import re

from ._common import (  # noqa: F401
    logger, _normalize_fact,
)


class ProfileMixin:
    """Stats, weekly stats, cognitive profile, rules file, intelligence dashboard."""


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

    def _touch_procedures_last_used(self, ids: list) -> None:
        """Mark procedures as used — feeds recency ranking and the weekly
        'repeated mistakes prevented' stat. Best-effort, never raises."""
        if not ids:
            return
        try:
            with self._cursor() as cur:
                cur.execute(
                    "UPDATE procedures SET last_used = NOW() WHERE id = ANY(%s::uuid[])",
                    (ids,)
                )
        except Exception:
            pass

    def weekly_stats(self, user_id: str, sub_user_id: str = "default") -> dict:
        """Weekly memory report: facts/procedures learned, recalls served,
        and repeated-mistake preventions (procedures with past failures that
        were recalled this week)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT COUNT(*) FILTER (WHERE f.created_at > NOW() - INTERVAL '7 days') AS this_week,
                          COUNT(*) FILTER (WHERE f.created_at > NOW() - INTERVAL '14 days'
                                             AND f.created_at <= NOW() - INTERVAL '7 days') AS prev_week
                   FROM facts f
                   JOIN entities e ON e.id = f.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s
                     AND e.name NOT LIKE '\\_%%' AND f.archived = FALSE""",
                (user_id, sub_user_id)
            )
            f = cur.fetchone()

            cur.execute(
                """SELECT COUNT(*) AS cnt FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s
                     AND created_at > NOW() - INTERVAL '7 days'""",
                (user_id, sub_user_id)
            )
            procs_new = cur.fetchone()["cnt"]

            cur.execute(
                """SELECT name, version FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s
                     AND parent_version_id IS NOT NULL
                     AND created_at > NOW() - INTERVAL '7 days'
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, sub_user_id)
            )
            bump = cur.fetchone()

            cur.execute(
                """SELECT COUNT(*) AS cnt FROM usage_log
                   WHERE user_id = %s AND action IN ('search', 'search_all')
                     AND created_at > NOW() - INTERVAL '7 days'""",
                (user_id,)
            )
            recalls = cur.fetchone()["cnt"]

            cur.execute(
                """SELECT p.name, p.fail_count,
                          (SELECT MAX(pe.created_at) FROM procedure_evolution pe
                            WHERE pe.procedure_id IN (p.id, p.parent_version_id)) AS last_bitten
                   FROM procedures p
                   WHERE p.user_id = %s AND p.sub_user_id = %s
                     AND p.fail_count > 0 AND p.is_current = TRUE
                     AND p.last_used > NOW() - INTERVAL '7 days'
                   ORDER BY p.last_used DESC LIMIT 5""",
                (user_id, sub_user_id)
            )
            prevented = [
                {
                    "name": r["name"],
                    "fail_count": r["fail_count"],
                    "last_bitten": r["last_bitten"].date().isoformat() if r["last_bitten"] else None,
                }
                for r in cur.fetchall()
            ]

        return {
            "facts_learned": f["this_week"],
            "facts_prev_week": f["prev_week"],
            "procedures_learned": procs_new,
            "latest_version_bump": (
                {"name": bump["name"], "version": bump["version"]} if bump else None
            ),
            "recalls_served": recalls,
            "prevented": prevented,
        }

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
            facts_str = "\n".join(f"  - {_normalize_fact(f)}" for f in ent["facts"][:20])
            rels_str = ""
            if ent.get("relations"):
                rels_str = "\n  Relations: " + ", ".join(
                    f"{r.get('type', '')} → {r.get('target', '')}"
                    for r in ent["relations"][:5]
                )
            sections.append(f"{ent['entity']} [type: {ent['type']}]:\n{facts_str}{rels_str}")
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
            facts_str = "; ".join(_normalize_fact(f) for f in ent.get("facts", [])[:10])
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
        """User's vault statistics (uncached).

        Excludes system entities whose name starts with '_' (e.g. '_reflections'),
        matching the same filter used in get_all_entities so UI counters agree
        with the visible list.
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT COUNT(*) FROM entities
                   WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%'""",
                (user_id, sub_user_id)
            )
            entities = cur.fetchone()[0]

            cur.execute(
                """SELECT e.type, COUNT(*) as cnt
                   FROM entities e
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'
                   GROUP BY e.type""",
                (user_id, sub_user_id)
            )
            by_type = {r["type"]: r["cnt"] for r in cur.fetchall()}

            # Active facts use the same filter as get_all_entities_full so the
            # stats counter reconciles with what export/list actually return.
            cur.execute(
                """SELECT COUNT(*) FILTER (WHERE f.archived = FALSE
                                             AND (f.expires_at IS NULL OR f.expires_at > NOW())) AS active,
                          COUNT(*) FILTER (WHERE f.archived = TRUE) AS archived
                   FROM facts f
                   JOIN entities e ON e.id = f.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'""",
                (user_id, sub_user_id)
            )
            fact_counts = cur.fetchone()
            facts = fact_counts["active"]
            archived_facts = fact_counts["archived"]

            cur.execute(
                """SELECT COUNT(*) FROM knowledge k
                   JOIN entities e ON e.id = k.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'""",
                (user_id, sub_user_id)
            )
            knowledge = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM relations r
                   JOIN entities e ON e.id = r.source_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'""",
                (user_id, sub_user_id)
            )
            relations = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM embeddings emb
                   JOIN entities e ON e.id = emb.entity_id
                   WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'""",
                (user_id, sub_user_id)
            )
            embeddings = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM episodes
                   WHERE user_id = %s AND sub_user_id = %s
                     AND (expires_at IS NULL OR expires_at > NOW())""",
                (user_id, sub_user_id)
            )
            episodes = cur.fetchone()[0]

            cur.execute(
                """SELECT COUNT(*) FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s
                     AND is_current = TRUE
                     AND (expires_at IS NULL OR expires_at > NOW())""",
                (user_id, sub_user_id)
            )
            procedures = cur.fetchone()[0]

            return {
                "entities": entities,
                "by_type": by_type,
                "facts": facts,
                "archived_facts": archived_facts,
                "knowledge": knowledge,
                "relations": relations,
                "embeddings": embeddings,
                "episodes": episodes,
                "procedures": procedures,
            }

    def get_value_mirror(self, user_id: str) -> dict:
        """Lightweight intelligence summary for quota wall. Cached 5 minutes."""
        cache_key = f"value_mirror:{user_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            # Exclude system '_'-prefixed entities (e.g. '_reflections') so counts
            # match user-visible lists in the UI.
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM entities
                     WHERE user_id = %s AND name NOT LIKE '\\_%%') AS entities_count,
                    (SELECT COUNT(*) FROM facts f
                     JOIN entities e ON e.id = f.entity_id
                     WHERE e.user_id = %s AND e.name NOT LIKE '\\_%%'
                       AND f.archived = FALSE) AS facts_count,
                    (SELECT COUNT(*) FROM episodes
                     WHERE user_id = %s
                       AND (expires_at IS NULL OR expires_at > NOW())) AS episodes_count,
                    (SELECT COUNT(*) FROM procedures
                     WHERE user_id = %s AND is_current = TRUE
                       AND (expires_at IS NULL OR expires_at > NOW())) AS procedures_count,
                    (SELECT COUNT(*) FROM procedures
                     WHERE user_id = %s AND is_current = TRUE AND version > 1
                       AND (expires_at IS NULL OR expires_at > NOW())) AS evolved_count
            """, (user_id, user_id, user_id, user_id, user_id))
            row = cur.fetchone()

            cur.execute("""
                SELECT name, version FROM procedures
                WHERE user_id = %s AND is_current = TRUE AND version > 1
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY version DESC LIMIT 1
            """, (user_id,))
            top = cur.fetchone()

        result = {
            "facts_learned": row["facts_count"],
            "episodes_recorded": row["episodes_count"],
            "procedures_mastered": row["procedures_count"],
            "procedures_evolved": row["evolved_count"],
            "top_evolved": {"name": top["name"], "version": top["version"]} if top else None,
        }
        self.cache.set(cache_key, result, ttl=300)
        return result

    def get_intelligence_dashboard(self, user_id: str, sub_user_id: str = "default") -> dict:
        """Full intelligence dashboard data. Cached 5 minutes."""
        cache_key = f"intel_dash:{user_id}:{sub_user_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        with self._cursor(dict_cursor=True) as cur:
            # Core counts — exclude system '_'-prefixed entities (e.g. '_reflections')
            # so dashboard counters match the user-visible entity list.
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM entities
                     WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%') AS entities,
                    (SELECT COUNT(*) FROM facts f
                     JOIN entities e ON e.id = f.entity_id
                     WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'
                       AND f.archived = FALSE) AS facts,
                    (SELECT COUNT(*) FROM relations r
                     JOIN entities e ON e.id = r.source_id
                     WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%') AS relations,
                    (SELECT COUNT(*) FROM knowledge k
                     JOIN entities e ON e.id = k.entity_id
                     WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%') AS knowledge,
                    (SELECT COUNT(*) FROM episodes
                     WHERE user_id = %s AND sub_user_id = %s
                       AND (expires_at IS NULL OR expires_at > NOW())) AS episodes,
                    (SELECT COUNT(*) FROM procedures
                     WHERE user_id = %s AND sub_user_id = %s
                       AND is_current = TRUE
                       AND (expires_at IS NULL OR expires_at > NOW())) AS procedures,
                    (SELECT COUNT(*) FROM procedures
                     WHERE user_id = %s AND sub_user_id = %s
                       AND is_current = TRUE AND version > 1
                       AND (expires_at IS NULL OR expires_at > NOW())) AS evolved
            """, (user_id, sub_user_id) * 7)
            counts = cur.fetchone()

            # Entity type breakdown (exclude system '_'-prefixed entities)
            cur.execute("""
                SELECT type, COUNT(*) as cnt FROM entities
                WHERE user_id = %s AND sub_user_id = %s AND name NOT LIKE '\\_%%'
                GROUP BY type ORDER BY cnt DESC
            """, (user_id, sub_user_id))
            by_type = {r["type"]: r["cnt"] for r in cur.fetchall()}

            # Top evolved procedures (up to 5)
            cur.execute("""
                SELECT name, version FROM procedures
                WHERE user_id = %s AND sub_user_id = %s
                  AND is_current = TRUE AND version > 1
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY version DESC LIMIT 5
            """, (user_id, sub_user_id))
            evolved_procs = [{"name": r["name"], "version": r["version"]} for r in cur.fetchall()]

            # Facts added in last 7 days (exclude facts on system '_'-entities)
            cur.execute("""
                SELECT COUNT(*) FROM facts f
                JOIN entities e ON e.id = f.entity_id
                WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'
                  AND f.archived = FALSE
                  AND f.created_at >= NOW() - INTERVAL '7 days'
            """, (user_id, sub_user_id))
            facts_7d = cur.fetchone()[0]

            # Facts added in prior 7 days (for growth comparison)
            cur.execute("""
                SELECT COUNT(*) FROM facts f
                JOIN entities e ON e.id = f.entity_id
                WHERE e.user_id = %s AND e.sub_user_id = %s AND e.name NOT LIKE '\\_%%'
                  AND f.archived = FALSE
                  AND f.created_at >= NOW() - INTERVAL '14 days'
                  AND f.created_at < NOW() - INTERVAL '7 days'
            """, (user_id, sub_user_id))
            facts_prev_7d = cur.fetchone()[0]

            # Episodes in last 7 days
            cur.execute("""
                SELECT COUNT(*) FROM episodes
                WHERE user_id = %s AND sub_user_id = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND created_at >= NOW() - INTERVAL '7 days'
            """, (user_id, sub_user_id))
            episodes_7d = cur.fetchone()[0]

        result = {
            "entities": counts["entities"],
            "facts": counts["facts"],
            "relations": counts["relations"],
            "knowledge": counts["knowledge"],
            "episodes": counts["episodes"],
            "procedures": counts["procedures"],
            "evolved": counts["evolved"],
            "by_type": by_type,
            "evolved_procedures": evolved_procs,
            "facts_7d": facts_7d,
            "facts_prev_7d": facts_prev_7d,
            "episodes_7d": episodes_7d,
        }
        self.cache.set(cache_key, result, ttl=300)
        return result
