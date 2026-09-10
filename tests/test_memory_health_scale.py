"""Retrieval health must not read two score scales as one.

`usage_log.query_score` holds a rerank/cosine similarity in 0..1 for some
searches and a raw RRF score for others, and RRF tops out near 0.05 by
construction. Averaging the two and comparing the result to a cosine-shaped
threshold called production healthy-or-not at random: 286 of 304 scored
searches sat "below 0.4" with a mean of 0.075, 23 of 26 accounts were marked
critical, and the Monday digest was ready to tell every one of them that their
memory needed attention.

The per-search label in `cloud/api.py` had already been made scale-aware. The
aggregation had not. These tests pin the cut points to each other.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store._health import HealthMixin


def _status(searches, no_match):
    """The status the aggregation would assign for this share of empty results."""
    share = no_match / searches
    if share >= HealthMixin._CRITICAL_NO_MATCH_SHARE:
        return "critical"
    if share >= HealthMixin._DEGRADED_NO_MATCH_SHARE:
        return "degraded"
    return "healthy"


def test_the_cut_points_match_the_per_search_label():
    """Both files decide the same question; they must not drift apart.

    `_quality_label` in cloud/api.py: >= 0.3 strong, >= 0.02 weak, else none.
    """
    api = Path(__file__).parent.parent / "cloud" / "api.py"
    body = api.read_text()
    assert "if top_score >= 0.3:" in body
    assert "if top_score >= 0.02:" in body
    assert HealthMixin._STRONG_SCORE == 0.30
    assert HealthMixin._WEAK_SCORE == 0.02


def test_a_low_score_is_not_by_itself_a_failure():
    """An RRF score of 0.05 is a good match, not a broken one."""
    assert HealthMixin._LOW_QUALITY_SCORE == HealthMixin._WEAK_SCORE
    assert HealthMixin._LOW_QUALITY_SCORE < 0.05


def test_healthy_when_searches_find_something():
    assert _status(20, 0) == "healthy"
    assert _status(20, 5) == "healthy"      # 25% empty


def test_degraded_when_a_large_minority_find_nothing():
    assert _status(20, 6) == "degraded"     # 30%
    assert _status(20, 11) == "degraded"    # 55%


def test_critical_only_when_most_searches_find_nothing():
    assert _status(20, 12) == "critical"    # 60%
    assert _status(20, 20) == "critical"


def test_the_production_sample_is_no_longer_uniformly_critical():
    """The measurement that started this: mean 0.075 over 304 searches.

    Under the old rule every one of those accounts was critical. Under the new
    one the verdict depends on how many searches came back empty, which is the
    thing a user would actually recognise as a problem.
    """
    # A user whose scores are all RRF-scale but who finds what they look for.
    assert _status(304, 10) == "healthy"
    # A user whose searches genuinely return nothing.
    assert _status(304, 250) == "critical"


def test_thresholds_are_ordered_and_are_shares_not_scores():
    assert 0 < HealthMixin._DEGRADED_NO_MATCH_SHARE < HealthMixin._CRITICAL_NO_MATCH_SHARE <= 1
    assert HealthMixin._WEAK_SCORE < HealthMixin._STRONG_SCORE < 1


def test_the_old_score_thresholds_are_gone():
    """They classified on a quantity that does not exist: a mean over two scales."""
    assert not hasattr(HealthMixin, "_HEALTH_THRESHOLD_HEALTHY")
    assert not hasattr(HealthMixin, "_HEALTH_THRESHOLD_DEGRADED")
