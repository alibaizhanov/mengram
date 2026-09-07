"""Procedural memory: versions, feedback, evolution, regression gate.

Part of :class:`cloud.store.CloudStore`; split out of the former single-file store.
"""

import datetime
import json
import math
import re

from ._common import (  # noqa: F401
    annotate_steps, carry_step_history, estimate, procedure_similarity, is_near_duplicate_procedure, apply_step_outcome, psycopg2, logger,
)


def _iso(value):
    """ISO string for a timestamp column, None for NULL. Dict-cursor rows may lack the key on old fixtures."""
    return value.isoformat() if hasattr(value, "isoformat") else (value or None)


class ProcedureMixin:
    """Procedural memory: versions, feedback, evolution, regression gate."""


    # =====================================================
    # PROCEDURAL MEMORY v2.5
    # =====================================================

    def save_procedure(self, user_id: str, name: str, trigger_condition: str = None,
                       steps: list[dict] = None, entity_names: list[str] = None,
                       source_episode_ids: list[str] = None,
                       metadata: dict = None, expires_at: str = None,
                       version: int = 1, parent_version_id: str = None,
                       evolved_from_episode: str = None,
                       is_current: bool = True,
                       sub_user_id: str = "default") -> str:
        """Save or update a procedural memory — learned workflow/skill."""
        meta_json = json.dumps(metadata) if metadata else '{}'
        steps_json = json.dumps(steps or [])
        entities = entity_names or []
        ep_ids = source_episode_ids or []

        # Case-insensitive lookup: use existing name to trigger ON CONFLICT correctly
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT name FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s)
                   AND version = %s LIMIT 1""",
                (user_id, sub_user_id, name, version)
            )
            existing = cur.fetchone()
            if existing:
                name = existing["name"]  # Use canonical casing

        # Conflicts on version 1 come from re-extraction (add/reflection) and
        # must not resurrect a retired v1 next to a newer current version, so
        # its lifecycle fields stay untouched. Conflicts on version > 1 only
        # come from evolve_procedure landing on a stale quarantined copy of the
        # same version number — there the incoming row is authoritative: without
        # taking its is_current/metadata the old version is already retired and
        # the procedure is left with no current version at all.
        try:
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO procedures
                       (user_id, sub_user_id, name, trigger_condition, steps, entity_names,
                        source_episode_ids, metadata, expires_at,
                        version, parent_version_id, evolved_from_episode, is_current)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::uuid[], %s::jsonb, %s,
                               %s, %s, %s, %s)
                       ON CONFLICT ON CONSTRAINT uq_procedures_user_sub_name_ver
                       DO UPDATE SET
                           trigger_condition = COALESCE(EXCLUDED.trigger_condition, procedures.trigger_condition),
                           steps = EXCLUDED.steps,
                           entity_names = EXCLUDED.entity_names,
                           is_current = CASE WHEN procedures.version > 1
                                             THEN EXCLUDED.is_current
                                             ELSE procedures.is_current END,
                           metadata = CASE WHEN procedures.version > 1
                                           THEN EXCLUDED.metadata
                                           ELSE procedures.metadata END,
                           parent_version_id = CASE WHEN procedures.version > 1
                                                    THEN EXCLUDED.parent_version_id
                                                    ELSE procedures.parent_version_id END,
                           evolved_from_episode = CASE WHEN procedures.version > 1
                                                       THEN EXCLUDED.evolved_from_episode
                                                       ELSE procedures.evolved_from_episode END,
                           updated_at = NOW()
                       RETURNING id""",
                    (user_id, sub_user_id, name, trigger_condition, steps_json, entities,
                     ep_ids if ep_ids else None, meta_json, expires_at,
                     version, parent_version_id, evolved_from_episode, is_current)
                )
                proc_id = str(cur.fetchone()[0])
        except (psycopg2.IntegrityError, psycopg2.errors.UniqueViolation):
            # Race condition: CI index caught a case-different duplicate
            with self._cursor(dict_cursor=True) as cur:
                cur.execute(
                    """SELECT id FROM procedures
                       WHERE user_id = %s AND sub_user_id = %s AND LOWER(name) = LOWER(%s) AND version = %s""",
                    (user_id, sub_user_id, name, version)
                )
                row = cur.fetchone()
                if row:
                    proc_id = str(row["id"])
                else:
                    raise
        logger.info(f"⚙️ Procedure saved: {name} v{version}")
        return proc_id

    def find_near_duplicate_procedure(self, user_id: str, name: str, steps: list,
                                      sub_user_id: str = "default") -> dict | None:
        """The current procedure this extraction is really about, if one exists.

        Extraction names the same workflow a little differently every run, and
        the unique constraint only catches an exact (case-insensitive) match,
        so one user ends up with "Deploy to Railway", "Deploying to Railway"
        and "Railway deploy" as three procedures competing in every search.
        This compares normalised name tokens and step tokens against the
        user's current procedures and returns the best near-duplicate."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, steps, success_count, fail_count
                   FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s AND is_current = TRUE
                     AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY updated_at DESC
                   LIMIT 2000""",
                (user_id, sub_user_id)
            )
            rows = cur.fetchall()
        best, best_score = None, 0.0
        for row in rows:
            if (row["name"] or "").strip().lower() == (name or "").strip().lower():
                continue    # exact match is the unique constraint's job, not ours
            name_sim, step_sim = procedure_similarity(name, steps, row["name"], row["steps"] or [])
            if is_near_duplicate_procedure(name_sim, step_sim) and name_sim + step_sim > best_score:
                best, best_score = row, name_sim + step_sim
        if best is None:
            return None
        return {
            "id": str(best["id"]),
            "name": best["name"],
            "steps": best["steps"] or [],
            "success_count": best["success_count"] or 0,
            "fail_count": best["fail_count"] or 0,
        }

    def save_extracted_procedure(self, user_id: str, name: str, trigger_condition: str = None,
                                 steps: list = None, entity_names: list = None,
                                 metadata: dict = None, expires_at: str = None,
                                 sub_user_id: str = "default") -> tuple:
        """Save a freshly extracted v1 procedure without minting a near-duplicate.

        Returns (procedure_id, action) where action is one of:
          "created"   — no near-duplicate; saved as usual (exact-name conflicts
                        still take the ON CONFLICT path inside save_procedure).
          "refreshed" — a near-duplicate exists but has never been run; its
                        steps, trigger and entities are updated in place under
                        its existing name. Nothing was ever learned about it,
                        so the newer extraction is the better description.
          "kept"      — a near-duplicate exists and has a record. It is left
                        exactly as it is: an extraction is a description, not
                        a run, and must not overwrite evidence.
        """
        dup = self.find_near_duplicate_procedure(user_id, name, steps or [], sub_user_id=sub_user_id)
        if dup is None:
            proc_id = self.save_procedure(
                user_id=user_id, name=name, trigger_condition=trigger_condition,
                steps=steps, entity_names=entity_names, metadata=metadata,
                expires_at=expires_at, sub_user_id=sub_user_id,
            )
            return proc_id, "created"

        if dup["success_count"] + dup["fail_count"] > 0:
            logger.info(f"⚙️ Procedure kept: '{name}' is '{dup['name']}' "
                        f"({dup['success_count']}✓/{dup['fail_count']}✗) — not overwritten")
            return dup["id"], "kept"

        with self._cursor() as cur:
            cur.execute(
                """UPDATE procedures
                   SET steps = %s::jsonb,
                       trigger_condition = COALESCE(%s, trigger_condition),
                       entity_names = CASE WHEN %s::text[] IS NULL OR cardinality(%s::text[]) = 0
                                           THEN entity_names ELSE %s::text[] END,
                       updated_at = NOW()
                   WHERE id = %s AND user_id = %s AND sub_user_id = %s""",
                (json.dumps(steps or []), trigger_condition,
                 entity_names, entity_names, entity_names,
                 dup["id"], user_id, sub_user_id)
            )
        logger.info(f"⚙️ Procedure refreshed: '{name}' → '{dup['name']}' (untested, updated in place)")
        return dup["id"], "refreshed"

    def save_procedure_embedding(self, procedure_id: str, chunk_text: str, embedding: list[float]):
        """Save embedding for a procedure. Routes by vector size."""
        col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        with self._cursor() as cur:
            cur.execute(
                f"""INSERT INTO procedure_embeddings (procedure_id, chunk_text, {col}, tsv)
                    VALUES (%s, %s, %s::vector, to_tsvector('english', %s))""",
                (procedure_id, chunk_text, embedding, chunk_text)
            )

    def delete_procedure_embeddings(self, procedure_id: str):
        """Delete all embeddings for a procedure."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM procedure_embeddings WHERE procedure_id = %s", (procedure_id,))

    def get_procedures(self, user_id: str, limit: int = 20,
                       sub_user_id: str = "default", offset: int = 0) -> list[dict]:
        """Get all current procedures for a user (latest versions only)."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, trigger_condition, steps, entity_names,
                          success_count, fail_count, last_used, last_succeeded, version,
                          created_at, updated_at, metadata
                   FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s
                     AND is_current = TRUE
                     AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY updated_at DESC
                   LIMIT %s OFFSET %s""",
                (user_id, sub_user_id, limit, offset)
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "name": row["name"],
                    "trigger_condition": row["trigger_condition"],
                    # The agent reads this, and a model handed 4 and 1 will
                    # answer 80% — the small-sample error the estimate exists
                    # to prevent. Raw counts stay; the reading comes with them.
                    "steps": annotate_steps(row["steps"] or []),
                    "entity_names": row["entity_names"] or [],
                    "success_count": row["success_count"] or 0,
                    "fail_count": row["fail_count"] or 0,
                    "reliability": estimate(row["success_count"] or 0,
                                            row["fail_count"] or 0),
                    "version": row["version"] or 1,
                    "last_used": row["last_used"].isoformat() if row["last_used"] else None,
                    "last_succeeded": _iso(row.get("last_succeeded")),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "procedural",
                })
            return results

    def count_procedures(self, user_id: str, sub_user_id: str = "default") -> int:
        """Count current non-expired procedures (pagination total for /v1/procedures)."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s
                     AND is_current = TRUE
                     AND (expires_at IS NULL OR expires_at > NOW())""",
                (user_id, sub_user_id)
            )
            return cur.fetchone()[0]

    def search_procedures_vector(self, user_id: str, embedding: list[float],
                                 top_k: int = 5, sub_user_id: str = "default",
                                 query_text: str = "") -> list[dict]:
        """Hybrid search over procedural memory: vector + BM25 + RRF + proven-success
        weighting (current versions only). Routes by query vector size: 1024 →
        embedding_v2, else embedding. Procedures with track record (high success_count,
        low fail_count, recently used) are surfaced ahead of equally-relevant but
        untested or stale ones."""
        emb_col = "embedding_v2" if len(embedding) == 1024 else "embedding"
        if query_text:
            query_text = query_text.replace("\x00", "")
        query = f"""
            SELECT p.id, p.name, p.trigger_condition, p.steps, p.entity_names,
                   p.success_count, p.fail_count, p.last_used, p.last_succeeded, p.version, p.updated_at, p.metadata,
                   1 - (pe.{emb_col} <=> %s::vector) AS score
            FROM procedure_embeddings pe
            JOIN procedures p ON p.id = pe.procedure_id
            WHERE p.user_id = %s AND p.sub_user_id = %s
              AND p.is_current = TRUE
              AND (p.expires_at IS NULL OR p.expires_at > NOW())
              AND pe.{emb_col} IS NOT NULL
              AND 1 - (pe.{emb_col} <=> %s::vector) > 0.25
            ORDER BY pe.{emb_col} <=> %s::vector
            LIMIT %s
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(query, (embedding, user_id, sub_user_id, embedding, embedding, top_k * 4))
            # Stage 1: Vector results
            vec_rows = {}
            seen = set()
            for rank, row in enumerate(cur.fetchall()):
                pid = str(row["id"])
                if pid in seen:
                    continue
                seen.add(pid)
                vec_rows[pid] = (rank, row)

            # Stage 2: BM25 text results
            bm25_rows = {}
            if query_text:
                cur.execute("""
                    SELECT DISTINCT ON (p.id)
                           p.id,
                           ts_rank_cd(pe.tsv, plainto_tsquery('english', %s), 32) AS rank
                    FROM procedure_embeddings pe
                    JOIN procedures p ON p.id = pe.procedure_id
                    WHERE p.user_id = %s AND p.sub_user_id = %s
                      AND p.is_current = TRUE
                      AND (p.expires_at IS NULL OR p.expires_at > NOW())
                      AND pe.tsv @@ plainto_tsquery('english', %s)
                    ORDER BY p.id, rank DESC
                """, (query_text, user_id, sub_user_id, query_text))
                bm25_list = cur.fetchall()
                bm25_list.sort(key=lambda r: float(r["rank"]), reverse=True)
                bm25_rows = {str(r["id"]): i for i, r in enumerate(bm25_list[:top_k * 4])}

                # Fetch full rows for BM25-only hits
                for pid in bm25_rows:
                    if pid not in vec_rows:
                        cur.execute("""
                            SELECT p.id, p.name, p.trigger_condition, p.steps, p.entity_names,
                                   p.success_count, p.fail_count, p.last_used, p.last_succeeded, p.version, p.updated_at, p.metadata
                            FROM procedures p WHERE p.id = %s
                        """, (pid,))
                        r = cur.fetchone()
                        if r:
                            r = dict(r)
                            r["score"] = 0
                            vec_rows[pid] = (len(vec_rows), r)

            # Stage 3: RRF fusion (k=60)
            rrf_k = 60
            rrf_scores = {}
            for pid, (rank, _) in vec_rows.items():
                rrf_scores[pid] = 1.0 / (rrf_k + rank)
            for pid, rank in bm25_rows.items():
                rrf_scores[pid] = rrf_scores.get(pid, 0) + 1.0 / (rrf_k + rank)

            # Build results — RRF score weighted by proven success + recency.
            # Procedures store success_count + fail_count + last_used. ~1960 procedures
            # in prod have real track record (max 70 successful runs); the signal was
            # ignored by the ranker. Now:
            #   history_factor: 100% success → ×1.3, 50/50 → ×1.0, 100% fail → ×0.7
            #                   no history → ×1.0 (neutral — don't penalize untested)
            #   recency_factor: used today → ×1.0, 30 days ago → ×0.86, year ago → ×0.70
            #                   never used → ×1.0 (neutral)
            # Combined range at equal vector match: 0.49× (failed+stale) to 1.30× (proven+fresh).
            now = datetime.datetime.now(datetime.timezone.utc)
            results = []
            for pid in sorted(rrf_scores, key=rrf_scores.get, reverse=True):
                _, row = vec_rows[pid]
                s_count = row.get("success_count") or 0
                f_count = row.get("fail_count") or 0
                total_runs = s_count + f_count
                if total_runs > 0:
                    success_rate = s_count / total_runs
                    history_factor = 0.7 + 0.6 * success_rate
                else:
                    history_factor = 1.0  # untested → neutral

                last_used = row.get("last_used")
                if last_used:
                    try:
                        age_days = (now - last_used.replace(tzinfo=datetime.timezone.utc)).days
                        recency_factor = 0.7 + 0.3 * math.exp(-0.02 * age_days)
                    except Exception:
                        recency_factor = 0.85
                else:
                    recency_factor = 1.0  # never used → neutral

                final_score = round(rrf_scores[pid] * history_factor * recency_factor, 4)
                results.append({
                    "id": pid,
                    "name": row["name"],
                    "trigger_condition": row.get("trigger_condition"),
                    "steps": row.get("steps") or [],
                    "entity_names": row.get("entity_names") or [],
                    "success_count": s_count,
                    "fail_count": f_count,
                    "reliability": estimate(s_count, f_count),
                    "version": row.get("version") or 1,
                    "last_used": last_used.isoformat() if last_used else None,
                    "last_succeeded": _iso(row.get("last_succeeded")),
                    "score": final_score,
                    "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "procedural",
                })
            results = results[:top_k]
            # Normalize scores to 0-1 range (RRF scores are tiny, clients expect 0-1)
            # Only normalize when 2+ results; single result keeps raw score to avoid false 1.0
            if len(results) >= 2:
                max_s = max(r["score"] for r in results)
                if max_s > 0:
                    for r in results:
                        r["score"] = round(r["score"] / max_s, 4)
        self._touch_procedures_last_used([r["id"] for r in results])
        return results

    def search_procedures_text(self, user_id: str, query: str,
                               top_k: int = 5, sub_user_id: str = "default") -> list[dict]:
        """BM25 text search over procedural memory (current versions only)."""
        if query:
            query = query.replace("\x00", "")
        sql = """
            SELECT p.id, p.name, p.trigger_condition, p.steps, p.entity_names,
                   p.success_count, p.fail_count, p.last_used, p.last_succeeded, p.version, p.updated_at, p.metadata,
                   ts_rank_cd(pe.tsv, plainto_tsquery('english', %s), 32) AS score
            FROM procedure_embeddings pe
            JOIN procedures p ON p.id = pe.procedure_id
            WHERE p.user_id = %s AND p.sub_user_id = %s
              AND p.is_current = TRUE
              AND (p.expires_at IS NULL OR p.expires_at > NOW())
              AND pe.tsv @@ plainto_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(sql, (query, user_id, sub_user_id, query, top_k))
            results = []
            seen = set()
            for row in cur.fetchall():
                pid = str(row["id"])
                if pid in seen:
                    continue
                seen.add(pid)
                results.append({
                    "id": pid,
                    "name": row["name"],
                    "trigger_condition": row["trigger_condition"],
                    # The agent reads this, and a model handed 4 and 1 will
                    # answer 80% — the small-sample error the estimate exists
                    # to prevent. Raw counts stay; the reading comes with them.
                    "steps": annotate_steps(row["steps"] or []),
                    "entity_names": row["entity_names"] or [],
                    "success_count": row["success_count"] or 0,
                    "fail_count": row["fail_count"] or 0,
                    "reliability": estimate(row["success_count"] or 0,
                                            row["fail_count"] or 0),
                    "version": row["version"] or 1,
                    "last_used": row["last_used"].isoformat() if row.get("last_used") else None,
                    "last_succeeded": _iso(row.get("last_succeeded")),
                    "score": round(float(row["score"]), 4),
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    "metadata": row.get("metadata") or {},
                    "memory_type": "procedural",
                })
        self._touch_procedures_last_used([r["id"] for r in results])
        return results

    @staticmethod
    def _apply_step_outcome(steps: list, success: bool, failed_at_step: int = None) -> list:
        """Credit the steps that ran and debit the one that broke — see
        cloud.procedure_match.apply_step_outcome, shared with the local store."""
        return apply_step_outcome(steps, success, failed_at_step)

    def procedure_feedback(self, user_id: str, procedure_id: str, success: bool,
                           sub_user_id: str = "default", failed_at_step: int = None) -> dict:
        """Record success/failure feedback for a procedure and for its steps."""
        col = "success_count" if success else "fail_count"
        # A success is the only thing that moves last_succeeded — never a read.
        mark = ", last_succeeded = NOW()" if success else ""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT steps FROM procedures
                   WHERE id = %s AND user_id = %s AND sub_user_id = %s""",
                (procedure_id, user_id, sub_user_id)
            )
            found = cur.fetchone()
            if not found:
                return {"error": "procedure not found"}
            steps = self._apply_step_outcome(found["steps"] or [], success, failed_at_step)

            cur.execute(
                f"""UPDATE procedures
                    SET {col} = {col} + 1, steps = %s::jsonb,
                        last_used = NOW(), updated_at = NOW(){mark}
                    WHERE id = %s AND user_id = %s AND sub_user_id = %s
                    RETURNING id, name, success_count, fail_count, steps, last_succeeded""",
                (json.dumps(steps), procedure_id, user_id, sub_user_id)
            )
            row = cur.fetchone()
            return {
                "id": str(row["id"]),
                "name": row["name"],
                "success_count": row["success_count"],
                "fail_count": row["fail_count"],
                "steps": row["steps"],
                "last_succeeded": _iso(row.get("last_succeeded")),
                "feedback": "success" if success else "failure",
            }

    # =====================================================
    # EXPERIENCE-DRIVEN PROCEDURES v2.7
    # =====================================================

    def get_procedure_by_id(self, user_id: str, procedure_id: str, sub_user_id: str = "default") -> dict | None:
        """Get a single procedure by ID."""
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, trigger_condition, steps, entity_names,
                          success_count, fail_count, version, parent_version_id,
                          evolved_from_episode, is_current, last_used, last_succeeded,
                          created_at, updated_at, metadata
                   FROM procedures
                   WHERE id = %s AND user_id = %s AND sub_user_id = %s""",
                (procedure_id, user_id, sub_user_id)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": str(row["id"]),
                "name": row["name"],
                "trigger_condition": row["trigger_condition"],
                "steps": row["steps"] or [],
                "entity_names": row["entity_names"] or [],
                "success_count": row["success_count"] or 0,
                "fail_count": row["fail_count"] or 0,
                "version": row["version"] or 1,
                "parent_version_id": str(row["parent_version_id"]) if row["parent_version_id"] else None,
                "evolved_from_episode": str(row["evolved_from_episode"]) if row["evolved_from_episode"] else None,
                "is_current": row["is_current"],
                "last_used": row["last_used"].isoformat() if row["last_used"] else None,
                "last_succeeded": _iso(row.get("last_succeeded")),
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "metadata": row.get("metadata") or {},
            }

    def evolve_procedure(self, user_id: str, procedure_id: str,
                         new_steps: list[dict], new_trigger: str = None,
                         episode_id: str = None, change_type: str = "step_modified",
                         diff: dict = None, metadata: dict = None,
                         sub_user_id: str = "default") -> str:
        """Create a new version of a procedure (experience-driven evolution).

        Marks the old version as not current, creates a new row with version+1,
        and logs the evolution in procedure_evolution table.
        Returns the new procedure ID.
        """
        old = self.get_procedure_by_id(user_id, procedure_id, sub_user_id=sub_user_id)
        if not old:
            raise ValueError(f"Procedure {procedure_id} not found")

        old_version = old["version"]
        new_version = old_version + 1
        new_metadata = metadata if metadata is not None else (old.get("metadata") or {})

        # The revision arrives as plain text from the LLM, with no counters on
        # it. Written as-is it wipes the record of every step, including the
        # ones the revision never touched — so a workflow could never build
        # evidence about its stable parts. Steps whose text is unchanged keep
        # their record; anything edited starts at zero, because nobody has run
        # *that* yet.
        new_steps = carry_step_history(old.get("steps") or [], new_steps)

        # --- Cross-procedure regression gate (v1) ---------------------------
        # Before promoting, check whether this revision silently breaks another
        # current procedure that shared surface with it. If so, quarantine the
        # new version for review instead of shipping it to an agent. The one
        # problem the 2025-26 procedural-memory literature leaves open.
        from cloud.regression_gate import find_regressions
        new_proc_view = {
            "id": None, "name": old["name"],
            "entity_names": old["entity_names"],
            "steps": new_steps,
            "trigger_condition": new_trigger or old["trigger_condition"],
            "metadata": new_metadata,
        }
        regressions = []
        try:
            others = [p for p in self.get_procedures(user_id, limit=200, sub_user_id=sub_user_id)
                      if str(p.get("id")) != str(procedure_id)]
            regressions = find_regressions(old, new_proc_view, others)
        except Exception as e:
            logger.warning(f"regression gate skipped ({e})")

        gated = bool(regressions)
        if gated:
            gate_meta = dict(new_metadata)
            gate_meta["status"] = "needs_review"
            gate_meta["quarantine_reason"] = regressions
            new_metadata = gate_meta

        # A version that has never succeeded is a hypothesis, not evidence.
        # Superseding one with another mints a version number for something
        # nobody tried, and the production data shows where that leads: a chain
        # 32 deep, fourteen versions in two days, and the one ancestor with a
        # real record (9 successes) buried under revisions that were never run.
        # So an unproven version is revised in place. Its successor inherits
        # nothing because there was nothing to inherit, and the last version
        # that actually worked stays the lineage's most recent evidence.
        #
        # A gated revision still takes the versioned path: quarantine needs a
        # row of its own to hold the flagged copy while the old one serves.
        if not gated and int(old.get("success_count") or 0) == 0:
            with self._cursor() as cur:
                cur.execute(
                    """UPDATE procedures
                       SET steps = %s::jsonb, trigger_condition = %s,
                           metadata = %s::jsonb, evolved_from_episode = %s,
                           updated_at = NOW()
                       WHERE id = %s AND user_id = %s AND sub_user_id = %s""",
                    (json.dumps(new_steps), new_trigger or old["trigger_condition"],
                     json.dumps(new_metadata), episode_id,
                     procedure_id, user_id, sub_user_id)
                )
            with self._cursor() as cur:
                cur.execute(
                    """INSERT INTO procedure_evolution
                       (procedure_id, episode_id, change_type, diff,
                        version_before, version_after)
                       VALUES (%s, %s, %s, %s::jsonb, %s, %s)""",
                    (procedure_id, episode_id, "revised_in_place",
                     json.dumps(dict(diff or {})), old_version, old_version)
                )
            logger.info(f"✏️ Procedure revised in place: {old['name']} v{old_version} "
                        f"(never succeeded, so no new version minted)")
            return procedure_id

        # Only retire the old current version if the new one is safe to promote.
        if not gated:
            with self._cursor() as cur:
                cur.execute(
                    "UPDATE procedures SET is_current = FALSE, updated_at = NOW() WHERE id = %s",
                    (procedure_id,)
                )

        # Create new version. If gated, it lands as NOT current (quarantined) —
        # the last known-good version stays authoritative until review.
        new_proc_id = self.save_procedure(
            user_id=user_id,
            name=old["name"],
            trigger_condition=new_trigger or old["trigger_condition"],
            steps=new_steps,
            entity_names=old["entity_names"],
            metadata=new_metadata,
            version=new_version,
            parent_version_id=procedure_id,
            evolved_from_episode=episode_id,
            is_current=not gated,
            sub_user_id=sub_user_id,
        )

        # Log evolution
        with self._cursor() as cur:
            log_diff = dict(diff or {})
            if gated:
                log_diff["quarantined"] = regressions
            cur.execute(
                """INSERT INTO procedure_evolution
                   (procedure_id, episode_id, change_type, diff,
                    version_before, version_after)
                   VALUES (%s, %s, %s, %s::jsonb, %s, %s)""",
                (new_proc_id, episode_id,
                 "quarantined" if gated else change_type,
                 json.dumps(log_diff), old_version, new_version)
            )

        if gated:
            names = ", ".join(r["dependent_name"] or "?" for r in regressions)
            logger.info(f"🚧 Procedure revision quarantined: {old['name']} "
                        f"v{old_version}→v{new_version} may break: {names}")
        else:
            logger.info(f"🔄 Procedure evolved: {old['name']} v{old_version} → v{new_version}")
        return new_proc_id

    def get_procedure_history(self, user_id: str, procedure_id: str, sub_user_id: str = "default") -> list[dict]:
        """Get all versions of a procedure by tracing the version chain.

        Finds the procedure name, then returns all versions ordered by version number.
        """
        # First get the name from the given procedure
        proc = self.get_procedure_by_id(user_id, procedure_id, sub_user_id=sub_user_id)
        if not proc:
            return []

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, name, trigger_condition, steps, entity_names,
                          success_count, fail_count, version, parent_version_id,
                          evolved_from_episode, is_current, created_at, updated_at
                   FROM procedures
                   WHERE user_id = %s AND sub_user_id = %s AND name = %s
                   ORDER BY version ASC""",
                (user_id, sub_user_id, proc["name"])
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "name": row["name"],
                    "trigger_condition": row["trigger_condition"],
                    # The agent reads this, and a model handed 4 and 1 will
                    # answer 80% — the small-sample error the estimate exists
                    # to prevent. Raw counts stay; the reading comes with them.
                    "steps": annotate_steps(row["steps"] or []),
                    "entity_names": row["entity_names"] or [],
                    "success_count": row["success_count"] or 0,
                    "fail_count": row["fail_count"] or 0,
                    "reliability": estimate(row["success_count"] or 0,
                                            row["fail_count"] or 0),
                    "version": row["version"] or 1,
                    "parent_version_id": str(row["parent_version_id"]) if row["parent_version_id"] else None,
                    "evolved_from_episode": str(row["evolved_from_episode"]) if row["evolved_from_episode"] else None,
                    "is_current": row["is_current"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                })
            return results

    def get_procedure_evolution(self, user_id: str, procedure_id: str, sub_user_id: str = "default") -> list[dict]:
        """Get the evolution log for a procedure (all versions)."""
        # Get all version IDs for this procedure name
        proc = self.get_procedure_by_id(user_id, procedure_id, sub_user_id=sub_user_id)
        if not proc:
            return []

        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT pe.id, pe.procedure_id, pe.episode_id, pe.change_type,
                          pe.diff, pe.version_before, pe.version_after, pe.created_at,
                          prev.success_count AS prev_success,
                          prev.fail_count   AS prev_fail
                   FROM procedure_evolution pe
                   JOIN procedures p ON p.id = pe.procedure_id
                   -- The record the retiring version carried. A successor draws
                   -- its prior from this, so without it every revision reads as
                   -- untested next to the version it was written to fix.
                   -- UNIQUE(user_id, sub_user_id, name, version) keeps the join
                   -- to at most one row.
                   LEFT JOIN procedures prev
                          ON prev.user_id     = p.user_id
                         AND prev.sub_user_id = p.sub_user_id
                         AND prev.name        = p.name
                         AND prev.version     = pe.version_before
                   WHERE p.user_id = %s AND p.sub_user_id = %s AND p.name = %s
                   ORDER BY pe.created_at ASC""",
                (user_id, sub_user_id, proc["name"])
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "procedure_id": str(row["procedure_id"]),
                    "episode_id": str(row["episode_id"]) if row["episode_id"] else None,
                    "change_type": row["change_type"],
                    "diff": row["diff"] or {},
                    "version_before": row["version_before"],
                    "version_after": row["version_after"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "success_count": row["prev_success"],
                    "fail_count": row["prev_fail"],
                })
            return results

    def get_unlinked_actionable_episodes(self, user_id: str, limit: int = 50, sub_user_id: str = "default") -> list[dict]:
        """Get recent episodes not linked to any procedure, excluding failures.

        Returns positive, neutral, and mixed episodes for pattern detection.
        Includes neutral episodes which represent the majority of user activity.
        """
        with self._cursor(dict_cursor=True) as cur:
            cur.execute(
                """SELECT id, summary, context, outcome, participants,
                          emotional_valence, importance, created_at
                   FROM episodes
                   WHERE user_id = %s AND sub_user_id = %s
                     AND linked_procedure_id IS NULL
                     AND emotional_valence IN ('positive', 'neutral', 'mixed')
                     AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (user_id, sub_user_id, limit)
            )
            results = []
            for row in cur.fetchall():
                results.append({
                    "id": str(row["id"]),
                    "summary": row["summary"],
                    "context": row["context"],
                    "outcome": row["outcome"],
                    "participants": row["participants"] or [],
                    "emotional_valence": row["emotional_valence"],
                    "importance": round(float(row["importance"] or 0.5), 2),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })
            return results

    def link_episodes_to_procedure(self, episode_ids: list[str], procedure_id: str):
        """Link episodes to a procedure (after auto-creating from pattern)."""
        if not episode_ids:
            return
        with self._cursor() as cur:
            cur.execute(
                """UPDATE episodes SET linked_procedure_id = %s
                   WHERE id = ANY(%s::uuid[])""",
                (procedure_id, episode_ids)
            )
