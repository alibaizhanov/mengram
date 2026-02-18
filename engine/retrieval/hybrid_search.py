"""
Hybrid Retrieval Engine — combines Vector Search + Graph Traversal.

This is the key feature that differentiates from Mem0:
1. Vector search → finds semantically similar chunks
2. Graph expansion → expands through relations to N levels
3. Context assembly → assembles into structured response for AI
"""

from dataclasses import dataclass, field

from engine.graph.knowledge_graph import KnowledgeGraph, build_graph_from_vault
from engine.vector.vector_store import VectorStore, SearchResult, index_vault


@dataclass
class RetrievalResult:
    """Full hybrid search result"""
    query: str
    # Direct matches from vector search
    direct_matches: list[SearchResult] = field(default_factory=list)
    # Related entities from graph expansion
    graph_context: list[dict] = field(default_factory=list)
    # Assembled context for AI agent
    assembled_context: str = ""

    def __repr__(self):
        return (
            f"RetrievalResult(\n"
            f"  query='{self.query}'\n"
            f"  direct_matches={len(self.direct_matches)}\n"
            f"  graph_entities={len(self.graph_context)}\n"
            f"  context_length={len(self.assembled_context)} chars\n"
            f")"
        )


class HybridRetrieval:
    """
    Hybrid search: Vector + Graph.

    Workflow:
    1. Vector search on query → top-K chunks
    2. Extract entity_id from found chunks
    3. Graph traversal from these entities to depth levels
    4. Assemble context: direct matches + graph relations
    """

    def __init__(self, graph: KnowledgeGraph, vector_store: VectorStore):
        self.graph = graph
        self.vector_store = vector_store

    def query(self, text: str, top_k: int = 5, graph_depth: int = 1,
              min_score: float = 0.15) -> RetrievalResult:
        """
        Main method — hybrid search.

        Args:
            text: search query
            top_k: how many chunks from vector search
            graph_depth: graph traversal depth
            min_score: minimum score for vector search
        """
        result = RetrievalResult(query=text)

        # Step 1: Vector search
        result.direct_matches = self.vector_store.search(
            query=text, top_k=top_k, min_score=min_score
        )

        # Step 2: Graph expansion from found entities
        seen_entities = set()
        for match in result.direct_matches:
            entity_id = match.entity_id
            if entity_id in seen_entities:
                continue
            seen_entities.add(entity_id)

            # Get entity from graph
            entity = self.graph.get_entity(entity_id)
            if not entity:
                continue

            # Graph traversal
            neighbors = self.graph.get_neighbors(entity_id, depth=graph_depth)
            for neighbor in neighbors:
                n_id = neighbor["entity"].id
                if n_id not in seen_entities:
                    result.graph_context.append(neighbor)
                    seen_entities.add(n_id)

        # Step 3: Assemble context for AI
        result.assembled_context = self._assemble_context(result)

        return result

    def get_entity_context(self, entity_name: str, graph_depth: int = 2) -> RetrievalResult:
        """
        Get full context for a specific entity.
        For queries like "tell me everything about Project Alpha".
        """
        result = RetrievalResult(query=f"context:{entity_name}")

        entity = self.graph.find_entity(entity_name)
        if not entity:
            result.assembled_context = f"Entity '{entity_name}' not found in vault."
            return result

        # All chunks for this entity
        chunks_data = self.vector_store.search_by_entity(entity.id)
        for chunk in chunks_data:
            result.direct_matches.append(SearchResult(
                chunk_id=chunk["id"],
                entity_id=chunk["entity_id"],
                entity_name=chunk["entity_name"],
                section=chunk["section"],
                content=chunk["content"],
                score=1.0,
            ))

        # Graph traversal
        neighbors = self.graph.get_neighbors(entity.id, depth=graph_depth)
        for neighbor in neighbors:
            result.graph_context.append(neighbor)

        result.assembled_context = self._assemble_context(result)
        return result

    def _assemble_context(self, result: RetrievalResult) -> str:
        """
        Assembles human-readable context from search results.
        This text goes into the AI agent prompt.
        """
        parts = []

        # Direct matches
        if result.direct_matches:
            parts.append("## Relevant fragments from notes\n")
            seen_content = set()
            for match in result.direct_matches:
                if match.content in seen_content:
                    continue
                seen_content.add(match.content)
                parts.append(
                    f"**{match.entity_name}** ({match.section}) "
                    f"[score: {match.score:.2f}]:\n"
                    f"{match.content}\n"
                )

        # Graph relations
        if result.graph_context:
            parts.append("\n## Related entities (from knowledge graph)\n")

            # Group by relation type
            by_type: dict[str, list] = {}
            for ctx in result.graph_context:
                rel = ctx["relation_type"]
                entity = ctx["entity"]
                # Skip tags for cleaner context
                if entity.entity_type == "tag":
                    continue
                by_type.setdefault(rel, []).append(entity)

            for rel_type, entities in by_type.items():
                names = ", ".join(e.name for e in entities)
                parts.append(f"- **{rel_type}**: {names}")

        return "\n".join(parts)


def build_retrieval_engine(vault_path: str) -> HybridRetrieval:
    """
    Creates full retrieval engine from vault.
    Builds graph + indexes vectors.
    """
    print("=" * 50)
    print("🏗️  Building Mengram Retrieval Engine")
    print("=" * 50)

    # Step 1: Knowledge Graph
    print("\n📊 Step 1: Building Knowledge Graph...")
    graph = build_graph_from_vault(vault_path)
    stats = graph.stats()
    print(f"   ✅ {stats['total_entities']} entities, {stats['total_relations']} relations")

    # Step 2: Vector Store
    print("\n📐 Step 2: Indexing vectors...")
    vector_store = index_vault(vault_path)

    # Step 3: Hybrid Engine
    print("\n🔗 Step 3: Connecting hybrid retrieval...")
    engine = HybridRetrieval(graph, vector_store)

    print("\n✅ Engine ready!\n")
    return engine


if __name__ == "__main__":
    import sys

    vault_path = sys.argv[1] if len(sys.argv) > 1 else "./test_vault"
    engine = build_retrieval_engine(vault_path)

    # Test queries
    queries = [
        "database performance issue",
        "who works on backend projects",
        "what technologies does Ali use",
    ]

    for q in queries:
        print(f"\n{'='*60}")
        print(f"🔍 Query: '{q}'")
        print(f"{'='*60}")

        result = engine.query(q, top_k=3, graph_depth=2)
        print(result)
        print(f"\n📋 Assembled context:\n{result.assembled_context}")

    # Test: context for entity
    print(f"\n{'='*60}")
    print(f"🎯 Entity context: 'Project Alpha'")
    print(f"{'='*60}")
    result = engine.get_entity_context("Project Alpha")
    print(result)
    print(f"\n📋 Context:\n{result.assembled_context}")
