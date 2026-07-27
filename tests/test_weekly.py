"""Weekly report renderer tests — pure formatting, no network/DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli


FULL = {
    "facts_learned": 47, "facts_prev_week": 35,
    "procedures_learned": 3,
    "latest_version_bump": {"name": "deploy-railway", "version": 5},
    "recalls_served": 128,
    "prevented": [
        {"name": "psycopg2 pool deadlock", "fail_count": 3, "last_bitten": "2026-04-12"},
        {"name": "wrong table (api_calls)", "fail_count": 1, "last_bitten": "2026-07-02"},
    ],
}

EMPTY = {
    "facts_learned": 0, "facts_prev_week": 0, "procedures_learned": 0,
    "latest_version_bump": None, "recalls_served": 0, "prevented": [],
}


def test_width_never_exceeds_58():
    out = cli._render_weekly(FULL, "week of Jul 21–27", color=False)
    assert max(len(l) for l in out.splitlines()) <= 58


def test_box_lines_are_exactly_58():
    out = cli._render_weekly(FULL, "week of Jul 21–27", color=False)
    box = [l for l in out.splitlines() if l.startswith("│")]
    assert box and all(len(l) == 58 for l in box)


def test_hero_metric_and_dates():
    out = cli._render_weekly(FULL, "w", color=False)
    assert "REPEATED MISTAKES PREVENTED" in out
    assert "last bitten: Apr 12" in out
    assert "deploy-railway v4 → v5" in out
    assert "▲ 12 vs last week" in out


def test_zero_state_is_a_clean_week():
    out = cli._render_weekly(EMPTY, "first week", color=False)
    assert "0 — clean week" in out
    assert max(len(l) for l in out.splitlines()) <= 58


def test_missing_last_bitten_falls_back_to_fail_count():
    stats = dict(FULL, prevented=[{"name": "x", "fail_count": 4, "last_bitten": None}])
    out = cli._render_weekly(stats, "w", color=False)
    assert "4 past failures" in out


def test_color_mode_contains_ansi():
    out = cli._render_weekly(FULL, "w", color=True)
    assert "\033[1;33m" in out and "\033[0m" in out
