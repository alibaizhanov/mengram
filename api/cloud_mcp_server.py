"""
Mengram Cloud MCP Server — Claude Desktop with cloud-based memory.

Works via cloud API instead of local vault.
Memory accessible from any device.

Claude Desktop config:
{
  "mcpServers": {
    "mengram": {
      "command": "mengram",
      "args": ["server", "--cloud"],
      "env": {
        "MENGRAM_API_KEY": "om-...",
        "MENGRAM_URL": "https://mengram.io"
      }
    }
  }
}
"""

import sys
import os
import json
import asyncio
from urllib.parse import unquote

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, Resource, ResourceTemplate
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from cloud.client import CloudMemory


def create_cloud_mcp_server(mem: CloudMemory, user_id: str = "default") -> "Server":
    """Create MCP server backed by cloud API."""

    # Build profile from cloud (Cognitive Profile with fallback to entity listing)
    def _get_profile():
        # Try real Cognitive Profile first (LLM-generated system prompt)
        try:
            profile_data = mem.get_profile(user_id=user_id)
            system_prompt = profile_data.get("system_prompt", "")
            if system_prompt and profile_data.get("status") == "ok":
                facts_used = profile_data.get("facts_used", 0)
                return (
                    f"# Cognitive Profile\n\n"
                    f"{system_prompt}\n\n"
                    f"---\n*Based on {facts_used} facts from memory.*"
                )
        except Exception:
            pass

        # Fallback: basic entity listing
        try:
            memories = mem.get_all(user_id=user_id)
            if not memories:
                return "Memory is empty. Start conversations and use 'remember' to build knowledge."

            lines = [f"# Memory Overview\n\nVault: {len(memories)} entities"]
            by_type = {}
            for m_item in memories:
                t = m_item.get("type", "unknown")
                by_type.setdefault(t, []).append(m_item.get("name", "?"))

            for t, names in sorted(by_type.items(), key=lambda x: -len(x[1])):
                lines.append(f"- **{t}**: {', '.join(names[:15])}")

            return "\n".join(lines)
        except Exception as e:
            return f"Error loading profile: {e}"

    def _get_procedures():
        """Get active procedures formatted as markdown."""
        try:
            procs = mem.procedures(user_id=user_id, limit=20)
            if not procs:
                return "No learned procedures yet."

            evolved_count = sum(1 for p in procs if p.get("version", 1) > 1)
            lines = ["# Active Procedures\n"]
            if evolved_count:
                lines.append(f"*{evolved_count} procedure(s) have evolved through experience.*\n")

            for p in procs:
                v = p.get("version", 1)
                sc = p.get("success_count", 0)
                fc = p.get("fail_count", 0)
                total = sc + fc
                reliability = f"{int(sc / total * 100)}%" if total > 0 else "untested"
                evolved_tag = f" [evolved x{v-1}]" if v > 1 else ""

                lines.append(f"## {p['name']} (v{v}, {reliability} reliable){evolved_tag}")
                lines.append(f"ID: `{p['id']}`")
                if p.get("trigger_condition"):
                    lines.append(f"**When:** {p['trigger_condition']}")
                if total > 0:
                    lines.append(f"**Stats:** {sc} successes, {fc} failures")
                for s in p.get("steps", []):
                    lines.append(f"{s.get('step', '?')}. **{s.get('action', '')}** — {s.get('detail', '')}")
                lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Error loading procedures: {e}"

    def _get_triggers():
        """Get pending triggers formatted as markdown."""
        try:
            triggers = mem.get_triggers(include_fired=False, limit=20, user_id=user_id)
            if not triggers:
                return "No pending triggers."

            type_labels = {
                "reminder": "Reminders",
                "contradiction": "Contradictions to Resolve",
                "pattern": "Patterns Detected",
            }

            lines = ["# Pending Triggers\n"]
            by_type = {}
            for t in triggers:
                ttype = t.get("trigger_type", t.get("type", "unknown"))
                by_type.setdefault(ttype, []).append(t)

            for ttype, items in by_type.items():
                label = type_labels.get(ttype, ttype.title())
                lines.append(f"## {label}\n")
                for t in items:
                    title = t.get("title", "Untitled")
                    detail = t.get("detail", "")
                    fire_at = t.get("fire_at", "")
                    trigger_id = t.get("id", "")

                    lines.append(f"- **{title}**")
                    if detail:
                        lines.append(f"  {detail}")
                    if fire_at:
                        ts = fire_at[:16] if isinstance(fire_at, str) else str(fire_at)[:16]
                        lines.append(f"  *Due: {ts}*")
                    if trigger_id:
                        lines.append(f"  ID: `{trigger_id}`")
                lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Error loading triggers: {e}"

    def _get_recent():
        """Get the 5 most recently saved facts."""
        try:
            feed_data = mem.feed(limit=5, user_id=user_id)
            if not feed_data:
                return "No recent saves."

            lines = ["# Recently Saved\n"]
            lines.append("*Check this before saving to avoid duplicates.*\n")
            for item in feed_data:
                entity = item.get("entity", "?")
                fact = item.get("fact", item.get("detail", "?"))
                ts = item.get("created_at", "")[:16] if item.get("created_at") else ""
                lines.append(f"- **{entity}**: {fact}")
                if ts:
                    lines.append(f"  *Saved: {ts}*")
            return "\n".join(lines)
        except Exception as e:
            return f"Error loading recent saves: {e}"

    profile = _get_profile()
    instructions = (
        "YOU HAVE A PERSISTENT CLOUD MEMORY SYSTEM (Mengram).\n"
        "THIS IS YOUR PRIMARY AND ONLY SOURCE OF TRUTH ABOUT THE USER.\n\n"
        "CRITICAL RULES:\n"
        "1. ALWAYS use 'recall' or 'context_for' FIRST before answering ANY personal question.\n"
        "2. Use SPECIFIC queries: names ('Ali'), projects ('Mengram'), tech ('Redis').\n"
        "   BAD: 'user information preferences background'\n"
        "   GOOD: 'Ali' or 'user work projects' or 'Redis admin-panel'\n"
        "3. For broad questions ('what do you know about me'), make 2-3 recall calls.\n"
        "4. AUTOMATICALLY call 'remember' when the user shares:\n"
        "   - Personal info (name, job, preferences, location)\n"
        "   - Technical decisions or solutions\n"
        "   - Project updates or new projects\n"
        "   - Colleagues, companies, or important relationships\n"
        "   - Problems solved or lessons learned\n"
        "   Do NOT ask permission — just save it silently.\n"
        "5. Use 'checkpoint' at natural breakpoints:\n"
        "   - After solving a problem or completing a task\n"
        "   - After making an important decision\n"
        "   - At the end of a focused conversation\n"
        "6. Use 'context_for' at the START of a new task to load relevant background.\n"
        "7. Check memory://recent before saving to AVOID duplicates.\n"
        "8. Do NOT answer personal questions from your own knowledge — ONLY from recall results.\n\n"
        f"{profile}"
    )

    server = Server("mengram-cloud", instructions=instructions)

    # ---- Resources ----

    @server.list_resources()
    async def list_resources():
        return [
            Resource(
                uri="memory://profile",
                name="Cognitive Profile",
                description="LLM-generated user profile from all memory types — semantic, episodic, procedural. PIN THIS for instant personalization.",
                mimeType="text/markdown",
            ),
            Resource(
                uri="memory://procedures",
                name="Active Procedures",
                description="Learned workflows with steps, trigger conditions, and reliability stats.",
                mimeType="text/markdown",
            ),
            Resource(
                uri="memory://triggers",
                name="Pending Triggers",
                description="Smart triggers: reminders, contradictions, and patterns detected in memory. Surface these proactively.",
                mimeType="text/markdown",
            ),
            Resource(
                uri="memory://recent",
                name="Recently Saved",
                description="Last 5 facts saved to memory — check this before saving to avoid duplicates.",
                mimeType="text/markdown",
            ),
        ]

    @server.list_resource_templates()
    async def list_resource_templates():
        return [
            ResourceTemplate(
                uriTemplate="memory://entity/{name}",
                name="Entity Details",
                description="Full details for a specific entity — facts, relations, and knowledge with artifacts.",
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri):
        uri_str = str(uri)

        if uri_str == "memory://profile":
            return _get_profile()

        elif uri_str == "memory://procedures":
            return _get_procedures()

        elif uri_str == "memory://triggers":
            return _get_triggers()

        elif uri_str == "memory://recent":
            return _get_recent()

        elif uri_str.startswith("memory://entity/"):
            entity_name = unquote(uri_str.replace("memory://entity/", ""))
            entity = mem.get(entity_name, user_id=user_id)
            if not entity:
                return f"Entity '{entity_name}' not found in memory."

            lines = [f"# {entity.get('entity', entity_name)} ({entity.get('type', 'unknown')})\n"]

            facts = entity.get("facts", [])
            if facts:
                lines.append("## Facts")
                for f in facts:
                    lines.append(f"- {f}")

            relations = entity.get("relations", [])
            if relations:
                lines.append("\n## Relations")
                for r in relations:
                    arrow = "\u2192" if r.get("direction") == "outgoing" else "\u2190"
                    lines.append(f"- {arrow} {r.get('type', '')}: {r.get('target', '')}")

            knowledge = entity.get("knowledge", [])
            if knowledge:
                lines.append("\n## Knowledge")
                for k in knowledge:
                    lines.append(f"\n**[{k.get('type', '')}] {k.get('title', '')}**")
                    lines.append(k.get("content", ""))
                    if k.get("artifact"):
                        lines.append(f"```\n{k['artifact']}\n```")

            return "\n".join(lines)

        return f"Unknown resource: {uri_str}"

    # ---- Tools ----

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="remember",
                description="Save knowledge from a conversation to memory — pass message pairs and the AI extracts entities, facts, relations, episodes, and procedures. Use after meaningful exchanges where the user shares personal info, preferences, decisions, or technical context worth remembering.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "conversation": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["role", "content"],
                            },
                        },
                    },
                    "required": ["conversation"],
                },
            ),
            Tool(
                name="remember_text",
                description="Save plain text to memory — the AI extracts entities, facts, and relations automatically. Use when user shares a note, snippet, URL content, or any freeform text to remember.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to extract knowledge from and store in memory"},
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="recall",
                description="ALWAYS call this FIRST when user asks anything personal. Semantic search through cloud memory. Use specific keywords: person names, project names, technologies. For broad questions like 'what do you know about me', search for the user's name or 'Ali'. Multiple calls with different queries are encouraged.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Specific search query — use names, projects, technologies. NOT generic phrases."},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="search",
                description="Advanced structured search — returns entities with relevance scores, facts, and knowledge snippets. Use instead of 'recall' when you need detailed results with scores for comparison or analysis.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query — names, topics, technologies, or natural language questions"},
                        "top_k": {"type": "integer", "default": 5, "description": "Number of results to return (default 5)"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="timeline",
                description="Search memory by time. Use when user asks 'what did I do last week', 'when did I...', 'what happened in January'. Returns facts with timestamps.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "after": {"type": "string", "description": "ISO datetime — start of range (e.g. 2025-02-01T00:00:00Z)"},
                        "before": {"type": "string", "description": "ISO datetime — end of range"},
                    },
                },
            ),
            Tool(
                name="vault_stats",
                description="Get memory vault statistics — total entities, facts, relations, knowledge items, and storage usage. Use when user asks 'how much do you remember', 'memory stats', or 'how big is my vault'.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="run_agents",
                description="Run memory agents that analyze, clean, and find patterns in memory. Use 'curator' to find contradictions and stale facts, 'connector' to find hidden patterns and insights, 'digest' for weekly summary, or 'all' for everything.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "enum": ["curator", "connector", "digest", "all"], "default": "all"},
                        "auto_fix": {"type": "boolean", "default": True, "description": "Auto-archive low quality facts (curator)"},
                    },
                },
            ),
            Tool(
                name="get_insights",
                description="Get AI-generated insights about the user — patterns, connections, reflections from memory analysis. Call this when user asks 'what patterns do you see', 'what do you know about how I think', 'analyze my memory'.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="list_procedures",
                description="List learned workflows/procedures from memory. Use when user asks 'how do I usually...', 'what's my process for...', 'show my workflows'. Returns procedures with steps, success/fail counts, and version info.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Optional search query to find specific procedures"},
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            ),
            Tool(
                name="procedure_feedback",
                description="Record success or failure for a procedure. ALWAYS use this when the user reports that a workflow worked or failed. On failure with context, the system automatically evolves the procedure to a new improved version.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "procedure_id": {"type": "string", "description": "UUID of the procedure"},
                        "success": {"type": "boolean", "description": "true if it worked, false if it failed"},
                        "context": {"type": "string", "description": "What went wrong (required when success=false to trigger evolution)"},
                        "failed_at_step": {"type": "integer", "description": "Which step number failed (optional)"},
                    },
                    "required": ["procedure_id", "success"],
                },
            ),
            Tool(
                name="procedure_history",
                description="Show how a procedure evolved over time — all versions and what changed. Use when user asks 'how has my deploy process changed', 'show procedure evolution'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "procedure_id": {"type": "string", "description": "UUID of any version of the procedure"},
                    },
                    "required": ["procedure_id"],
                },
            ),
            Tool(
                name="get_entity",
                description="Get details of a specific entity — facts, relations, knowledge. Use when user asks about a specific person, project, or concept by name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Entity name to look up"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="delete_entity",
                description="Delete an entity and all its data (facts, relations, knowledge). Use when user explicitly asks to remove something from memory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Entity name to delete"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="list_episodes",
                description="List or search episodic memories (events, interactions, experiences). Use when user asks 'what happened', 'show my recent events', 'find episodes about...'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query (optional — if omitted, returns recent episodes)"},
                        "limit": {"type": "integer", "default": 20, "description": "Max results to return"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="search_all",
                description="Unified search across ALL memory types — semantic, episodic, and procedural. Best for broad queries that might match different memory types.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {"type": "integer", "default": 5, "description": "Max results per memory type"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_graph",
                description="Get the knowledge graph — all entities and their relationships. Use when user asks 'show my knowledge graph', 'how are things connected', 'map my memory'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="get_triggers",
                description="List smart triggers — reminders, contradictions detected, and patterns. Use when user asks 'any reminders?', 'what should I know?', 'pending alerts'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include_fired": {"type": "boolean", "default": False, "description": "Include already-fired triggers"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="get_feed",
                description="Get activity feed — recent memory changes and events. Use when user asks 'what's new in my memory?', 'show recent activity', 'memory changelog'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 20, "description": "Max feed items to return"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="archive_fact",
                description="Archive a specific fact on an entity — soft-delete without removing the entity. Use when a fact is outdated, wrong, or no longer relevant.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_name": {"type": "string", "description": "Entity the fact belongs to"},
                        "fact_content": {"type": "string", "description": "Exact text of the fact to archive"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                    "required": ["entity_name", "fact_content"],
                },
            ),
            Tool(
                name="merge_entities",
                description="Merge two entities into one — combines facts, relations, and knowledge. Use when there are duplicate entities (e.g. 'JS' and 'JavaScript').",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Entity to merge FROM (will be deleted)"},
                        "target": {"type": "string", "description": "Entity to merge INTO (will be kept)"},
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                    "required": ["source", "target"],
                },
            ),
            Tool(
                name="reflect",
                description="Trigger AI reflection on memories — analyzes facts to find patterns, insights, and connections. Use when user asks 'analyze my memory', 'find patterns', 'what can you learn from my data?'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="dismiss_trigger",
                description="Dismiss a smart trigger without firing its webhook. Use when user wants to ignore or snooze a trigger notification.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "trigger_id": {"type": "integer", "description": "ID of the trigger to dismiss"},
                    },
                    "required": ["trigger_id"],
                },
            ),
            Tool(
                name="fix_entity_type",
                description="Fix an entity's type classification. Use when an entity was auto-classified incorrectly (e.g. a technology labeled as 'person'). Accepts any descriptive type string.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Entity name to reclassify"},
                        "new_type": {
                            "type": "string",
                            "description": "Correct entity type (e.g. person, project, technology, company, concept, place, activity, event, book, tool, etc.)",
                        },
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                    "required": ["name", "new_type"],
                },
            ),
            Tool(
                name="list_memories",
                description="List all stored memory entities with their types and fact counts. Use when user asks 'what do you remember?', 'show all memories', or 'list everything you know'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="get_reflections",
                description="Get AI-generated reflections (insights and patterns found across memories). Optional scope filter: 'entity' (per-entity insights), 'cross' (cross-entity connections), 'temporal' (time-based patterns).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "Filter by scope",
                            "enum": ["entity", "cross", "temporal"],
                        },
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="dedup",
                description="Find and automatically merge duplicate entities. Scans all entities and merges near-duplicates (e.g. 'React' and 'React framework'). Use when user says 'clean up duplicates' or memory feels cluttered.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "Optional user ID override"},
                    },
                },
            ),
            Tool(
                name="checkpoint",
                description="Save a session checkpoint — summarize key decisions, learnings, and outcomes from this conversation. Lighter than 'remember': takes a structured summary instead of raw messages. Call at natural breakpoints: end of a task, after solving a bug, after making a decision.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Brief summary of what was accomplished or decided (1-3 sentences)"},
                        "decisions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key decisions made (e.g. 'Chose PostgreSQL over MongoDB')",
                        },
                        "learnings": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Things learned or discovered (e.g. 'pgvector requires extension install')",
                        },
                        "next_steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "What needs to happen next (saved as reminders)",
                        },
                    },
                    "required": ["summary"],
                },
            ),
            Tool(
                name="context_for",
                description="Get relevant memory context for a specific task. Returns a compact context pack: related entities, procedures, and past events. Use at the START of a new task to load relevant background. More focused than 'recall' — returns structured context, not search results.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Description of the task (e.g. 'deploy API to Railway', 'fix database connection pool issue')"},
                    },
                    "required": ["task"],
                },
            ),
            Tool(
                name="generate_rules_file",
                description="Generate a CLAUDE.md, .cursorrules, or .windsurfrules file from memory. Creates structured project rules, conventions, tech stack, workflows, and known issues — perfect for AI coding assistants. Output can be saved directly to a file.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["claude_md", "cursorrules", "windsurf"],
                            "default": "claude_md",
                            "description": "Output format: claude_md (Claude Code), cursorrules (Cursor), windsurf (Windsurf)",
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "remember":
                result = mem.add(arguments["conversation"], user_id=user_id)
                if result.get("status") == "accepted":
                    text = "✅ Accepted! Processing in background — memories will appear shortly."
                else:
                    text = (
                        f"✅ Remembered!\n"
                        f"Created: {', '.join(result.get('created', [])) or 'none'}\n"
                        f"Updated: {', '.join(result.get('updated', [])) or 'none'}\n"
                        f"Knowledge: {result.get('knowledge_count', 0)}"
                    )
                try:
                    await server.request_context.session.send_resource_updated(uri="memory://profile")
                    await server.request_context.session.send_resource_updated(uri="memory://procedures")
                except Exception:
                    pass
                return [TextContent(type="text", text=text)]

            elif name == "remember_text":
                result = mem.add([
                    {"role": "user", "content": arguments["text"]},
                ], user_id=user_id)
                if result.get("status") == "accepted":
                    text = "✅ Accepted! Processing in background."
                else:
                    text = (
                        f"✅ Remembered!\n"
                        f"Created: {', '.join(result.get('created', [])) or 'none'}\n"
                        f"Updated: {', '.join(result.get('updated', [])) or 'none'}"
                    )
                try:
                    await server.request_context.session.send_resource_updated(uri="memory://profile")
                    await server.request_context.session.send_resource_updated(uri="memory://procedures")
                except Exception:
                    pass
                return [TextContent(type="text", text=text)]

            elif name == "recall":
                results = mem.search(arguments["query"], user_id=user_id)
                if not results:
                    return [TextContent(type="text", text="Nothing found in memory.")]

                lines = []
                for r in results:
                    lines.append(f"## {r['entity']} ({r.get('type', '?')}) — score: {r.get('score', 0)}")
                    for fact in r.get("facts", []):
                        lines.append(f"- {fact}")
                    for k in r.get("knowledge", []):
                        lines.append(f"\n**[{k.get('type', '')}] {k.get('title', '')}**")
                        lines.append(k.get("content", ""))
                        if k.get("artifact"):
                            lines.append(f"```\n{k['artifact']}\n```")
                    lines.append("")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "search":
                top_k = arguments.get("top_k", 5)
                results = mem.search(arguments["query"], user_id=user_id, limit=top_k)
                return [TextContent(
                    type="text",
                    text=json.dumps(results, ensure_ascii=False, indent=2),
                )]

            elif name == "timeline":
                results = mem.timeline(
                    after=arguments.get("after"),
                    before=arguments.get("before"),
                    user_id=user_id,
                )
                if not results:
                    return [TextContent(type="text", text="No facts found in that time range.")]
                lines = []
                for entity in results:
                    lines.append(f"## {entity['entity']} ({entity['type']})")
                    for f in entity["facts"]:
                        ts = f.get("created_at", "")[:10] if f.get("created_at") else ""
                        lines.append(f"  [{ts}] {f['content']}")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "vault_stats":
                stats = mem.stats(user_id=user_id)
                return [TextContent(
                    type="text",
                    text=json.dumps(stats, ensure_ascii=False, indent=2),
                )]

            elif name == "run_agents":
                agent = arguments.get("agent", "all")
                auto_fix = arguments.get("auto_fix", True)
                result = mem.run_agents(agent=agent, auto_fix=auto_fix, user_id=user_id)
                
                lines = [f"🤖 Agent run complete ({agent})"]
                
                if agent == "all" and "agents" in result:
                    r = result["agents"]
                    # Curator
                    c = r.get("curator", {})
                    if c and c.get("health_score"):
                        lines.append(f"\n🧹 **Curator** — Health: {int(c['health_score']*100)}%")
                        if c.get("summary"): lines.append(c["summary"])
                        meta = c.get("_meta", {})
                        if meta.get("actions_taken"): lines.append(f"✅ Auto-fixed: {meta['actions_taken']} facts archived")
                    # Connector
                    cn = r.get("connector", {})
                    if cn.get("patterns"):
                        lines.append(f"\n🔗 **Connector** — {len(cn.get('connections',[]))} connections, {len(cn['patterns'])} patterns")
                        for p in cn["patterns"][:3]:
                            lines.append(f"- {p.get('pattern', '')}")
                    if cn.get("suggestions"):
                        lines.append("\n💡 **Suggestions:**")
                        for s in cn["suggestions"][:3]:
                            lines.append(f"- [{s.get('priority','?')}] {s.get('action','')}")
                    # Digest
                    d = r.get("digest", {})
                    if d.get("headline"):
                        lines.append(f"\n📰 **Digest:** {d['headline']}")
                        if d.get("recommendation"):
                            lines.append(f"💡 {d['recommendation']}")
                else:
                    lines.append(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
                
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "get_insights":
                insights = mem.insights(user_id=user_id)
                
                if not insights.get("has_insights"):
                    return [TextContent(type="text", text="No insights yet. Run agents first or add more memories.")]
                
                lines = ["🧠 **AI Insights from Memory**\n"]
                for group in insights.get("groups", []):
                    lines.append(f"### {group.get('title', '')}")
                    for item in group.get("items", []):
                        conf = int(item.get("confidence", 0) * 100)
                        lines.append(f"- **{item.get('title', '')}** ({conf}% confidence)")
                        lines.append(f"  {item.get('content', '')[:200]}")
                    lines.append("")
                
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "list_procedures":
                query = arguments.get("query")
                limit = arguments.get("limit", 10)
                procs = mem.procedures(query=query, limit=limit, user_id=user_id)
                if not procs:
                    return [TextContent(type="text", text="No learned procedures yet.")]

                lines = [f"📋 **{len(procs)} Procedure(s)**\n"]
                for p in procs:
                    v = p.get("version", 1)
                    sc = p.get("success_count", 0)
                    fc = p.get("fail_count", 0)
                    total = sc + fc
                    reliability = f"{int(sc / total * 100)}%" if total > 0 else "untested"
                    evolved_tag = f" [evolved x{v-1}]" if v > 1 else ""

                    lines.append(f"### {p['name']} (v{v}, {reliability}){evolved_tag}")
                    lines.append(f"ID: `{p['id']}`")
                    if p.get("trigger_condition"):
                        lines.append(f"When: {p['trigger_condition']}")
                    if total > 0:
                        lines.append(f"Stats: {sc} successes, {fc} failures")
                    for s in p.get("steps", []):
                        lines.append(f"  {s.get('step', '?')}. {s.get('action', '')} — {s.get('detail', '')}")
                    lines.append("")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "procedure_feedback":
                proc_id = arguments["procedure_id"]
                success = arguments["success"]
                context = arguments.get("context")
                failed_at_step = arguments.get("failed_at_step")

                result = mem.procedure_feedback(
                    proc_id, success=success,
                    context=context, failed_at_step=failed_at_step,
                    user_id=user_id)

                sc = result.get("success_count", 0)
                fc = result.get("fail_count", 0)
                total = sc + fc
                reliability = f"{int(sc / total * 100)}%" if total > 0 else "untested"

                if success:
                    text = (
                        f"✅ Recorded success for **{result.get('name', '?')}**\n"
                        f"Stats: {sc} successes, {fc} failures ({reliability} reliable)\n"
                        f"Version: v{result.get('version', 1)}"
                    )
                else:
                    evo_lines = [
                        f"❌ Recorded failure for **{result.get('name', '?')}**",
                        f"Stats: {sc} successes, {fc} failures ({reliability} reliable)",
                    ]
                    if result.get("evolution_triggered"):
                        evo_lines.append("")
                        evo_lines.append("🔄 **Evolution triggered** — analyzing failure to create improved version automatically.")
                        if context:
                            evo_lines.append(f"Failure context: {context[:200]}")
                        if failed_at_step:
                            evo_lines.append(f"Failed at step: {failed_at_step}")
                        evo_lines.append("")
                        evo_lines.append("Use `procedure_history` to see the new version once ready.")
                    text = "\n".join(evo_lines)
                return [TextContent(type="text", text=text)]

            elif name == "procedure_history":
                proc_id = arguments["procedure_id"]
                history = mem.procedure_history(proc_id, user_id=user_id)

                versions = history.get("versions", [])
                evolution = history.get("evolution_log", [])

                if not versions:
                    return [TextContent(type="text", text="Procedure not found.")]

                # Current version prominently at top
                current = next((v for v in versions if v.get("is_current")), versions[-1])
                sc = current.get("success_count", 0)
                fc = current.get("fail_count", 0)
                total = sc + fc
                reliability = f"{int(sc / total * 100)}%" if total > 0 else "untested"

                lines = [f"# {versions[0]['name']} — Evolution History ({len(versions)} versions)\n"]
                lines.append(f"## Current: v{current.get('version', 1)} ({reliability} reliable, {sc} successes, {fc} failures)")
                for s in current.get("steps", []):
                    lines.append(f"  {s.get('step', '?')}. **{s.get('action', '')}** — {s.get('detail', '')}")
                lines.append("")

                # Evolution timeline with diffs
                if evolution:
                    lines.append("---\n## Evolution Timeline\n")
                    for e in evolution:
                        v_before = e.get("version_before", "?")
                        v_after = e.get("version_after", "?")
                        change_type = e.get("change_type", "unknown")
                        date = e.get("created_at", "")[:10]
                        diff = e.get("diff", {})

                        lines.append(f"### v{v_before} → v{v_after} ({change_type}) [{date}]")
                        for item in diff.get("added", []):
                            lines.append(f"  + {item}")
                        for item in diff.get("removed", []):
                            lines.append(f"  - {item}")
                        for item in diff.get("modified", []):
                            lines.append(f"  ~ {item}")
                        lines.append("")

                # All historical versions
                if len(versions) > 1:
                    lines.append("---\n## All Versions\n")
                    for v in versions:
                        marker = " (CURRENT)" if v.get("is_current") else " (superseded)"
                        lines.append(f"**v{v.get('version', 1)}**{marker}")
                        for s in v.get("steps", []):
                            lines.append(f"  {s.get('step', '?')}. {s.get('action', '')}")
                        lines.append("")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "get_entity":
                uid = arguments.get("user_id", user_id)
                entity = mem.get(arguments["name"], user_id=uid)
                if not entity:
                    return [TextContent(type="text", text=f"Entity '{arguments['name']}' not found.")]

                lines = [f"## {entity.get('entity', arguments['name'])} ({entity.get('type', 'unknown')})\n"]

                facts = entity.get("facts", [])
                if facts:
                    lines.append("**Facts:**")
                    for f in facts:
                        lines.append(f"- {f}")

                relations = entity.get("relations", [])
                if relations:
                    lines.append("\n**Relations:**")
                    for r in relations:
                        arrow = "→" if r.get("direction") == "outgoing" else "←"
                        lines.append(f"- {arrow} {r.get('type', '')}: {r.get('target', '')}")

                knowledge = entity.get("knowledge", [])
                if knowledge:
                    lines.append("\n**Knowledge:**")
                    for k in knowledge:
                        lines.append(f"\n[{k.get('type', '')}] {k.get('title', '')}")
                        lines.append(k.get("content", ""))
                        if k.get("artifact"):
                            lines.append(f"```\n{k['artifact']}\n```")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "delete_entity":
                uid = arguments.get("user_id", user_id)
                success = mem.delete(arguments["name"], user_id=uid)
                if success:
                    text = f"🗑️ Deleted entity '{arguments['name']}' and all its data."
                    try:
                        await server.request_context.session.send_resource_updated(uri="memory://profile")
                    except Exception:
                        pass
                else:
                    text = f"❌ Could not delete '{arguments['name']}' — entity may not exist."
                return [TextContent(type="text", text=text)]

            elif name == "list_episodes":
                uid = arguments.get("user_id", user_id)
                query = arguments.get("query")
                limit = arguments.get("limit", 20)
                episodes = mem.episodes(query=query, limit=limit, user_id=uid)

                if not episodes:
                    return [TextContent(type="text", text="No episodes found.")]

                lines = [f"📖 **{len(episodes)} Episode(s)**\n"]
                for ep in episodes:
                    summary = ep.get("summary", "No summary")
                    ts = ep.get("created_at", "")[:16] if ep.get("created_at") else ""
                    category = ep.get("category", "")
                    participants = ", ".join(ep.get("participants", [])) if ep.get("participants") else ""

                    header = f"### {summary}"
                    if ts:
                        header += f" ({ts})"
                    lines.append(header)
                    if category:
                        lines.append(f"Category: {category}")
                    if participants:
                        lines.append(f"Participants: {participants}")
                    if ep.get("outcome"):
                        lines.append(f"Outcome: {ep['outcome']}")
                    if ep.get("context"):
                        lines.append(f"Context: {ep['context']}")
                    lines.append("")
                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "search_all":
                uid = arguments.get("user_id", user_id)
                limit = arguments.get("limit", 5)
                results = mem.search_all(arguments["query"], limit=limit, user_id=uid)

                lines = [f"🔍 **Unified search: '{arguments['query']}'**\n"]

                # Semantic results
                semantic = results.get("semantic", [])
                if semantic:
                    lines.append(f"### Semantic ({len(semantic)} results)")
                    for r in semantic:
                        lines.append(f"**{r.get('entity', '?')}** ({r.get('type', '?')}) — score: {r.get('score', 0)}")
                        for fact in r.get("facts", []):
                            lines.append(f"- {fact}")
                        lines.append("")

                # Episodic results
                episodic = results.get("episodic", [])
                if episodic:
                    lines.append(f"### Episodic ({len(episodic)} results)")
                    for ep in episodic:
                        lines.append(f"- {ep.get('summary', '?')}")
                    lines.append("")

                # Procedural results
                procedural = results.get("procedural", [])
                if procedural:
                    lines.append(f"### Procedural ({len(procedural)} results)")
                    for p in procedural:
                        lines.append(f"- {p.get('name', '?')} (v{p.get('version', 1)})")
                    lines.append("")

                if not semantic and not episodic and not procedural:
                    lines.append("No results found across any memory type.")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "get_graph":
                uid = arguments.get("user_id", user_id)
                graph = mem.graph(user_id=uid)

                nodes = graph.get("nodes", [])
                edges = graph.get("edges", [])

                lines = [f"🕸️ **Knowledge Graph** — {len(nodes)} entities, {len(edges)} relations\n"]

                if nodes:
                    lines.append("**Entities:**")
                    for n in nodes:
                        facts_count = n.get("facts_count", 0)
                        lines.append(f"- {n.get('name', '?')} ({n.get('type', '?')}) — {facts_count} facts")

                if edges:
                    lines.append("\n**Relations:**")
                    for e in edges:
                        lines.append(f"- {e.get('source', '?')} → {e.get('type', '?')} → {e.get('target', '?')}")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "get_triggers":
                uid = arguments.get("user_id", user_id)
                include_fired = arguments.get("include_fired", False)
                triggers = mem.get_triggers(include_fired=include_fired, user_id=uid)

                if not triggers:
                    return [TextContent(type="text", text="No triggers found.")]

                lines = [f"🔔 **{len(triggers)} Trigger(s)**\n"]
                for t in triggers:
                    ttype = t.get("trigger_type", t.get("type", "unknown"))
                    title = t.get("title", "Untitled")
                    detail = t.get("detail", "")
                    fire_at = t.get("fire_at", "")
                    trigger_id = t.get("id", "")

                    lines.append(f"### [{ttype}] {title}")
                    if detail:
                        lines.append(f"{detail}")
                    if fire_at:
                        ts = fire_at[:16] if isinstance(fire_at, str) else str(fire_at)[:16]
                        lines.append(f"Due: {ts}")
                    if trigger_id:
                        lines.append(f"ID: `{trigger_id}`")
                    lines.append("")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "get_feed":
                uid = arguments.get("user_id", user_id)
                limit = arguments.get("limit", 20)
                feed = mem.feed(limit=limit, user_id=uid)

                if not feed:
                    return [TextContent(type="text", text="No activity yet.")]

                lines = [f"📰 **Activity Feed** ({len(feed)} items)\n"]
                for item in feed:
                    action = item.get("action", "?")
                    entity = item.get("entity", "?")
                    ts = item.get("created_at", "")[:16] if item.get("created_at") else ""
                    detail = item.get("detail", "")

                    entry = f"- **{action}** {entity}"
                    if ts:
                        entry += f" ({ts})"
                    lines.append(entry)
                    if detail:
                        lines.append(f"  {detail}")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "archive_fact":
                uid = arguments.get("user_id", user_id)
                result = mem.archive_fact(
                    arguments["entity_name"],
                    arguments["fact_content"],
                    user_id=uid,
                )
                text = f"🗄️ Archived fact on '{arguments['entity_name']}': {arguments['fact_content'][:80]}"
                try:
                    await server.request_context.session.send_resource_updated(uri="memory://profile")
                except Exception:
                    pass
                return [TextContent(type="text", text=text)]

            elif name == "merge_entities":
                uid = arguments.get("user_id", user_id)
                result = mem.merge(arguments["source"], arguments["target"], user_id=uid)
                text = (
                    f"🔗 Merged '{arguments['source']}' → '{arguments['target']}'\n"
                    f"All facts, relations, and knowledge have been combined."
                )
                try:
                    await server.request_context.session.send_resource_updated(uri="memory://profile")
                except Exception:
                    pass
                return [TextContent(type="text", text=text)]

            elif name == "reflect":
                uid = arguments.get("user_id", user_id)
                result = mem.reflect(user_id=uid)

                lines = ["🧠 **Reflection triggered**\n"]
                if result.get("reflections"):
                    for r in result["reflections"]:
                        lines.append(f"### {r.get('title', 'Insight')}")
                        lines.append(r.get("content", ""))
                        if r.get("scope"):
                            lines.append(f"*Scope: {r['scope']}*")
                        lines.append("")
                elif result.get("message"):
                    lines.append(result["message"])
                else:
                    lines.append(json.dumps(result, ensure_ascii=False, indent=2)[:2000])

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "dismiss_trigger":
                trigger_id = arguments["trigger_id"]
                result = mem.dismiss_trigger(trigger_id)
                return [TextContent(type="text", text=f"✅ Trigger {trigger_id} dismissed.")]

            elif name == "fix_entity_type":
                entity_name = arguments["name"]
                new_type = arguments["new_type"]
                uid = arguments.get("user_id", user_id)
                mem.fix_entity_type(entity_name, new_type, user_id=uid)
                return [TextContent(type="text", text=f"✅ Entity **{entity_name}** type changed to **{new_type}**.")]

            elif name == "list_memories":
                uid = arguments.get("user_id", user_id)
                memories = mem.get_all(user_id=uid)

                if not memories:
                    return [TextContent(type="text", text="No memories stored yet.")]

                lines = [f"📋 **{len(memories)} memories**\n"]
                for m in memories:
                    name_str = m.get("name", m.get("entity", "?"))
                    type_str = m.get("type", "unknown")
                    facts = m.get("facts", [])
                    lines.append(f"- **{name_str}** ({type_str}) — {len(facts)} facts")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "get_reflections":
                uid = arguments.get("user_id", user_id)
                scope = arguments.get("scope")
                reflections = mem.reflections(scope=scope, user_id=uid)

                if not reflections:
                    return [TextContent(type="text", text="No reflections yet. Use the `reflect` tool first to generate insights.")]

                lines = [f"🔮 **{len(reflections)} reflections**\n"]
                for r in reflections:
                    title = r.get("title", "Insight")
                    content = r.get("content", "")
                    scope_str = r.get("scope", "")
                    lines.append(f"### {title}")
                    lines.append(content)
                    if scope_str:
                        lines.append(f"*Scope: {scope_str}*")
                    lines.append("")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "dedup":
                uid = arguments.get("user_id", user_id)
                result = mem.dedup(user_id=uid)
                merged = result.get("merged", [])
                count = result.get("count", 0)

                if count == 0:
                    return [TextContent(type="text", text="✅ No duplicates found — memory is clean.")]

                lines = [f"🔀 **Merged {count} duplicate(s)**\n"]
                for m in merged:
                    lines.append(f"- {m}")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "checkpoint":
                summary = arguments["summary"]
                decisions = arguments.get("decisions", [])
                learnings = arguments.get("learnings", [])
                next_steps = arguments.get("next_steps", [])

                parts = [f"Session checkpoint: {summary}"]
                if decisions:
                    parts.append("Decisions made: " + "; ".join(decisions))
                if learnings:
                    parts.append("Learnings: " + "; ".join(learnings))
                if next_steps:
                    parts.append("Next steps: " + "; ".join(next_steps))

                checkpoint_text = "\n".join(parts)
                result = mem.add([
                    {"role": "user", "content": checkpoint_text},
                    {"role": "assistant", "content": f"Checkpoint saved: {summary[:100]}"},
                ], user_id=user_id)

                saved_items = []
                if decisions:
                    saved_items.append(f"{len(decisions)} decisions")
                if learnings:
                    saved_items.append(f"{len(learnings)} learnings")
                if next_steps:
                    saved_items.append(f"{len(next_steps)} next steps")

                status = result.get("status", "ok")
                if status == "accepted":
                    text = (
                        f"📌 Checkpoint saved (processing in background).\n"
                        f"Contents: {', '.join(saved_items) or 'session summary'}\n"
                        f"Summary: {summary[:200]}"
                    )
                else:
                    text = (
                        f"📌 Checkpoint saved.\n"
                        f"Created: {', '.join(result.get('created', [])) or 'processing'}\n"
                        f"Contents: {', '.join(saved_items) or 'session summary'}"
                    )

                try:
                    await server.request_context.session.send_resource_updated(uri="memory://profile")
                    await server.request_context.session.send_resource_updated(uri="memory://recent")
                except Exception:
                    pass

                return [TextContent(type="text", text=text)]

            elif name == "context_for":
                task = arguments["task"]
                results = mem.search_all(task, limit=5, user_id=user_id)

                lines = [f"# Context for: {task}\n"]

                semantic = results.get("semantic", [])
                if semantic:
                    lines.append("## Relevant Knowledge\n")
                    for r in semantic[:5]:
                        lines.append(f"**{r.get('entity', '?')}** ({r.get('type', '?')})")
                        for fact in r.get("facts", [])[:5]:
                            lines.append(f"- {fact}")
                        for k in r.get("knowledge", [])[:2]:
                            lines.append(f"- [{k.get('type', '')}] {k.get('title', '')}: {k.get('content', '')[:150]}")
                        lines.append("")

                procedural = results.get("procedural", [])
                if procedural:
                    lines.append("## Relevant Procedures\n")
                    for p in procedural[:3]:
                        v = p.get("version", 1)
                        lines.append(f"**{p.get('name', '?')}** (v{v})")
                        for s in p.get("steps", []):
                            lines.append(f"  {s.get('step', '?')}. {s.get('action', '')} — {s.get('detail', '')}")
                        lines.append("")

                episodic = results.get("episodic", [])
                if episodic:
                    lines.append("## Related Past Events\n")
                    for ep in episodic[:3]:
                        outcome = f" → {ep.get('outcome', '')}" if ep.get("outcome") else ""
                        lines.append(f"- {ep.get('summary', '?')}{outcome}")
                    lines.append("")

                if not semantic and not procedural and not episodic:
                    lines.append("No relevant context found in memory for this task.")

                return [TextContent(type="text", text="\n".join(lines))]

            elif name == "generate_rules_file":
                fmt = arguments.get("format", "claude_md")
                result = mem.rules(format=fmt, user_id=user_id)

                if result.get("status") != "ok":
                    return [TextContent(type="text", text=f"Could not generate rules file: {result.get('status', 'unknown error')}. {result.get('error', '')}")]

                content = result.get("content", "")
                facts_used = result.get("facts_used", 0)
                procs_used = result.get("procedures_used", 0)
                fmt_names = {"claude_md": "CLAUDE.md", "cursorrules": ".cursorrules", "windsurf": ".windsurfrules"}

                header = (
                    f"📄 Generated **{fmt_names.get(fmt, fmt)}** from {facts_used} facts and {procs_used} procedures.\n"
                    f"Copy the content below to your project root.\n\n---\n\n"
                )
                return [TextContent(type="text", text=header + content)]

            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]

    return server


async def main():
    if not MCP_AVAILABLE:
        print("❌ MCP SDK not installed: pip install mcp", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("MENGRAM_API_KEY", "")
    base_url = os.environ.get("MENGRAM_URL", "https://mengram.io")
    user_id = os.environ.get("MENGRAM_USER_ID", "default")

    if not api_key:
        print("❌ Set MENGRAM_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    mem = CloudMemory(api_key=api_key, base_url=base_url)
    server = create_cloud_mcp_server(mem, user_id=user_id)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
