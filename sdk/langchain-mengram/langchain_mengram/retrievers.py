"""Mengram retriever for LangChain."""

from __future__ import annotations

import logging
from typing import List, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field, model_validator

logger = logging.getLogger(__name__)


class MengramRetriever(BaseRetriever):
    """Retriever that searches across Mengram's three memory types.

    Returns Documents from semantic, episodic, and procedural memory
    with metadata indicating the source memory type and relevance score.

    Setup:
        Install ``langchain-mengram`` and set your API key:

        .. code-block:: bash

            pip install langchain-mengram
            export MENGRAM_API_KEY="om-..."

    Instantiate:
        .. code-block:: python

            from langchain_mengram import MengramRetriever

            retriever = MengramRetriever(
                api_key="om-...",
                user_id="user-123",
                top_k=5,
            )

    Usage:
        .. code-block:: python

            docs = retriever.invoke("deployment issues")
            for doc in docs:
                print(doc.metadata["memory_type"], doc.page_content)
    """

    api_key: str = Field(description="Mengram API key (starts with 'om-').")
    user_id: str = Field(default="default", description="User ID to search memories for.")
    api_url: str = Field(
        default="https://mengram.io",
        description="Mengram API base URL.",
    )
    top_k: int = Field(
        default=5,
        description="Maximum number of results per memory type.",
    )
    memory_types: List[str] = Field(
        default=["semantic", "episodic", "procedural"],
        description="Which memory types to search. Options: semantic, episodic, procedural.",
    )

    _client: object = None

    class Config:
        arbitrary_types_allowed = True

    @model_validator(mode="after")
    def _init_client(self) -> "MengramRetriever":
        from cloud.client import CloudMemory

        self._client = CloudMemory(api_key=self.api_key, base_url=self.api_url)
        return self

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """Search Mengram and return LangChain Documents."""
        docs: List[Document] = []

        try:
            results = self._client.search_all(
                query, limit=self.top_k, user_id=self.user_id
            )
        except Exception as e:
            logger.warning("Mengram search failed: %s", e)
            return docs

        if "semantic" in self.memory_types:
            for r in results.get("semantic", []):
                facts = r.get("facts", [])
                knowledge = r.get("knowledge", [])

                content_parts = []
                if facts:
                    content_parts.append(
                        f"{r.get('entity', 'Unknown')}: {'; '.join(facts)}"
                    )
                for k in knowledge:
                    content_parts.append(
                        f"[{k.get('type', '')}] {k.get('title', '')}: "
                        f"{k.get('content', '')}"
                    )

                if content_parts:
                    docs.append(
                        Document(
                            page_content="\n".join(content_parts),
                            metadata={
                                "memory_type": "semantic",
                                "entity": r.get("entity", ""),
                                "entity_type": r.get("type", ""),
                                "score": r.get("score", 0),
                                "source": "mengram",
                            },
                        )
                    )

        if "episodic" in self.memory_types:
            for ep in results.get("episodic", []):
                content = f"Event: {ep.get('summary', '')}"
                if ep.get("context"):
                    content += f"\nDetails: {ep['context']}"
                if ep.get("outcome"):
                    content += f"\nOutcome: {ep['outcome']}"

                docs.append(
                    Document(
                        page_content=content,
                        metadata={
                            "memory_type": "episodic",
                            "participants": ep.get("participants", []),
                            "emotional_valence": ep.get(
                                "emotional_valence", "neutral"
                            ),
                            "importance": ep.get("importance", 0.5),
                            "score": ep.get("score", 0),
                            "source": "mengram",
                        },
                    )
                )

        if "procedural" in self.memory_types:
            for pr in results.get("procedural", []):
                steps_text = "\n".join(
                    f"  {s.get('step', i + 1)}. {s.get('action', '')} "
                    f"— {s.get('detail', '')}"
                    for i, s in enumerate(pr.get("steps", []))
                )
                content = f"Procedure: {pr.get('name', '')}"
                if pr.get("trigger_condition"):
                    content += f"\nWhen: {pr['trigger_condition']}"
                if steps_text:
                    content += f"\nSteps:\n{steps_text}"

                docs.append(
                    Document(
                        page_content=content,
                        metadata={
                            "memory_type": "procedural",
                            "procedure_name": pr.get("name", ""),
                            "success_count": pr.get("success_count", 0),
                            "fail_count": pr.get("fail_count", 0),
                            "score": pr.get("score", 0),
                            "source": "mengram",
                        },
                    )
                )

        docs.sort(key=lambda d: d.metadata.get("score", 0), reverse=True)
        return docs
