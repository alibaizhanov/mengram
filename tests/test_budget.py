"""A recall that fits, and says what it cut.

The integrator's number was 4,500 tokens of history per request. `max_tokens`
cuts search results in rank order to a budget; these tests hold that the cut
is in rank order, that nothing lower-ranked sneaks in because it is short,
that the report is honest, and that the SDK passes the budget through.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from cloud import budget
from cloud.client import CloudMemory


def _ent(name, facts, score=0.9, **extra):
    return {"entity": name, "type": "technology", "score": score, "facts": facts,
            "relations": extra.get("relations", []), "knowledge": extra.get("knowledge", [])}


def test_estimate_is_rough_but_never_zero_for_text():
    assert budget.estimate_tokens("") == 0
    assert budget.estimate_tokens("a") == 1
    assert 8 <= budget.estimate_tokens("Railway auto-deploys from the main branch.") <= 14
    # non-Latin text costs more per character, as it does in real tokenizers
    assert budget.estimate_tokens("Деплой идёт с ветки main") > budget.estimate_tokens("Deploy goes from main")


def test_cut_is_in_rank_order_fact_by_fact():
    results = [
        _ent("Railway", ["auto-deploys from main", "uses gunicorn with 1 worker", "DATABASE_URL on port 5432"]),
        _ent("Redis", ["cache for quota counters"], score=0.7),
        _ent("Postgres", ["session mode pooler"], score=0.6),
    ]
    kept, report = budget.fit_search(results, 24)
    # header (Railway (technology)) ~5 + fact1 ~6 + fact2 ~7 = 18; fact3 (~7) breaks it
    assert [e["entity"] for e in kept] == ["Railway"]
    assert kept[0]["facts"] == ["auto-deploys from main", "uses gunicorn with 1 worker"]
    assert report["kept"] == {"entities": 1, "facts": 2, "episodes": 0, "procedures": 0, "chunks": 0}
    assert report["dropped"]["facts"] == 3 and report["dropped"]["entities"] == 2
    assert report["used_tokens"] <= 24 and report["method"] == "estimate"


def test_a_short_lower_ranked_entity_does_not_sneak_in():
    results = [_ent("Long entity name with many words", ["a fact " * 10]), _ent("Io", ["x"], score=0.1)]
    kept, report = budget.fit_search(results, 12)
    assert [e["entity"] for e in kept] == ["Long entity name with many words"]
    assert kept[0]["facts"] == [] and report["dropped"]["entities"] == 1


def test_everything_fits_when_the_budget_is_large():
    results = [_ent("Railway", ["a", "b"], relations=[{"type": "uses", "target": "Redis"}],
                    knowledge=[{"title": "runbook", "content": "restart the worker"}])]
    kept, report = budget.fit_search(results, 5000)
    assert kept == results
    assert sum(report["dropped"].values()) == 0


def test_search_all_spends_one_budget_across_sections_in_order():
    result = {
        "semantic": [_ent("Railway", ["auto-deploys from main"])],
        "episodic": [{"summary": "Deployed to Railway", "outcome": "health check passed"},
                     {"summary": "Second deploy " * 20}],
        "procedural": [{"name": "Deploy", "steps": [{"step": 1, "action": "push", "detail": "to main"}]}],
        "chunks": [{"content": "raw chunk " * 50}],
        "results": [],
    }
    result["results"] = [dict(result["semantic"][0], memory_type="semantic"),
                         dict(result["episodic"][0], memory_type="episodic"),
                         dict(result["episodic"][1], memory_type="episodic")]
    cut, report = budget.fit_search_all(result, 40)
    assert [e["entity"] for e in cut["semantic"]] == ["Railway"]
    assert [e["summary"] for e in cut["episodic"]] == ["Deployed to Railway"]
    assert report["dropped"]["episodes"] == 1
    # a section is cut at its first item that does not fit, but a later, cheaper
    # section still gets what is left: the short procedure fits, the big chunk does not
    assert [p["name"] for p in cut["procedural"]] == ["Deploy"] and report["kept"]["procedures"] == 1
    assert cut["chunks"] == [] and report["dropped"]["chunks"] == 1
    # the merged ranking agrees with the sections
    assert [x.get("entity") or x.get("summary") for x in cut["results"]] == ["Railway", "Deployed to Railway"]
    assert cut["result_quality"] if "result_quality" in result else True


def _resp(payload):
    class R:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()
    return R()


@patch("urllib.request.urlopen")
def test_sdk_passes_the_budget_and_keeps_the_report(mock_urlopen):
    mock_urlopen.return_value = _resp({"results": [{"entity": "Railway", "facts": ["x"]}],
                                       "budget": {"max_tokens": 300, "used_tokens": 12}})
    m = CloudMemory("om-k")
    out = m.search("deploy", max_tokens=300)
    sent = json.loads(mock_urlopen.call_args[0][0].data)
    assert sent["max_tokens"] == 300
    assert out[0]["entity"] == "Railway" and m.last_budget["used_tokens"] == 12

    mock_urlopen.return_value = _resp({"semantic": [], "budget": {"max_tokens": 800}})
    m.search_all("deploy", max_tokens=800)
    sent = json.loads(mock_urlopen.call_args[0][0].data)
    assert sent["max_tokens"] == 800


@patch("urllib.request.urlopen")
def test_sdk_sends_no_budget_unless_asked(mock_urlopen):
    mock_urlopen.return_value = _resp({"results": []})
    CloudMemory("om-k").search("deploy")
    assert "max_tokens" not in json.loads(mock_urlopen.call_args[0][0].data)
