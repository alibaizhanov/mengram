"""Local mode, step 1: a memfmt folder with the cloud's rules for changing it.

Hermetic: every test works on a tmp folder. No model, no network — extraction
results are built by hand from the engine's dataclasses, and `add()` uses the
engine's MockLLMClient.
"""

import sys
from pathlib import Path

import memfmt
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.extractor.conversation_extractor import (  # noqa: E402
    ExtractedEntity, ExtractedEpisode, ExtractedFact, ExtractedKnowledge,
    ExtractedProcedure, ExtractedRelation, ExtractionResult, MockLLMClient,
)
from local import LocalStore  # noqa: E402


def _extraction(**kw) -> ExtractionResult:
    return ExtractionResult(**kw)


DEPLOY_STEPS = [
    {"step": 1, "action": "push to main", "detail": "the webhook does the rest"},
    {"step": 2, "action": "watch the boot log"},
    {"step": 3, "action": "verify /health", "detail": "expect 200 within 60s"},
]


def _seed(tmp_path) -> LocalStore:
    store = LocalStore(tmp_path / "memory")
    store.add_extraction(_extraction(
        entities=[
            ExtractedEntity("Ali", "person", [ExtractedFact("prefers Rust for memory safety"),
                                              ExtractedFact("based in Almaty")]),
            ExtractedEntity("Railway", "service", [ExtractedFact("auto-deploys from main")]),
        ],
        relations=[ExtractedRelation("Ali", "Railway", "uses", "since 2024")],
        knowledge=[ExtractedKnowledge("Railway", "command", "deploy command",
                                      "how the service ships", artifact="railway up --detach")],
        episodes=[ExtractedEpisode("deploy failed on a cold pool", context="two workers booted at once",
                                   outcome="rolled back", emotional_valence="negative",
                                   importance=0.8, happened_at="2026-07-30")],
        procedures=[ExtractedProcedure("Deploy to Railway", "a change lands on main", DEPLOY_STEPS, ["Railway"])],
    ))
    store.save()
    return store


# --- files are the memory ---------------------------------------------------

def test_save_writes_a_memfmt_tree_that_loads_back(tmp_path):
    store = _seed(tmp_path)
    root = tmp_path / "memory"
    assert (root / "entities" / "Ali.md").exists()
    assert (root / "procedures" / "Deploy to Railway.md").exists()
    assert (root / "MEMORY.md").exists()
    again = LocalStore(root)
    assert memfmt.canonical(again.memory) == memfmt.canonical(store.memory)


def test_the_folder_passes_memfmt_validate(tmp_path):
    _seed(tmp_path)
    from memfmt.cli import main
    assert main(["validate", str(tmp_path / "memory")]) == 0


def test_stats_and_profile(tmp_path):
    store = _seed(tmp_path)
    s = store.stats()
    assert (s["entities"], s["facts"], s["episodes"], s["procedures"]) == (2, 3, 1, 1)
    prof = store.profile()
    assert "2 entities" in prof and "Ali (person)" in prof


# --- ingestion rules -----------------------------------------------------------

def test_entities_merge_and_facts_dedup(tmp_path):
    store = _seed(tmp_path)
    stats = store.add_extraction(_extraction(entities=[
        ExtractedEntity("ali", "person", [ExtractedFact("Based in Almaty."), ExtractedFact("runs Mengram")]),
    ]))
    ali = store._entity("Ali")
    assert stats["entities_created"] == 0 and stats["entities_updated"] == 1
    assert stats["facts_added"] == 1
    assert ali.facts == ["prefers Rust for memory safety", "based in Almaty", "runs Mengram"]


def test_relations_and_knowledge_do_not_duplicate(tmp_path):
    store = _seed(tmp_path)
    store.add_extraction(_extraction(
        relations=[ExtractedRelation("Ali", "Railway", "uses")],
        knowledge=[ExtractedKnowledge("Railway", "command", "Deploy command", "again")],
    ))
    ali, railway = store._entity("Ali"), store._entity("Railway")
    assert len(ali.relations) == 1 and ali.relations[0].detail == "since 2024"
    assert len(railway.knowledge) == 1 and railway.knowledge[0].artifact == "railway up --detach"


def test_episode_importance_maps_to_the_format_and_dedups(tmp_path):
    store = _seed(tmp_path)
    ep = store.memory.episodes[0]
    assert ep.importance == 4 and ep.valence == "negative" and ep.happened == "2026-07-30"
    stats = store.add_extraction(_extraction(episodes=[
        ExtractedEpisode("deploy failed on a cold pool", happened_at="2026-07-30")]))
    assert stats["episodes_saved"] == 0 and len(store.memory.episodes) == 1


