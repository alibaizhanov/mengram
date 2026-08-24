"""A version nobody ran should not be superseded by another one nobody ran.

Production showed what the old rule cost: one procedure reached version 32 in
ten days, fourteen of those versions inside two days, none of them run even
once. The one ancestor with a real record — nine successes — was retired early
and everything after it started blank, so ten days of evidence produced a
current version that knows nothing about itself.

Hermetic: CloudStore is built without __init__ and handed a stub cursor, the
same way test_entity_chunks does it, so no database is required.
"""

import contextlib
import json

from cloud.store import CloudStore


class _FakeCursor:
    """Records SQL and hands back queued rows."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.sql = []
        self.params = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        return []


def _store(procedure, rows=None):
    """A store whose only real behaviour is evolve_procedure."""
    store = CloudStore.__new__(CloudStore)
    cursor = _FakeCursor(rows)

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cursor

    store._cursor = _cursor
    store.cursor = cursor
    store.get_procedure_by_id = lambda *a, **k: procedure
    store.get_procedures = lambda *a, **k: []          # nothing to regress against
    store.save_procedure = lambda **kw: (
        store.saved.append(kw) or "new-version-id")
    store.saved = []
    return store


def _procedure(success_count, fail_count=0, version=3):
    return {
        "id": "p1", "name": "deploy", "version": version,
        "success_count": success_count, "fail_count": fail_count,
        "entity_names": ["Railway"], "trigger_condition": "on merge",
        "steps": [{"step": 1, "action": "push", "detail": ""}],
        "metadata": {},
    }


NEW_STEPS = [{"step": 1, "action": "run migrations", "detail": ""},
             {"step": 2, "action": "push", "detail": ""}]


class TestUnprovenVersionsAreRevisedInPlace:
    def test_a_version_that_never_succeeded_is_edited_not_superseded(self):
        store = _store(_procedure(success_count=0))

        returned = store.evolve_procedure("u", "p1", NEW_STEPS, sub_user_id="default")

        assert returned == "p1"                 # same row, not a new version
        assert store.saved == []                # save_procedure never called
        assert any("UPDATE procedures SET steps" in s for s in store.cursor.sql)

    def test_a_failure_alone_does_not_earn_a_version(self):
        """Failures are what trigger revisions. If a failure counted as being
        proven, every revision would mint a version and nothing would change."""
        store = _store(_procedure(success_count=0, fail_count=5))
        assert store.evolve_procedure("u", "p1", NEW_STEPS) == "p1"
        assert store.saved == []

    def test_a_proven_version_still_gets_a_successor(self):
        store = _store(_procedure(success_count=9))

        returned = store.evolve_procedure("u", "p1", NEW_STEPS)

        assert returned == "new-version-id"
        assert store.saved and store.saved[0]["version"] == 4
        assert store.saved[0]["parent_version_id"] == "p1"

    def test_the_proven_version_is_retired_only_when_replaced(self):
        store = _store(_procedure(success_count=9))
        store.evolve_procedure("u", "p1", NEW_STEPS)
        assert any("is_current = FALSE" in s for s in store.cursor.sql)

    def test_an_in_place_revision_is_still_logged(self):
        """The reason a workflow changed is the part worth keeping, so an edit
        writes history even though the version number does not move."""
        store = _store(_procedure(success_count=0))
        store.evolve_procedure("u", "p1", NEW_STEPS, diff={"reason": "cold pool"})

        logged = [i for i, s in enumerate(store.cursor.sql)
                  if "INSERT INTO procedure_evolution" in s]
        assert logged
        params = store.cursor.params[logged[0]]
        assert params[2] == "revised_in_place"
        assert params[4] == params[5] == 3          # version_before == version_after
        assert json.loads(params[3])["reason"] == "cold pool"


class TestQuarantineStillWins:
    def test_a_flagged_revision_takes_the_versioned_path_even_if_unproven(self, monkeypatch):
        """Quarantine needs a row of its own to hold the flagged copy while the
        old version keeps serving. Editing in place would apply the very
        revision the gate just refused."""
        import cloud.regression_gate as gate
        monkeypatch.setattr(gate, "find_regressions",
                            lambda *a, **k: [{"dependent_id": "b",
                                              "dependent_name": "seed",
                                              "broken_preconditions": ["migrate first"],
                                              "broken_orderings": []}])
        store = _store(_procedure(success_count=0))

        returned = store.evolve_procedure("u", "p1", NEW_STEPS)

        assert returned == "new-version-id"
        assert store.saved, "a quarantined revision still needs its own row"
        assert store.saved[0]["is_current"] is False
        assert store.saved[0]["metadata"]["status"] == "needs_review"
        # and the old version must NOT be retired while it is still serving
        assert not any("is_current = FALSE" in s for s in store.cursor.sql)
