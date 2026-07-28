"""Mengram memory tools for CrewAI agents.

Usage:
    from mengram_crewai import mengram_tools

    tools = mengram_tools(api_key="om-...", user_id="user-123")
    agent = Agent(role="...", goal="...", tools=tools)

Gives agents three memory abilities: semantic search over facts/events,
saving durable facts, and retrieving learned workflows (procedures) with
success/failure track records and preconditions.
"""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field
from crewai.tools import BaseTool

__version__ = "0.2.0"


def _make_client(api_key: str | None, base_url: str | None):
    """Resolve a CloudMemory client from the installed mengram-ai package.

    Explicit api_key wins; otherwise MENGRAM_API_KEY is read at call time.
    """
    try:
        from mengram import Mengram
    except ImportError as e:
        raise ImportError(
            "mengram-crewai requires the mengram-ai package: pip install mengram-ai"
        ) from e
    key = api_key or os.getenv("MENGRAM_API_KEY")
    if not key:
        raise EnvironmentError(
            "MENGRAM_API_KEY environment variable or api_key= is required"
        )
    if base_url:
        return Mengram(api_key=key, base_url=base_url)
    return Mengram(api_key=key)


class _SearchInput(BaseModel):
    query: str = Field(description="What to look for, e.g. 'database preferences' or 'last deploy incident'")


class _SaveInput(BaseModel):
    content: str = Field(description="The durable fact, preference, event, or decision to remember")


class _ProceduresInput(BaseModel):
    task: str = Field(description="The task at hand, e.g. 'deploy to production'")


class MengramSearchTool(BaseTool):
    name: str = "search_memory"
    description: str = (
        "Search the user's long-term memory for facts, preferences, past events, and "
        "decisions. Use before answering anything that may depend on who the user is "
        "or what happened before."
    )
    args_schema: type[BaseModel] = _SearchInput
    api_key: str | None = None
    user_id: str = "default"
    base_url: str | None = None
    limit: int = 5

    def _run(self, query: str) -> str:
        m = _make_client(self.api_key, self.base_url)
        # Unified search across all three memory types, not facts-only.
        res = m.search_all(query, user_id=self.user_id, limit=self.limit)
        out = []
        for r in res.get("semantic", []):
            facts = "; ".join(r.get("facts", [])[:5])
            out.append(f"fact — {r.get('entity', '?')} ({r.get('type', '?')}): {facts}")
        for e in res.get("episodic", []):
            out.append(f"event — {e.get('summary', '')}")
        for p in res.get("procedural", []):
            out.append(f"procedure — {p.get('name', '?')} (v{p.get('version', 1)})")
        return "\n".join(out) if out else "No memories found for this query."


class MengramSaveTool(BaseTool):
    name: str = "save_memory"
    description: str = (
        "Save an important new fact, preference, event, or decision to the user's "
        "long-term memory so future sessions remember it. Use sparingly for durable "
        "information, not small talk."
    )
    args_schema: type[BaseModel] = _SaveInput
    api_key: str | None = None
    user_id: str = "default"
    base_url: str | None = None

    def _run(self, content: str) -> str:
        m = _make_client(self.api_key, self.base_url)
        res = m.add_text(content, user_id=self.user_id, source="crewai")
        return f"Saved (status: {res.get('status', 'ok')})."


class MengramProceduresTool(BaseTool):
    name: str = "get_procedures"
    description: str = (
        "Retrieve the user's learned workflows (procedures) relevant to a task — "
        "step-by-step playbooks that evolved from past successes and failures, with "
        "preconditions to verify. Use before performing a multi-step task the user "
        "has likely done before (deploys, releases, setups)."
    )
    args_schema: type[BaseModel] = _ProceduresInput
    api_key: str | None = None
    user_id: str = "default"
    base_url: str | None = None
    limit: int = 5

    def _run(self, task: str) -> str:
        m = _make_client(self.api_key, self.base_url)
        procedures = m.procedures(query=task, limit=self.limit, user_id=self.user_id)
        if not procedures:
            return "No learned procedures for this task yet."
        out = []
        for p in procedures:
            steps = "; ".join(
                f"{s.get('step', '?')}. {s.get('action', '')}" for s in p.get("steps", [])
            )
            preconditions = (
                (p.get("metadata") or {}).get("preconditions")
                or p.get("preconditions")
                or []
            )
            pre = ("; verify first: " + json.dumps(preconditions)) if preconditions else ""
            out.append(
                f"{p.get('name', '?')} (v{p.get('version', 1)}, "
                f"{p.get('success_count', 0)} successes / {p.get('fail_count', 0)} failures): "
                f"{steps}{pre}"
            )
        return "\n".join(out)


def mengram_tools(
    api_key: str | None = None,
    user_id: str = "default",
    base_url: str | None = None,
    limit: int = 5,
) -> list[BaseTool]:
    """All three Mengram tools, ready to pass to a CrewAI Agent(tools=...)."""
    common: dict[str, Any] = dict(api_key=api_key, user_id=user_id, base_url=base_url)
    return [
        MengramSearchTool(limit=limit, **common),
        MengramSaveTool(**common),
        MengramProceduresTool(limit=limit, **common),
    ]
