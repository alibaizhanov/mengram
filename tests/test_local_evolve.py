"""Local mode, step 2: failure → revision on files, with the regression gate.

The model is a stub that returns whatever JSON the test hands it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.extractor.conversation_extractor import ExtractedProcedure, ExtractionResult  # noqa: E402
from local import LocalStore  # noqa: E402
from local.evolve import evolve_on_failure, read_quarantine  # noqa: E402


class _Model:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, prompt, system="", response_format=None):
        self.prompts.append(prompt)
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


DEPLOY = [{"action": "push to main", "detail": "the webhook does the rest"},
          {"action": "watch the boot log"},
          {"action": "verify /health", "detail": "expect 200 within 60s"}]

FIX = {
    "new_steps": [
        {"step": 1, "action": "push to main", "detail": "the webhook does the rest"},
        {"step": 2, "action": "watch the boot log"},
        {"step": 3, "action": "wait for the pool", "detail": "pool_max reached"},
        {"step": 4, "action": "verify /health", "detail": "expect 200 within 60s"},
    ],
    "new_trigger": None,
    "change_type": "step_added",
    "change_description": "wait for the pool before probing",
    "violated_assumption": "the connection pool was warm when /health was probed",
    "precondition_check": "verify the pool is warm before probing /health",
}


def _store(tmp_path, extra=None) -> LocalStore:
    store = LocalStore(tmp_path / "memory")
    procs = [ExtractedProcedure("Deploy to Railway", "a change lands on main", DEPLOY, ["Railway", "Postgres"])]
    procs += extra or []
    store.add_extraction(ExtractionResult(procedures=procs))
    store.save()
    return store


def test_unknown_procedure_is_an_error(tmp_path):
    assert evolve_on_failure(_store(tmp_path), "nope", "x", _Model(FIX))["status"] == "error"


def test_the_prompt_carries_the_procedure_and_the_failure(tmp_path):
    store = _store(tmp_path)
    m = _Model(FIX)
    evolve_on_failure(store, "Deploy to Railway", "connection refused on /health", m, failed_at_step=3)
    (prompt,) = m.prompts
    assert "Deploy to Railway" in prompt and "3. verify /health" in prompt
    assert "connection refused on /health" in prompt and "Failed at step: 3" in prompt


def test_untested_version_is_revised_in_place(tmp_path):
    store = _store(tmp_path)
    r = evolve_on_failure(store, "Deploy to Railway", "cold pool", _Model(FIX))
    assert r["status"] == "revised_in_place" and r["version"] == 1
    p = store.memory.procedures[0]
    assert [s.action for s in p.steps] == ["push to main", "watch the boot log", "wait for the pool", "verify /health"]
    assert p.preconditions == ["verify the pool is warm before probing /health"]
    assert p.last_failure == "the connection pool was warm when /health was probed"
    assert p.evolution == []


def test_proven_version_gets_a_successor_with_the_record_on_the_lineage(tmp_path):
    store = _store(tmp_path)
    for _ in range(3):
        store.procedure_feedback("Deploy to Railway", success=True)
    store.procedure_feedback("Deploy to Railway", success=False, failed_at_step=3)
    r = evolve_on_failure(store, "Deploy to Railway", "cold pool", _Model(FIX))
    assert r["status"] == "promoted" and r["version"] == 2
    p = store.memory.procedures[0]
    assert (p.success_count, p.fail_count) == (0, 0)
    (rev,) = p.evolution
    assert (rev.version_before, rev.version_after, rev.success_count, rev.fail_count) == (1, 2, 3, 1)
    assert rev.reason == "wait for the pool before probing"
    # inherits a prior from the lineage instead of opening as untested
    assert p.reliability.endswith("expected")
    # unchanged steps keep their record; the new step starts untracked
    by_action = {s.action: s for s in p.steps}
    assert (by_action["push to main"].success_count, by_action["push to main"].fail_count or 0) == (4, 0)
    assert not by_action["wait for the pool"].tracked
    text = (tmp_path / "memory" / "procedures" / "Deploy to Railway.md").read_text()
    assert "- v1 → v2 (" in text and "3✓/1✗): wait for the pool before probing" in text
    assert "last_failure: the connection pool was warm when /health was probed" in text


def test_a_fix_that_breaks_a_dependent_is_quarantined_not_promoted(tmp_path):
    # Two procedures share the database; the fix adds "run migrations first".
    dependent = ExtractedProcedure("Hotfix the API", "a bug in prod", [
        {"action": "push the fix to main"}, {"action": "verify /health on the database api"}],
        ["Postgres"])
    store = _store(tmp_path, extra=[dependent])
    store.procedure_feedback("Deploy to Railway", success=True)
    fix = dict(FIX)
    fix["new_steps"] = [{"step": 1, "action": "run database migrations", "detail": "alembic upgrade head"}] + FIX["new_steps"]
    fix["precondition_check"] = "database migrations applied before push"
    fix["violated_assumption"] = "the database migration had already been applied"
    r = evolve_on_failure(store, "Deploy to Railway", "migration missing", _Model(fix))
    assert r["status"] == "quarantined"
    assert r["regressions"][0]["dependent_name"] == "Hotfix the API"
    p = store.memory.procedures[0]
    assert p.version == 1 and [s.action for s in p.steps][0] == "push to main"   # untouched
    q = read_quarantine(store)
    assert len(q) == 1 and q[0]["procedure"] == "Deploy to Railway"
    assert q[0]["proposed_steps"][0]["action"] == "run database migrations"
    # the surface the gate used is in the file, as frontmatter anyone can edit
    text = (tmp_path / "memory" / "procedures" / "Deploy to Railway.md").read_text()
    assert "entities:\n  - Railway\n  - Postgres" in text
    assert (tmp_path / "memory" / ".mengram" / "quarantine.json").exists()
    # and the folder still validates: the quarantine file is not a memfmt file
    from memfmt.cli import main
    assert main(["validate", str(tmp_path / "memory")]) == 0


def test_garbage_from_the_model_changes_nothing(tmp_path):
    store = _store(tmp_path)
    before = store.memory.procedures[0].steps[:]
    assert evolve_on_failure(store, "Deploy to Railway", "x", _Model("not json"))["status"] == "no_change"
    assert evolve_on_failure(store, "Deploy to Railway", "x", _Model({"new_steps": []}))["status"] == "no_change"
    assert store.memory.procedures[0].steps == before


def test_fenced_json_is_accepted(tmp_path):
    store = _store(tmp_path)
    r = evolve_on_failure(store, "Deploy to Railway", "x", _Model("```json\n" + json.dumps(FIX) + "\n```"))
    assert r["status"] == "revised_in_place"


def test_model_exception_is_reported_not_raised(tmp_path):
    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("no key")
    r = evolve_on_failure(_store(tmp_path), "Deploy to Railway", "x", Boom())
    assert r["status"] == "error" and "no key" in r["error"]
