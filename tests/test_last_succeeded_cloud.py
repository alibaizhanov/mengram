"""procedures.last_succeeded in the cloud store: moved only by a recorded success.

Hermetic: CloudStore is built without __init__ and given a stub cursor, the same
way the other store tests do it, so the assertions are about the SQL we send.
"""

import contextlib
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store import CloudStore  # noqa: E402
from cloud.markdown_export import procedure_file  # noqa: E402


class _FakeCursor:
    def __init__(self, fetchone_results=None):
        self._ones = list(fetchone_results or [])
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return self._ones.pop(0) if self._ones else None

    def fetchall(self):
        return []


def _store(cursor):
    store = CloudStore.__new__(CloudStore)

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cursor

    store._cursor = _cursor
    return store


NOW = datetime.datetime(2026, 9, 7, 12, 0, tzinfo=datetime.timezone.utc)


def test_success_sets_last_succeeded_in_the_same_update():
    cur = _FakeCursor([{"steps": [{"action": "push"}]},
                       {"id": "p1", "name": "Deploy", "success_count": 1, "fail_count": 0,
                        "steps": [], "last_succeeded": NOW}])
    out = _store(cur).procedure_feedback("u", "p1", True)
    update = cur.sql[-1]
    assert "success_count = success_count + 1" in update
    assert "last_succeeded = NOW()" in update and "RETURNING" in update and "last_succeeded" in update.split("RETURNING")[1]
    assert out["last_succeeded"] == NOW.isoformat()


def test_failure_leaves_last_succeeded_alone():
    cur = _FakeCursor([{"steps": [{"action": "push"}]},
                       {"id": "p1", "name": "Deploy", "success_count": 0, "fail_count": 1,
                        "steps": [], "last_succeeded": None}])
    out = _store(cur).procedure_feedback("u", "p1", False, failed_at_step=1)
    update = cur.sql[-1]
    assert "fail_count = fail_count + 1" in update and "last_succeeded = NOW()" not in update
    assert out["last_succeeded"] is None


def test_migration_adds_the_column_without_a_backfill():
    src = (Path(__file__).parent.parent / "cloud" / "store" / "_core.py").read_text()
    block = src.split("v2.23")[1]
    assert "ADD COLUMN IF NOT EXISTS last_succeeded TIMESTAMPTZ" in block
    assert "UPDATE procedures SET last_succeeded" not in src  # never derived from last_used


def test_export_writes_last_succeeded_as_a_date_and_a_body_line():
    out = procedure_file({"id": "p1", "name": "Deploy", "success_count": 3, "fail_count": 0,
                          "version": 1, "steps": [{"action": "push"}],
                          "last_succeeded": "2026-09-07T12:00:00+00:00"})
    assert "last_succeeded: 2026-09-07" in out and "**Last success** — 2026-09-07" in out


def test_export_stays_silent_without_it():
    out = procedure_file({"id": "p1", "name": "Deploy", "success_count": 3, "fail_count": 0,
                          "version": 1, "steps": [{"action": "push"}]})
    assert "last_succeeded" not in out and "Last success" not in out
