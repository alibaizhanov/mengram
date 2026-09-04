"""MCP over stdio for a memory folder — no account, no server, no network.

The same four tools the Claude connector exposes (remember, recall,
context_for, list_procedures) plus procedure_feedback, because outcomes are
the point of the format and an agent should be able to record one without
leaving its tool call. `handle_tool` is a plain function so the surface can
be tested without a transport; `create_server` wraps it for MCP.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from local.config import describe_model, llm_client
from local.store import LocalStore

TOOLS = [
    {
        "name": "remember",
        "description": ("Save what matters from a conversation into the memory folder: facts about "
                        "people, projects and tools, events with their outcome, and workflows. Needs "
                        "the folder's configured model; says so if there is none."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to remember (or use 'conversation')"},
                "conversation": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "role": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["role", "content"]},
                    "description": "Messages to extract from",
                },
            },
        },
    },
    {
        "name": "recall",
        "description": ("What the memory folder knows about a topic: facts, past events with their "
                        "outcome, and learned workflows with their track record. Word overlap, no model."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "context_for",
        "description": ("Context pack for a task: who the user is (profile) plus whatever in memory "
                        "bears on the task. Call once at the start of a piece of work."),
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "What you are about to do"}},
            "required": ["task"],
        },
    },
    {
        "name": "list_procedures",
        "description": ("Learned workflows with their record: version, success/fail counts, a "
                        "reliability reading (untested / N% expected / N% reliable), per-step counts, "
                        "preconditions and the last failure. Read the record before following one."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Filter by words (optional)"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "procedure_feedback",
        "description": ("Record that a workflow was run and whether it worked. On failure, give the "
                        "step that broke and what happened; with a model configured the workflow is "
                        "revised (or the revision quarantined if it would break another workflow)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "success": {"type": "boolean"},
                "failed_at_step": {"type": "integer", "description": "1-based step that failed"},
                "context": {"type": "string", "description": "What happened (failures only)"},
            },
            "required": ["name", "success"],
        },
    },
]


def _procedures_markdown(procs: list) -> str:
    if not procs:
        return "No learned workflows yet."
    lines = ["# Learned workflows", ""]
    for p in procs:
        lines.append(f"## {p['name']} (v{p['version']} · {p['reliability']}, "
                     f"{p['success_count']}✓/{p['fail_count']}✗)")
        if p.get("trigger_condition"):
            lines.append(f"When: {p['trigger_condition']}")
        for pre in p.get("preconditions") or []:
            lines.append(f"- precondition: {pre}")
        for i, s in enumerate(p["steps"], 1):
            rec = f" ({s['success_count']}✓/{s['fail_count']}✗)" if "success_count" in s else ""
            lines.append(f"{i}. {s['action']}" + (f" — {s['detail']}" if s.get("detail") else "") + rec)
        if p.get("last_failure"):
            when = f"{p['last_failed']}: " if p.get("last_failed") else ""
            lines.append(f"Last failure: {when}{p['last_failure']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def handle_tool(store: LocalStore, name: str, arguments: dict | None) -> str:
    """One tool call → one text result. Never raises for a bad argument."""
    args = arguments or {}
    if name == "remember":
        conversation = args.get("conversation") or args.get("text") or ""
        if not conversation:
            return "Nothing to remember: pass 'text' or 'conversation'."
        client = llm_client(store.root)
        if client is None:
            return ("No model configured for this folder, so nothing was extracted. Set one: "
                    "mengram local init --provider anthropic|openai|ollama (or export ANTHROPIC_API_KEY / OPENAI_API_KEY).")
        stats = store.add(conversation, client)
        procs = stats["procedures"]
        return (f"Remembered: entities +{stats['entities_created']} (updated {stats['entities_updated']}), "
                f"facts +{stats['facts_added']}, episodes +{stats['episodes_saved']}, workflows created "
                f"{procs['created']} / refreshed {procs['refreshed']} / kept {procs['kept']}.")

    if name == "recall":
        query = (args.get("query") or "").strip()
        if not query:
            return "Nothing to recall: pass 'query'."
        return store.recall(query, limit=int(args.get("limit") or 5)) or "Nothing relevant in memory."

    if name == "context_for":
        task = (args.get("task") or "").strip()
        parts = []
        profile = store.profile()
        if profile:
            parts.append("# Who this is for\n\n" + profile)
        relevant = store.recall(task, limit=5) if task else ""
        if relevant:
            parts.append("# Relevant memory\n\n" + relevant)
        return "\n\n".join(parts) or "Memory is empty."

    if name == "list_procedures":
        query = (args.get("query") or "").strip() or None
        return _procedures_markdown(store.procedures(query=query, limit=int(args.get("limit") or 10)))

    if name == "procedure_feedback":
        pname = (args.get("name") or "").strip()
        if not pname or "success" not in args:
            return "Pass 'name' and 'success'."
        success = bool(args["success"])
        step = args.get("failed_at_step")
        step = int(step) if step not in (None, "") else None
        context = (args.get("context") or "").strip() or None
        result = store.procedure_feedback(pname, success=success, failed_at_step=step,
                                          reason=context if not success else None)
        if "error" in result:
            return f"{result['error']}: {pname}"
        line = (f"Recorded: {result['name']} v{result['version']} is now "
                f"{result['success_count']}✓/{result['fail_count']}✗ ({result['reliability']}).")
        if success or not context:
            return line
        client = llm_client(store.root)
        if client is None:
            return line + " No model configured, so the workflow was not revised."
        from local.evolve import evolve_on_failure
        r = evolve_on_failure(store, pname, context, client, failed_at_step=step)
        status = r.get("status")
        if status == "promoted":
            return line + (f" Revised to v{r['version']} ({r['reliability']}); violated assumption: "
                           f"{r.get('violated_assumption')}.")
        if status == "revised_in_place":
            return line + f" Revised in place (it had never run); violated assumption: {r.get('violated_assumption')}."
        if status == "quarantined":
            names = ", ".join(x["dependent_name"] for x in r["regressions"])
            return line + f" The revision was QUARANTINED: it would break {names}. Review: {r['quarantine']}."
        if status == "no_change":
            return line + f" No revision: {r.get('reason')}."
        return line + f" Revision failed: {r.get('error')}."

    return f"Unknown tool: {name}"


def create_server(root: str | Path):
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    root = Path(root)
    store = LocalStore(root)
    instructions = (
        "Mengram, local mode: the user's memory is a folder of Markdown they own, at "
        f"{root.resolve()} ({store.stats()['entities']} entities, "
        f"{store.stats()['procedures']} workflows; model: {describe_model(root)}).\n"
        "Call recall (or context_for) before answering anything about the user's work. "
        "Before following a learned workflow, read its record with list_procedures; after "
        "running one, call procedure_feedback so the record moves. Call remember after a "
        "conversation that taught you something durable."
    )
    server = Server("mengram-local", instructions=instructions)

    @server.list_tools()
    async def list_tools():
        return [Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"]) for t in TOOLS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None):
        # Re-read the folder on every call: another process (a hook, the
        # user's editor) may have changed it since the server started.
        live = LocalStore(root)
        try:
            text = handle_tool(live, name, arguments)
        except Exception as e:  # a folder problem must reach the agent as text, not as a dead server
            text = f"Error: {e}"
        return [TextContent(type="text", text=text)]

    return server


async def main(root: str | Path):
    from mcp.server.stdio import stdio_server
    server = create_server(root)
    print(f"mengram local MCP server on {Path(root).resolve()}", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "memory"))
