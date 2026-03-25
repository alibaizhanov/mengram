"""
Mengram memory backend for CrewAI — drop-in replacement for built-in Memory.

Usage:

    from integrations.crewai_memory import MengramMemory

    crew = Crew(
        agents=[agent],
        tasks=[task],
        memory=MengramMemory(api_key="om-..."),
    )
    crew.kickoff()

Agents automatically get recall() and remember() tools.
Mengram handles extraction (entities, episodes, procedures) and search
with Graph RAG — no local LLM or embedder needed.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("mengram.crewai")


@dataclass
class MengramRecord:
    """Minimal MemoryRecord duck-type for CrewAI compatibility."""

    id: str = ""
    content: str = ""
    scope: str = "/"
    categories: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())


@dataclass
class MengramMatch:
    """Minimal MemoryMatch duck-type for CrewAI compatibility."""

    record: MengramRecord
    score: float = 1.0

    def format(self) -> str:
        return self.record.content


class MengramMemory:
    """
    Mengram memory backend for CrewAI.

    Pass as `memory=` to any Crew. Agents get recall/remember tools
    that use Mengram's cloud API for persistent, cross-session memory
    with semantic, episodic, and procedural memory types.

    Args:
        api_key: Mengram API key (om-...). Falls back to MENGRAM_API_KEY env var.
        base_url: API base URL (default: https://mengram.io).
        agent_id: Scope memories to a specific agent.
        user_id: User identifier for multi-tenant setups.
        read_only: If True, agents can only recall, not remember.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://mengram.io",
        agent_id: str | None = None,
        user_id: str = "default",
        read_only: bool = False,
    ):
        from cloud.client import CloudMemory

        key = api_key or os.environ.get("MENGRAM_API_KEY")
        if not key:
            raise ValueError(
                "API key required. Pass api_key= or set MENGRAM_API_KEY env var. "
                "Get your key at https://mengram.io"
            )

        self._client = CloudMemory(api_key=key, base_url=base_url)
        self._agent_id = agent_id
        self._user_id = user_id
        self._pending_jobs: list[str] = []
        self.read_only = read_only

    def recall(
        self,
        query: str,
        *,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[MengramMatch]:
        """Search all 3 memory types and return formatted results."""
        try:
            data = self._client.search_all(
                query,
                limit=min(limit, 20),
                user_id=self._user_id,
            )
        except Exception as e:
            logger.warning("mengram recall failed: %s", e)
            return []

        matches: list[MengramMatch] = []

        # Semantic — facts about entities
        for r in data.get("semantic", []):
            entity = r.get("entity", "")
            facts = r.get("facts", [])
            if not facts:
                continue
            lines = [f"{entity}: {f}" for f in facts[:5]]
            content = "KNOWN FACTS:\n" + "\n".join(f"- {l}" for l in lines)
            matches.append(
                MengramMatch(
                    record=MengramRecord(
                        id=f"sem-{entity}",
                        content=content,
                    ),
                    score=r.get("score", 0.5),
                )
            )

        # Episodic — past events
        for ep in data.get("episodic", [])[:5]:
            summary = ep.get("summary", "")
            if not summary:
                continue
            line = summary
            if ep.get("outcome"):
                line += f" -> {ep['outcome']}"
            if ep.get("created_at"):
                line += f" ({ep['created_at'][:10]})"
            matches.append(
                MengramMatch(
                    record=MengramRecord(
                        id=ep.get("id", str(uuid.uuid4())),
                        content=f"PAST EVENT: {line}",
                    ),
                    score=ep.get("score", 0.4),
                )
            )

        # Procedural — known workflows
        for pr in data.get("procedural", [])[:3]:
            name = pr.get("name", "")
            steps = pr.get("steps", [])
            if not name:
                continue
            steps_str = " -> ".join(s.get("action", "") for s in steps[:8])
            success = pr.get("success_count", 0)
            fail = pr.get("fail_count", 0)
            content = (
                f"KNOWN WORKFLOW: {name}\n"
                f"Steps: {steps_str}\n"
                f"Track record: {success} successes, {fail} failures"
            )
            matches.append(
                MengramMatch(
                    record=MengramRecord(
                        id=pr.get("id", str(uuid.uuid4())),
                        content=content,
                    ),
                    score=pr.get("score", 0.3),
                )
            )

        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:limit]

    def remember(self, content: str, **kwargs: Any) -> MengramRecord:
        """Save text to Mengram. Extraction happens server-side."""
        record = MengramRecord(content=content)
        try:
            result = self._client.add_text(
                content,
                user_id=self._user_id,
                agent_id=self._agent_id,
            )
            job_id = result.get("job_id")
            if job_id:
                self._pending_jobs.append(job_id)
            logger.debug("mengram remember: job_id=%s", job_id)
        except Exception as e:
            logger.warning("mengram remember failed: %s", e)
        return record

    def remember_many(self, contents: list[str], **kwargs: Any) -> list[MengramRecord]:
        """Save multiple texts to Mengram."""
        return [self.remember(c, **kwargs) for c in contents]

    def drain_writes(self) -> None:
        """Wait for pending background jobs to complete."""
        if not self._pending_jobs:
            return
        for job_id in self._pending_jobs:
            try:
                self._client.wait_for_job(job_id, poll_interval=1.0, max_wait=30.0)
            except Exception as e:
                logger.debug("drain_writes: job %s: %s", job_id, e)
        self._pending_jobs.clear()
