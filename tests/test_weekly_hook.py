"""Once-a-week SessionStart report gating — no network, pure logic."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli


class FakeMem:
    def __init__(self, stats):
        self._stats = stats
    def weekly_stats(self, user_id="default"):
        return self._stats


FULL = {"facts_learned": 47, "procedures_learned": 3, "recalls_served": 128,
        "prevented": [{"name": "psycopg2 pool deadlock", "last_bitten": "2026-04-12"}]}
EMPTY = {"facts_learned": 0, "procedures_learned": 0, "recalls_served": 0, "prevented": []}


def _isolate(tmp, name):
    cli._weekly_state_path = lambda: Path(tmp) / name


def test_shows_once_then_gated(monkeypatch=None):
    tmp = tempfile.mkdtemp()
    _isolate(tmp, "a.json")
    m = FakeMem(FULL)
    first = cli._maybe_weekly_message(m, "u1")
    assert first and "Mengram" in first and "prevented: 1" in first.lower()
    assert cli._maybe_weekly_message(m, "u1") is None  # same week → gated


def test_empty_week_no_nag():
    tmp = tempfile.mkdtemp()
    _isolate(tmp, "b.json")
    assert cli._maybe_weekly_message(FakeMem(EMPTY), "u2") is None


def test_failure_is_silent():
    tmp = tempfile.mkdtemp()
    _isolate(tmp, "c.json")

    class Boom:
        def weekly_stats(self, user_id="default"):
            raise RuntimeError("quota exceeded")

    assert cli._maybe_weekly_message(Boom(), "u3") is None


def test_per_user_isolation():
    tmp = tempfile.mkdtemp()
    _isolate(tmp, "d.json")
    m = FakeMem(FULL)
    assert cli._maybe_weekly_message(m, "alice") is not None
    assert cli._maybe_weekly_message(m, "bob") is not None   # different user, own gate
    assert cli._maybe_weekly_message(m, "alice") is None     # alice already shown
