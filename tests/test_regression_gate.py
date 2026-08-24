"""Cross-procedure regression gate — pure-function tests, no DB/network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.regression_gate import (
    shares_surface, newly_added_preconditions, dependent_lacks_precondition,
    find_regressions, newly_ordered_steps, dependent_violates_order,
)


def proc(id, name, entities=None, steps=None, trigger=None, preconds=None):
    return {
        "id": id, "name": name,
        "entity_names": entities or [],
        "steps": steps or [],
        "trigger_condition": trigger,
        "metadata": {"preconditions": preconds or []},
    }


# --- shared surface -----------------------------------------------------------
def test_shares_surface_by_entity():
    a = proc("A", "deploy", entities=["Railway", "Postgres"])
    b = proc("B", "seed data", entities=["Postgres"])
    assert shares_surface(a, b) is True

def test_shares_surface_by_name_reference():
    a = proc("A", "run migrations", entities=[])
    b = proc("B", "release", steps=[{"step": 1, "action": "call run migrations then push", "detail": ""}])
    assert shares_surface(a, b) is True

def test_no_shared_surface():
    a = proc("A", "deploy", entities=["Railway"])
    b = proc("B", "write blog post", entities=["Ghost"])
    assert shares_surface(a, b) is False

def test_same_procedure_never_shares_with_itself():
    a = proc("A", "deploy", entities=["Railway"])
    assert shares_surface(a, a) is False


# --- newly added preconditions ------------------------------------------------
def test_newly_added_preconditions():
    old = proc("A", "deploy", preconds=["database reachable"])
    new = proc("A", "deploy", preconds=["database reachable", "run migrations before push"])
    assert newly_added_preconditions(old, new) == ["run migrations before push"]

def test_no_new_preconditions():
    old = proc("A", "deploy", preconds=["x"])
    new = proc("A", "deploy", preconds=["x"])
    assert newly_added_preconditions(old, new) == []


# --- dependent satisfaction ---------------------------------------------------
def test_dependent_lacks_precondition():
    b = proc("B", "seed", steps=[{"step": 1, "action": "insert rows", "detail": "into users"}])
    assert dependent_lacks_precondition(b, "run migrations before push") is True

def test_dependent_already_satisfies():
    b = proc("B", "seed", steps=[{"step": 1, "action": "run migrations", "detail": "then insert"}])
    assert dependent_lacks_precondition(b, "run migrations before push") is False

def test_negated_mention_does_not_count_as_satisfying():
    # "no encryption header" contains the tokens but means the opposite
    b = proc("B", "backup", steps=[{"step": 1, "action": "put object", "detail": "no encryption header"}])
    assert dependent_lacks_precondition(b, "set SSE-KMS encryption header on put") is True


# --- end-to-end regression detection ------------------------------------------
def test_regression_detected():
    old = proc("A", "deploy", entities=["Postgres"], preconds=[])
    new = proc("A", "deploy", entities=["Postgres"],
               preconds=["run migrations before push"])
    # B shares the Postgres entity, does not run migrations → should break
    b = proc("B", "seed data", entities=["Postgres"],
             steps=[{"step": 1, "action": "insert seed rows", "detail": ""}])
    regs = find_regressions(old, new, [b])
    assert len(regs) == 1
    assert regs[0]["dependent_name"] == "seed data"
    assert regs[0]["broken_preconditions"] == ["run migrations before push"]

def test_no_regression_when_dependent_satisfies():
    old = proc("A", "deploy", entities=["Postgres"], preconds=[])
    new = proc("A", "deploy", entities=["Postgres"], preconds=["run migrations before push"])
    b = proc("B", "seed", entities=["Postgres"],
             steps=[{"step": 1, "action": "run migrations", "detail": "then seed"}])
    assert find_regressions(old, new, [b]) == []

def test_no_regression_when_no_shared_surface():
    old = proc("A", "deploy", entities=["Postgres"], preconds=[])
    new = proc("A", "deploy", entities=["Postgres"], preconds=["run migrations before push"])
    b = proc("B", "tweet", entities=["Twitter"], steps=[{"step": 1, "action": "post", "detail": ""}])
    assert find_regressions(old, new, [b]) == []

def test_no_regression_when_revision_adds_nothing():
    old = proc("A", "deploy", entities=["Postgres"], preconds=["x"])
    new = proc("A", "deploy", entities=["Postgres"], preconds=["x"])  # steps changed, no new demand
    b = proc("B", "seed", entities=["Postgres"], steps=[{"step": 1, "action": "insert", "detail": ""}])
    assert find_regressions(old, new, [b]) == []

def test_multiple_dependents():
    old = proc("A", "deploy", entities=["Postgres"], preconds=[])
    new = proc("A", "deploy", entities=["Postgres"], preconds=["run migrations before push"])
    b1 = proc("B1", "seed", entities=["Postgres"], steps=[{"step": 1, "action": "insert", "detail": ""}])
    b2 = proc("B2", "backfill", entities=["Postgres"], steps=[{"step": 1, "action": "copy rows", "detail": ""}])
    b3 = proc("B3", "safe", entities=["Postgres"], steps=[{"step": 1, "action": "run migrations first", "detail": ""}])
    regs = find_regressions(old, new, [b1, b2, b3])
    assert {r["dependent_name"] for r in regs} == {"seed", "backfill"}


# --- ordering -----------------------------------------------------------------
# A revision that only moves steps around adds no precondition, so the
# precondition path never fires. That was the blind spot: "run migrations
# before push" is the constraint, whether or not anyone wrote it down.

def test_reorder_alone_is_caught():
    old = proc("A1", "deploy", entities=["Railway"],
               steps=[{"step": 1, "action": "push code", "detail": "to Railway"}])
    new = proc("A2", "deploy", entities=["Railway"],
               steps=[{"step": 1, "action": "run migrations", "detail": ""},
                      {"step": 2, "action": "push code", "detail": "to Railway"}])
    dependent = proc("B", "hotfix", entities=["Railway"],
                     steps=[{"step": 1, "action": "push code", "detail": "to Railway"}])
    assert newly_added_preconditions(old, new) == []      # nothing for the old path
    regressions = find_regressions(old, new, [dependent])
    assert regressions
    assert regressions[0]["broken_orderings"] == [["run migrations", "push code to Railway"]]

def test_a_dependent_that_already_orders_it_is_left_alone():
    old = proc("A1", "deploy", entities=["Railway"],
               steps=[{"step": 1, "action": "push code", "detail": "to Railway"}])
    new = proc("A2", "deploy", entities=["Railway"],
               steps=[{"step": 1, "action": "run migrations", "detail": ""},
                      {"step": 2, "action": "push code", "detail": "to Railway"}])
    dependent = proc("B", "release", entities=["Railway"],
                     steps=[{"step": 1, "action": "run migrations", "detail": ""},
                            {"step": 2, "action": "push code", "detail": "to Railway"}])
    assert find_regressions(old, new, [dependent]) == []

def test_doing_it_afterwards_does_not_count():
    """The prerequisite has to be earlier. Later is the bug, not the fix."""
    dependent = proc("B", "late", entities=["Railway"],
                     steps=[{"step": 1, "action": "push code", "detail": "to Railway"},
                            {"step": 2, "action": "run migrations", "detail": ""}])
    assert dependent_violates_order(dependent, "run migrations", "push code") is True

def test_one_step_doing_both_is_not_a_violation():
    """"register the schema then re-produce" does both. Deciding it got the
    order wrong inside one sentence would be a guess, and a false quarantine."""
    dependent = proc("B", "replay", entities=["Kafka"],
                     steps=[{"step": 1, "action": "register schema with registry then re-produce",
                             "detail": ""}])
    assert dependent_violates_order(
        dependent, "register schema with registry before producing", "produce message") is False

def test_a_dependent_that_never_does_the_action_is_untouched():
    dependent = proc("B", "unrelated", entities=["Railway"],
                     steps=[{"step": 1, "action": "read the logs", "detail": ""}])
    assert dependent_violates_order(dependent, "run migrations", "push code") is False

def test_only_newly_inserted_steps_create_a_constraint():
    """Reshuffling two steps that both already existed is a weaker signal and
    v1 deliberately stays out of it rather than pay in false quarantines."""
    old = proc("A1", "deploy",
               steps=[{"step": 1, "action": "push code", "detail": ""},
                      {"step": 2, "action": "run migrations", "detail": ""}])
    new = proc("A2", "deploy",
               steps=[{"step": 1, "action": "run migrations", "detail": ""},
                      {"step": 2, "action": "push code", "detail": ""}])
    assert newly_ordered_steps(old, new) == []
