"""What the contradiction pass may archive. E4 found the answers to three
support questions in the archive; these are those cases and their mirrors."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store._supersede import why_not_supersede


def test_a_vaguer_restatement_never_archives_a_value():
    assert why_not_supersede("has loyalty number LR-11560", "has a loyalty number with the Assistant")
    assert why_not_supersede("uses Visa ending 4471 for payments", "uses a Visa for payments")
    assert why_not_supersede("service is pinned to Python 3.12", "service is pinned to a Python version")


def test_a_new_value_may_replace_the_old_one():
    assert why_not_supersede("has loyalty number LR-11560", "has loyalty number LR-99001") is None
    assert why_not_supersede("service is pinned to Python 3.11", "service is pinned to Python 3.12") is None
    assert why_not_supersede("lives in Almaty", "relocated to Dubai") is None
    assert why_not_supersede("uses React", "switched to Svelte") is None


def test_one_occasion_does_not_override_a_standing_fact():
    assert why_not_supersede("always needs one child seat", "does not need a child seat for the business trip")
    assert why_not_supersede("always rents an automatic car", "took a manual this time")
    assert why_not_supersede("prefers the airport desk", "picked up at the harbour office for the spring trip")
    # a standing change does override
    assert why_not_supersede("always needs one child seat", "no longer needs a child seat") is None


def test_a_one_off_use_does_not_override_the_default():
    assert why_not_supersede("uses Visa ending 4471 for payments", "used Mastercard ending 9083 for a hotel")
    assert why_not_supersede("usually takes an estate", "rented a convertible last summer")


def test_someone_elses_fact_never_archives_the_persons():
    assert why_not_supersede("has loyalty number LR-11560", "wife's loyalty number is LR-48231")
    assert why_not_supersede("is allergic to kiwi", "has a colleague who is allergic to gluten")
    assert why_not_supersede("has a wife with loyalty number LR-48231", "wife's loyalty number is LR-50000") is None


# ---- find vs archive (audit 2026-10-23) --------------------------------------
# The pipeline used to archive old facts before the deny policy, the salience
# gate and the save had run on the new ones; a new fact dropped by any of them
# left the old fact archived and superseded by a fact that was never written.
# Finding the pairs now writes nothing; archiving is a separate step.
import json as _json
from contextlib import contextmanager

from cloud.store._search import SearchMixin


class _Cur:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        self.db.sql.append((sql, params))

    def fetchall(self):
        return [{"content": c} for c in self.db.live]


class _Store(SearchMixin):
    def __init__(self, live):
        self.live = list(live)
        self.sql = []

    @contextmanager
    def _cursor(self, dict_cursor=False):
        yield _Cur(self)

    def writes(self):
        return [s for s, _ in self.sql if s.lstrip().upper().startswith("UPDATE")]


class _LLM:
    def __init__(self, pairs):
        self.pairs = pairs

    def complete(self, prompt, **kw):
        return _json.dumps({"pairs": self.pairs})


def test_finding_superseded_facts_writes_nothing():
    store = _Store(["lives in Almaty"])
    pairs = store.find_superseded_facts(
        "e1", ["relocated to Dubai"], _LLM([{"old": "lives in Almaty", "new": "relocated to Dubai"}]))
    assert pairs == [("lives in Almaty", "relocated to Dubai")]
    assert store.writes() == []


def test_finding_keeps_the_guards():
    store = _Store(["has loyalty number LR-11560"])
    pairs = store.find_superseded_facts(
        "e1", ["has a loyalty number"],
        _LLM([{"old": "has loyalty number LR-11560", "new": "has a loyalty number"}]))
    assert pairs == []


def test_archiving_writes_one_update_per_pair():
    store = _Store(["lives in Almaty"])
    archived = store.archive_superseded_facts("e1", [("lives in Almaty", "relocated to Dubai")])
    assert archived == ["lives in Almaty"]
    assert len(store.writes()) == 1
    _, params = store.sql[-1]
    assert params == ("relocated to Dubai", "e1", "lives in Almaty")


def test_the_combined_call_still_archives():
    store = _Store(["lives in Almaty"])
    archived = store.archive_contradicted_facts(
        "e1", ["relocated to Dubai"], _LLM([{"old": "lives in Almaty", "new": "relocated to Dubai"}]))
    assert archived == ["lives in Almaty"]
    assert len(store.writes()) == 1
