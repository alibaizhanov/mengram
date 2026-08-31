"""Regression tests for the two audit H-bugs in upsert lifecycle handling.

H1 — a re-asserted fact must come back to life. UNIQUE(entity_id, content)
also covers archived rows, so the fact upsert used to hit DO UPDATE on a
superseded fact, report success via RETURNING, and leave it archived —
"lives in Almaty" could never return after being superseded.

H2 — a procedure must never end up with zero current versions. evolve_procedure
retires the old current version before inserting the new one; when the new
(name, version) collides with a stale quarantined row, the old DO UPDATE
rewrote steps but not is_current/metadata, so the quarantined row stayed
non-current (and kept its needs_review flag) while the old version was already
retired — the procedure vanished from every listing.

Hermetic: CloudStore is constructed without __init__ and given a stub cursor,
so no database is required. The assertions pin the SQL the store actually
sends, normalized to single spaces.
"""

import contextlib

from cloud.store import CloudStore


class _FakeCursor:
    """Records normalized SQL; serves queued fetchone/fetchall results."""

    def __init__(self, fetchone_results=None, fetchall_results=None):
        self._ones = list(fetchone_results or [])
        self._alls = list(fetchall_results or [])
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return self._ones.pop(0) if self._ones else None

    def fetchall(self):
        return self._alls.pop(0) if self._alls else []


def _store(cursor):
    store = CloudStore.__new__(CloudStore)

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cursor

    store._cursor = _cursor
    store._schedule_matview_refresh = lambda: None
    return store


class TestFactUpsertRevives:
    def _fact_upserts(self, expires_at=None):
        cur = _FakeCursor(fetchone_results=[{"content": "lives in Almaty"}])
        store = _store(cur)
        store._add_facts_knowledge_relations(
            "eid", "uid", "Frank",
            facts=["lives in Almaty"], expires_at=expires_at,
        )
        return [s for s in cur.sql if "INSERT INTO facts" in s]

    def test_upsert_resets_archived_and_superseded(self):
        (sql,) = self._fact_upserts()
        assert "ON CONFLICT (entity_id, content) DO UPDATE" in sql
        assert "archived = FALSE" in sql
        assert "superseded_by = NULL" in sql

    def test_expiring_branch_resets_too(self):
        (sql,) = self._fact_upserts(expires_at="2027-01-01")
        assert "expires_at" in sql
        assert "archived = FALSE" in sql
        assert "superseded_by = NULL" in sql


class TestProcedureUpsertLifecycle:
    def _save_sql(self):
        cur = _FakeCursor(fetchone_results=[
            None,                                             # canonical-name lookup
            ("11111111-1111-1111-1111-111111111111",),        # INSERT ... RETURNING id
        ])
        store = _store(cur)
        store.save_procedure(
            "uid", "deploy", trigger_condition="on release",
            steps=[{"action": "ship"}], version=2, is_current=True,
        )
        return next(s for s in cur.sql if "INSERT INTO procedures" in s)

    def test_conflict_takes_incoming_lifecycle_on_evolved_versions(self):
        sql = self._save_sql()
        assert "ON CONFLICT ON CONSTRAINT uq_procedures_user_sub_name_ver" in sql
        for field in ("is_current", "metadata", "parent_version_id", "evolved_from_episode"):
            assert (
                f"{field} = CASE WHEN procedures.version > 1 THEN EXCLUDED.{field} ELSE procedures.{field} END"
                in sql
            ), f"{field} must follow the incoming row only for evolved versions"

    def test_version_one_conflicts_keep_existing_lifecycle(self):
        """Re-extraction hitting a retired v1 must not mint a second current
        version — the CASE arms fall back to the row's own values there."""
        sql = self._save_sql()
        assert "ELSE procedures.is_current END" in sql
        assert "ELSE procedures.metadata END" in sql
