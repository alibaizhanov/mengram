"""Profile and rules generation on a reasoning model (gpt-5.4-mini in prod).

Hidden reasoning counts against max_completion_tokens; at the default effort
a 500-token budget could be spent before any text, and the empty reply then
crashed on `.strip()` of None (audit 2026-10-23)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud.store._profile import _completion_budget  # noqa: E402


def test_reasoning_models_get_low_effort_and_room_for_reasoning():
    kw = _completion_budget("gpt-5.4-mini", 500)
    assert kw == {"max_completion_tokens": 2000, "reasoning_effort": "low"}
    assert "temperature" not in kw


def test_other_models_keep_the_plain_budget():
    assert _completion_budget("gpt-4o-mini", 500) == {"max_completion_tokens": 500, "temperature": 1}
