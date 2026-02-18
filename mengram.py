"""
Mengram SDK — Mem0-compatible API with Knowledge Graph.

Usage:
    from mengram import Memory

    m = Memory(vault_path="./vault", llm_provider="anthropic", api_key="sk-ant-...")

    # Remember
    m.add("I work at Uzum Bank, backend on Spring Boot", user_id="ali")

    # Search
    results = m.search("where does ali work?", user_id="ali")

    # Everything we know
    all_memories = m.get_all(user_id="ali")

    # Delete
    m.delete("PostgreSQL")

    # Stats
    m.stats()

Differences from Mem0:
    - Data in .md files (can open in Obsidian)
    - Knowledge Graph with typed relations
    - Fully local
"""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from engine.brain import MengramBrain
from engine.extractor.llm_client import (
    LLMClient,
    AnthropicClient,
    OpenAIClient,
    OllamaClient,
    create_llm_client,
)
from engine.extractor.conversation_extractor import (
    ConversationExtractor,
    ExtractionResult,
    MockLLMClient,
)
from engine.vault_manager.vault_manager import VaultManager


@dataclass
class MemoryItem:
    """Single memory item (entity + facts)"""
    id: str
    name: str
    entity_type: str
    facts: list[str]
    relations: list[dict]
    source_file: str
    metadata: dict = field(default_factory=dict)

    def __repr__(self):
        return f"Memory({self.entity_type}: {self.name}, facts={len(self.facts)})"


@dataclass
class SearchResult:
    """Search result"""
    memory: MemoryItem
    score: float = 1.0
    context: str = ""

    def __repr__(self):
        return f"SearchResult({self.memory.name}, score={self.score:.2f})"


