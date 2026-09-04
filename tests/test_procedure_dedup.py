"""Write-time dedup of extracted procedures.

Extraction names the same workflow a little differently on every run, and the
unique constraint only catches an exact name. One user reached 450 current
procedures that way, with near-duplicates competing in every search faster
than the curator (which sees 50 at a time) could merge them.

Hermetic: CloudStore without __init__ and a stub cursor, as in
test_upsert_lifecycle.py.
"""

import contextlib

from cloud.store import (
    CloudStore, procedure_similarity, is_near_duplicate_procedure,
    _proc_name_tokens,
)


class _FakeCursor:
    def __init__(self, fetchall_results=None, fetchone_results=None):
        self._alls = list(fetchall_results or [])
        self._ones = list(fetchone_results or [])
        self.sql = []
        self.params = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)

    def fetchall(self):
        return self._alls.pop(0) if self._alls else []

    def fetchone(self):
        return self._ones.pop(0) if self._ones else None


def _store(cursor):
    store = CloudStore.__new__(CloudStore)

    @contextlib.contextmanager
    def _cursor(dict_cursor=False):
        yield cursor

    store._cursor = _cursor
    return store


STEPS = [{"action": "push to main"}, {"action": "watch the boot log"},
         {"action": "verify /health", "detail": "expect 200"}]


def _row(name, s=0, f=0, steps=STEPS, id="p-existing"):
    return {"id": id, "name": name, "steps": steps, "success_count": s, "fail_count": f}


# --- the similarity itself --------------------------------------------------

def test_tokens_drop_glue_and_stem():
    assert _proc_name_tokens("Deploying the backend to Railway") == {"deploy", "backend", "railway"}
    assert _proc_name_tokens("deploys") == _proc_name_tokens("deployed") == {"deploy"}


def test_renamed_same_workflow_is_a_duplicate():
    for other in ("Deploying to Railway", "Railway deploy", "deploy to railway (v2)"):
        n, st = procedure_similarity("Deploy to Railway", STEPS, other, STEPS)
        assert is_near_duplicate_procedure(n, st), other


def test_similar_name_different_steps_is_not():
    docs_steps = [{"action": "build the docs"}, {"action": "upload to S3"}]
    n, st = procedure_similarity("Deploy backend", STEPS, "Deploy docs", docs_steps)
    assert not is_near_duplicate_procedure(n, st)


def test_similar_name_same_steps_is():
    n, st = procedure_similarity("Deploy backend service", STEPS, "Deploy backend", STEPS)
    assert is_near_duplicate_procedure(n, st)


def test_unrelated_workflows_are_not():
    n, st = procedure_similarity("Rotate API keys", [{"action": "open dashboard"}],
                                 "Deploy to Railway", STEPS)
    assert not is_near_duplicate_procedure(n, st)


# --- the store method ---------------------------------------------------------

def test_no_candidates_creates_as_before():
    cur = _FakeCursor(fetchall_results=[[]])
    store = _store(cur)
    store.save_procedure = lambda **kw: "p-new"
    assert store.save_extracted_procedure("u", "Deploy to Railway", steps=STEPS) == ("p-new", "created")


def test_exact_name_is_left_to_the_unique_constraint():
    # Same name → not a near-duplicate; save_procedure's ON CONFLICT handles it.
    cur = _FakeCursor(fetchall_results=[[_row("deploy to railway", s=5)]])
    store = _store(cur)
    store.save_procedure = lambda **kw: "p-existing"
    assert store.save_extracted_procedure("u", "Deploy to Railway", steps=STEPS) == ("p-existing", "created")


def test_proven_duplicate_is_kept_untouched():
    cur = _FakeCursor(fetchall_results=[[_row("Deploy to Railway", s=11, f=1)]])
    store = _store(cur)
    store.save_procedure = lambda **kw: (_ for _ in ()).throw(AssertionError("must not insert"))
    pid, action = store.save_extracted_procedure("u", "Deploying to Railway", steps=STEPS)
    assert (pid, action) == ("p-existing", "kept")
    assert not any("UPDATE procedures" in q for q in cur.sql)


def test_untested_duplicate_is_refreshed_in_place():
    cur = _FakeCursor(fetchall_results=[[_row("Deploy to Railway")]])
    store = _store(cur)
    store.save_procedure = lambda **kw: (_ for _ in ()).throw(AssertionError("must not insert"))
    new_steps = STEPS + [{"action": "check the pool"}]
    pid, action = store.save_extracted_procedure(
        "u", "Deploying to Railway", trigger_condition="after merge",
        steps=new_steps, entity_names=["Railway"])
    assert (pid, action) == ("p-existing", "refreshed")
    upd = [q for q in cur.sql if "UPDATE procedures" in q]
    assert len(upd) == 1
    assert "SET steps = %s::jsonb" in upd[0]
    assert "COALESCE(%s, trigger_condition)" in upd[0]
    assert "WHERE id = %s AND user_id = %s AND sub_user_id = %s" in upd[0]
    params = cur.params[-1]
    assert params[-3:] == ("p-existing", "u", "default")


def test_best_candidate_wins_when_several_match():
    rows = [_row("Deploy backend", steps=STEPS, id="weaker"),
            _row("Deploying to Railway", steps=STEPS, id="closer")]
    cur = _FakeCursor(fetchall_results=[rows])
    store = _store(cur)
    dup = store.find_near_duplicate_procedure("u", "Deploy to Railway", STEPS)
    assert dup["id"] == "closer"


def test_candidates_are_scoped_to_user_and_current():
    cur = _FakeCursor(fetchall_results=[[]])
    store = _store(cur)
    store.find_near_duplicate_procedure("u", "x", STEPS, sub_user_id="s")
    (q,) = cur.sql
    assert "WHERE user_id = %s AND sub_user_id = %s AND is_current = TRUE" in q
    assert cur.params[0] == ("u", "s")