def test_untested_near_duplicate_is_refreshed_in_place(tmp_path):
    store = _seed(tmp_path)
    new_steps = DEPLOY_STEPS + [{"step": 4, "action": "check the pool"}]
    stats = store.add_extraction(_extraction(procedures=[
        ExtractedProcedure("Deploying to Railway", "after merge", new_steps)]))
    assert stats["procedures"] == {"created": 0, "refreshed": 1, "kept": 0}
    assert len(store.memory.procedures) == 1
    p = store.memory.procedures[0]
    assert p.name == "Deploy to Railway"            # keeps its existing name
    assert [s.action for s in p.steps][-1] == "check the pool"
    assert p.trigger == "after merge"


def test_proven_near_duplicate_is_kept_untouched(tmp_path):
    store = _seed(tmp_path)
    store.procedure_feedback("Deploy to Railway", success=True)
    stats = store.add_extraction(_extraction(procedures=[
        ExtractedProcedure("Railway deploy", "whatever", [{"action": "something else"}])]))
    assert stats["procedures"] == {"created": 0, "refreshed": 0, "kept": 1}
    p = store.memory.procedures[0]
    assert [s.action for s in p.steps][0] == "push to main" and p.success_count == 1


def test_unrelated_procedure_is_created(tmp_path):
    store = _seed(tmp_path)
    stats = store.add_extraction(_extraction(procedures=[
        ExtractedProcedure("Rotate API keys", "quarterly", [{"action": "open dashboard"}, {"action": "revoke"}])]))
    assert stats["procedures"]["created"] == 1 and len(store.memory.procedures) == 2


def test_counters_the_model_emitted_are_dropped(tmp_path):
    store = LocalStore(tmp_path / "m")
    store.add_extraction(_extraction(procedures=[ExtractedProcedure("p", "", [
        {"action": "x", "success_count": 99, "fail_count": 0}])]))
    assert not store.memory.procedures[0].steps[0].tracked


# --- retrieval ------------------------------------------------------------------

def test_search_ranks_by_overlap_across_kinds(tmp_path):
    store = _seed(tmp_path)
    hits = store.search("why did the railway deploy fail on the pool")
    kinds = [h["type"] for h in hits]
    assert "episode" in kinds and "procedure" in kinds and "entity" in kinds
    assert all(h["score"] == 2 for h in hits)          # railway+deploy / deploy+pool / railway+deploy
    assert store.search("cold pool rolled back")[0]["type"] == "episode"
    assert store.search("") == [] and store.search("zzz qqq") == []


def test_recall_is_a_readable_context_block(tmp_path):
    store = _seed(tmp_path)
    text = store.recall("deploy railway")
    assert text.startswith("[Mengram Memory")
    assert "Workflow: Deploy to Railway (v1, untested)" in text
    assert "1. push to main — the webhook does the rest" in text


def test_procedures_dict_shape_is_what_the_policy_gate_reads(tmp_path):
    store = _seed(tmp_path)
    (p,) = store.procedures()
    assert p["reliability"] == "untested" and p["trigger_condition"] == "a change lands on main"
    assert p["steps"][0] == {"action": "push to main", "detail": "the webhook does the rest"}
    from cloud import policy
    assert policy.decide(p, "git push origin main")["decision"] == "ask"
    assert store.procedures(query="railway")[0]["name"] == "Deploy to Railway"


# --- outcomes -------------------------------------------------------------------

def test_feedback_credits_the_steps_that_ran(tmp_path):
    store = _seed(tmp_path)
    store.procedure_feedback("Deploy to Railway", success=True)
    d = store.procedure_feedback("deploy to railway", success=False, failed_at_step=3,
                                 reason="probed /health before the pool was up")
    assert (d["success_count"], d["fail_count"]) == (1, 1)
    assert d["reliability"] == "50% reliable"
    steps = d["steps"]
    assert (steps[0]["success_count"], steps[0]["fail_count"]) == (2, 0)
    assert (steps[2]["success_count"], steps[2]["fail_count"]) == (1, 1)
    assert d["last_failure"] == "probed /health before the pool was up"
    assert d["last_failed"] is not None
    # and it is on disk, in the format
    text = (tmp_path / "memory" / "procedures" / "Deploy to Railway.md").read_text()
    assert "success_count: 1" in text and "fail_count: 1" in text
    assert "1. push to main — the webhook does the rest (2✓/0✗)" in text
    assert "last_failure: probed /health before the pool was up" in text


def test_feedback_on_unknown_procedure_is_an_error_not_a_crash(tmp_path):
    store = _seed(tmp_path)
    assert "error" in store.procedure_feedback("nope", success=True)


def test_failure_without_a_step_attributes_nothing_to_steps(tmp_path):
    store = _seed(tmp_path)
    d = store.procedure_feedback("Deploy to Railway", success=False)
    assert d["fail_count"] == 1 and all("success_count" not in s for s in d["steps"])


# --- the whole loop with a model ---------------------------------------------------

def test_add_extracts_with_the_users_model_and_writes(tmp_path):
    store = LocalStore(tmp_path / "memory")
    stats = store.add("I work at Uzum Bank as a backend developer", MockLLMClient())
    assert stats["entities_created"] >= 1
    assert (tmp_path / "memory" / "MEMORY.md").exists()
    assert store.existing_context().startswith("Known entities")