class Memory:
    """
    Mengram — Mem0-compatible API with Knowledge Graph.

    Each user_id gets its own vault (subfolder).
    Inside vault — .md files with entities, facts, [[links]].
    """

    def __init__(
        self,
        vault_path: str = "./vault",
        llm_provider: str = "anthropic",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
    ):
        self.base_vault_path = Path(vault_path)
        self.base_vault_path.mkdir(parents=True, exist_ok=True)

        # LLM client
        self.llm = self._create_llm(llm_provider, api_key, model, ollama_url)
        self.extractor = ConversationExtractor(self.llm)

        # Brain cache by user_id
        self._brains: dict[str, MengramBrain] = {}

    def _create_llm(
        self, provider: str, api_key: Optional[str],
        model: Optional[str], ollama_url: str
    ) -> LLMClient:
        """Creates LLM client"""
        if provider == "anthropic":
            key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
            return AnthropicClient(api_key=key, model=model or "claude-sonnet-4-20250514")
        elif provider == "openai":
            key = api_key or os.getenv("OPENAI_API_KEY", "")
            return OpenAIClient(api_key=key, model=model or "gpt-4o-mini")
        elif provider == "ollama":
            return OllamaClient(base_url=ollama_url, model=model or "llama3.2")
        elif provider == "mock":
            return MockLLMClient()
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _get_brain(self, user_id: str = "default") -> MengramBrain:
        """Gets brain for specific user_id"""
        if user_id not in self._brains:
            user_vault = str(self.base_vault_path / user_id)
            self._brains[user_id] = MengramBrain(
                vault_path=user_vault,
                llm_client=self.llm,
            )
        return self._brains[user_id]

    # ==========================================
    # Core methods (Mem0-compatible)
    # ==========================================

    def add(
        self,
        messages: str | list[dict],
        user_id: str = "default",
    ) -> dict:
        """
        Add memory from text or conversation.

        Args:
            messages: Text or [{"role": "user", "content": "..."}]
            user_id: User ID

        Returns:
            {"entities_created": [...], "entities_updated": [...]}

        Examples:
            m.add("I work at Uzum Bank", user_id="ali")
            m.add([
                {"role": "user", "content": "We use PostgreSQL 15"},
                {"role": "assistant", "content": "Good choice!"},
            ], user_id="ali")
        """
        brain = self._get_brain(user_id)

        if isinstance(messages, str):
            return brain.remember_text(messages)
        else:
            return brain.remember(messages)

    def search(
        self,
        query: str,
        user_id: str = "default",
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        Semantic search across memory (vector + graph).

        Args:
            query: Query (searches by meaning, not just keywords)
            user_id: User ID
            top_k: Maximum results

        Returns:
            [SearchResult(memory=..., score=..., context=...)]

        Examples:
            results = m.search("database issues", user_id="ali")
            for r in results:
                print(f"{r.memory.name} (score={r.score:.2f})")
                print(r.memory.facts)
        """
        brain = self._get_brain(user_id)

        # Use semantic search from brain
        raw_results = brain.search(query, top_k=top_k)
        context = brain.recall(query, top_k=top_k)

        results = []
        for data in raw_results:
            vault_path = str(self.base_vault_path / user_id)
            source_file = str(Path(vault_path) / f"{data['entity']}.md")

            memory = MemoryItem(
                id=data["entity"].lower().replace(" ", "_"),
                name=data["entity"],
                entity_type=data.get("type", "unknown"),
                facts=data.get("facts", []),
                relations=data.get("relations", []),
                source_file=source_file if Path(source_file).exists() else "",
                metadata={},
            )
            results.append(SearchResult(
                memory=memory,
                score=data.get("score", 0.5),
                context=context,
            ))

        return results

    def get_all(self, user_id: str = "default") -> list[MemoryItem]:
        """
        Get all memories for a user.

        Returns:
            [MemoryItem(...), ...]
        """
        brain = self._get_brain(user_id)
        graph = brain.graph
        entities = graph.all_entities()

        results = []
        for entity in entities:
            if entity.entity_type == "tag":
                continue

            facts = self._extract_facts_from_file(entity.source_file)
            neighbors = graph.get_neighbors(entity.id, depth=1)
            relations = [
                {
                    "type": n["relation_type"],
                    "target": n["entity"].name,
                    "target_type": n["entity"].entity_type,
                }
                for n in neighbors
                if n["entity"].entity_type != "tag"
            ]

            results.append(MemoryItem(
                id=entity.id,
                name=entity.name,
                entity_type=entity.entity_type,
                facts=facts,
                relations=relations,
                source_file=entity.source_file or "",
                metadata=entity.metadata or {},
            ))

        return results

    def get(self, entity_name: str, user_id: str = "default") -> Optional[MemoryItem]:
        """
        Get a specific entity by name.

        Examples:
            pg = m.get("PostgreSQL", user_id="ali")
            print(pg.facts, pg.relations)
        """
        brain = self._get_brain(user_id)
        entity = brain.graph.find_entity(entity_name)
        if not entity:
            return None

        facts = self._extract_facts_from_file(entity.source_file)
        neighbors = brain.graph.get_neighbors(entity.id, depth=1)
        relations = [
            {
                "type": n["relation_type"],
                "target": n["entity"].name,
                "target_type": n["entity"].entity_type,
            }
            for n in neighbors
            if n["entity"].entity_type != "tag"
        ]

        return MemoryItem(
            id=entity.id,
            name=entity.name,
            entity_type=entity.entity_type,
            facts=facts,
            relations=relations,
            source_file=entity.source_file or "",
            metadata=entity.metadata or {},
        )

    def delete(self, entity_name: str, user_id: str = "default") -> bool:
        """
        Delete entity from vault (removes .md file).

        Returns:
            True if deleted, False if not found
        """
        brain = self._get_brain(user_id)
        vault = brain.vault_manager
        file_path = vault._entity_file_path(entity_name)

        if file_path.exists():
            file_path.unlink()
            brain._graph = None  # Invalidate cache
            return True
        return False

    def stats(self, user_id: str = "default") -> dict:
        """Vault statistics"""
        brain = self._get_brain(user_id)
        return brain.get_stats()

    def graph(self, entity_name: str, user_id: str = "default", depth: int = 2) -> dict:
        """
        Get subgraph around entity.

        Returns:
            {"center": Entity, "nodes": [...], "edges": [...]}
        """
        brain = self._get_brain(user_id)
        entity = brain.graph.find_entity(entity_name)
        if not entity:
            return {"center": None, "nodes": [], "edges": []}
        return brain.graph.get_subgraph(entity.id, depth=depth)

    def _extract_facts_from_file(self, file_path: Optional[str]) -> list[str]:
        """Extract facts from .md file"""
        if not file_path:
            return []
        path = Path(file_path)
        if not path.exists():
            return []

        content = path.read_text(encoding="utf-8")
        facts = []
        in_facts_section = False

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("## Facts") or line.startswith("## Updates"):
                in_facts_section = True
                continue
            if line.startswith("## ") and in_facts_section:
                in_facts_section = False
                continue
            if in_facts_section and line.startswith("- "):
                # Remove [[links]] for cleanliness
                import re
                clean = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line[2:])
                if clean and not clean.startswith("*"):  # Skip dates
                    facts.append(clean)

        return facts


# ==========================================
# Convenience initialization function
# ==========================================

def init(
    vault_path: str = "./vault",
    provider: str = "anthropic",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Memory:
    """
    Quick initialization.

    Examples:
        import mengram
        m = mengram.init(provider="anthropic", api_key="sk-ant-...")
        m.add("I love Python", user_id="ali")
    """
    return Memory(
        vault_path=vault_path,
        llm_provider=provider,
        api_key=api_key,
        model=model,
    )


if __name__ == "__main__":
    print("=" * 60)
    print("🧠 Mengram SDK — Demo")
    print("=" * 60)

    # Mock LLM for testing
    m = Memory(vault_path="./demo_sdk_vault", llm_provider="mock")

    # 1. Add
    print("\n📝 m.add('I work at Uzum Bank...')")
    result = m.add(
        "I work at Uzum Bank, backend developer on Spring Boot. "
        "Problem with PostgreSQL connection pool in Project Alpha.",
        user_id="ali",
    )
    print(f"   Created: {result['entities_created']}")
    print(f"   Updated: {result['entities_updated']}")

    # 2. Get all
    print(f"\n📋 m.get_all():")
    all_memories = m.get_all(user_id="ali")
    for mem in all_memories:
        print(f"   {mem}")
        for fact in mem.facts:
            print(f"      • {fact}")

    # 3. Get specific
    print(f"\n🔍 m.get('PostgreSQL'):")
    pg = m.get("PostgreSQL", user_id="ali")
    if pg:
        print(f"   {pg}")
        print(f"   Facts: {pg.facts}")
        print(f"   Relations: {pg.relations}")

    # 4. Search
    print(f"\n🔍 m.search('database'):")
    results = m.search("database", user_id="ali")
    for r in results:
        print(f"   {r}")

    # 5. Stats
    print(f"\n📊 m.stats():")
    print(f"   {m.stats(user_id='ali')}")

    # 6. Graph
    print(f"\n🕸️ m.graph('User'):")
    g = m.graph("User", user_id="ali")
    print(f"   Nodes: {len(g.get('nodes', []))}")
    print(f"   Edges: {len(g.get('edges', []))}")

    # Comparison with Mem0
    print(f"\n{'='*60}")
    print("📊 API Comparison:")
    print(f"{'='*60}")
    print("""
    # Mem0:
    from mem0 import Memory
    m = Memory()
    m.add("I work at Uzum Bank", user_id="ali")
    m.search("where does ali work?")
    
    # Mengram (ours):
    from mengram import Memory
    m = Memory(vault_path="./vault", llm_provider="anthropic", api_key="...")
    m.add("I work at Uzum Bank", user_id="ali")
    m.search("where does ali work?")
    
    # Difference: m.get("PostgreSQL") → typed entity with facts + relations + graph
    # Mem0 doesn't have this
    """)
