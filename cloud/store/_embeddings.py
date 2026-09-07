"""Entity chunk embeddings.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import re


class EmbeddingMixin:
    """Entity chunk embeddings."""


    # ---- Embeddings ----

    def save_embedding(self, entity_id: str, chunk_text: str,
                       embedding: list[float]):
        """Store vector embedding for an entity chunk.
        Routes to `embedding` column (1536-dim, OpenAI) or `embedding_v2`
        column (1024-dim, Cohere) based on the actual vector size."""
        col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        embedding_str = f"[{','.join(str(x) for x in embedding)}]"
        with self._cursor() as cur:
            cur.execute(
                f"""INSERT INTO embeddings (entity_id, chunk_text, {col}, tsv)
                    VALUES (%s, %s, %s::vector, to_tsvector('english', %s))""",
                (entity_id, chunk_text, embedding_str, chunk_text)
            )

    def build_entity_chunks(self, entity_id: str, name: str,
                            summarize=None) -> list[str]:
        """Every embeddable chunk for an entity: its name, each active fact,
        each relation and each knowledge item.

        Callers replace an entity's whole embedding set (delete + re-insert),
        so the chunk list must cover the entity's *current* state, not just the
        facts of the conversation being ingested. The `"{name}: {fact}"` fact
        format is load-bearing: search_vector recovers fact text by splitting
        the chunk on the first ": " to match it against facts rows.

        summarize: optional (text) -> text, applied to knowledge over 2000
        chars; falls back to truncation when not supplied.
        """
        chunks = [name]
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT content FROM facts
                   WHERE entity_id = %s AND archived = FALSE
                     AND (expires_at IS NULL OR expires_at > NOW())""",
                (entity_id,)
            )
            chunks.extend(f"{name}: {r['content']}" for r in cur.fetchall())

            cur.execute(
                """SELECT r.type, e.name AS target
                   FROM relations r JOIN entities e ON e.id = r.target_id
                   WHERE r.source_id = %s""",
                (entity_id,)
            )
            chunks.extend(
                f"{name} {r['type']} {r['target']}"
                for r in cur.fetchall() if r["type"] and r["target"]
            )

            cur.execute(
                "SELECT title, content FROM knowledge WHERE entity_id = %s",
                (entity_id,)
            )
            for r in cur.fetchall():
                kt = f"{r['title'] or ''} {r['content'] or ''}".strip()
                if not kt:
                    continue
                if len(kt) > 2000:
                    kt = summarize(kt) if summarize else kt[:2000]
                chunks.append(kt)

        return chunks

    def get_embedded_chunk_texts(self, entity_id: str, dimensions: int) -> set[str]:
        """chunk_text values that already have a vector in the column matching
        `dimensions`. Lets callers embed only what changed instead of paying to
        re-embed an entity's whole history on every write."""
        col = "embedding_v2" if dimensions == 1024 else "embedding"
        with self._cursor() as cur:
            cur.execute(
                f"SELECT chunk_text FROM embeddings "
                f"WHERE entity_id = %s AND {col} IS NOT NULL",
                (entity_id,)
            )
            return {r[0] for r in cur.fetchall()}

    def delete_embeddings_for_texts(self, entity_id: str, texts: list[str]):
        """Drop the chunks listed — used to retire text that is no longer part
        of the entity (archived facts, removed relations)."""
        if not texts:
            return
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM embeddings WHERE entity_id = %s AND chunk_text = ANY(%s)",
                (entity_id, list(texts))
            )

    def delete_embeddings(self, entity_id: str):
        """Remove all embeddings for entity (before reindex)."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM embeddings WHERE entity_id = %s",
                (entity_id,)
            )
