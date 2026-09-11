"""Concurrent local sessions must preserve facts and measured outcomes."""

import multiprocessing
from pathlib import Path

import memfmt
import pytest

from engine.extractor.conversation_extractor import (
    ExtractedEntity, ExtractedFact, ExtractedProcedure, ExtractionResult,
)
from local import LocalStore
from local.persistence import ConcurrentWriteError, atomic_write


def _seed(root):
    store = LocalStore(root)
    store.add_extraction(ExtractionResult(
        entities=[ExtractedEntity("Ali", "person", [ExtractedFact("original fact")])],
        procedures=[ExtractedProcedure("Deploy", "release", [{"action": "push code"}])],
    ))
    store.save()
    return store


def _session(root, barrier, number):
    store = LocalStore(root)
    barrier.wait(timeout=20)  # all writers start from the same snapshot
    for i in range(3):
        store.memory.entities[0].facts.append(f"session {number} fact {i}")
        store.save()
        store.procedure_feedback("Deploy", success=True)
        store.step_outcome("Deploy", step=1, success=False, reason="test failure")


def test_separate_processes_keep_facts_and_all_outcomes(tmp_path):
    root = tmp_path / "memory"
    _seed(root)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(3)
    processes = [ctx.Process(target=_session, args=(root, barrier, i)) for i in range(3)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=25)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    final = LocalStore(root)
    assert set(final.memory.entities[0].facts) == {"original fact"} | {
        f"session {n} fact {i}" for n in range(3) for i in range(3)}
    proc = final.procedures()[0]
    assert proc["success_count"] == 9
    assert proc["fail_count"] == 0
    assert proc["steps"][0]["success_count"] == 9
    assert proc["steps"][0]["fail_count"] == 9


def test_two_new_sessions_merge_the_same_new_entity(tmp_path):
    a, b = LocalStore(tmp_path), LocalStore(tmp_path)
    a.memory.entities.append(memfmt.Entity(name="Ali", facts=["first fact"]))
    b.memory.entities.append(memfmt.Entity(name="Ali", facts=["second fact"]))
    a.save()
    b.save()
    again = LocalStore(tmp_path)
    assert len(again.memory.entities) == 1
    assert set(again.memory.entities[0].facts) == {"first fact", "second fact"}
    # Saving again must not reapply additions or overwrite the combined view.
    a.save()
    assert memfmt.canonical(a.memory) == memfmt.canonical(again.memory)


def test_stale_save_keeps_another_sessions_new_records(tmp_path):
    a = _seed(tmp_path)
    b = LocalStore(tmp_path)
    a.memory.entities.append(memfmt.Entity(name="Railway", facts=["hosts the API"]))
    a.memory.episodes.append(memfmt.Episode(summary="deployment completed"))
    a.save()
    b.memory.entities[0].facts.append("another fact")
    b.save()
    assert len(LocalStore(tmp_path).memory.entities) == 2
    assert LocalStore(tmp_path).memory.episodes[0].summary == "deployment completed"


def test_extraction_rechecks_procedures_after_the_model_returns(tmp_path):
    store = _seed(tmp_path)

    class Model:
        def complete(self, *args, **kwargs):
            LocalStore(tmp_path).procedure_feedback("Deploy", success=True)
            return '{"entities": [], "procedures": [{"name": "Deploy", "steps": [{"action": "delete everything"}]}]}'

    result = store.add("remember this deployment", Model())
    assert result["procedures"]["kept"] == 1
    proc = LocalStore(tmp_path).procedures()[0]
    assert proc["success_count"] == 1
    assert proc["steps"][0]["action"] == "push code"


def test_incompatible_fact_replacements_fail_without_overwriting(tmp_path):
    a = _seed(tmp_path)
    b = LocalStore(tmp_path)
    a.memory.entities[0].facts = ["first replacement"]
    b.memory.entities[0].facts = ["second replacement"]
    a.save()
    with pytest.raises(ConcurrentWriteError, match="reload"):
        b.save()
    assert LocalStore(tmp_path).memory.entities[0].facts == ["first replacement"]
    assert b.memory.entities[0].facts == ["second replacement"]


@pytest.mark.parametrize("step_only", [True, False])
def test_outcomes_cannot_be_applied_to_a_concurrently_revised_procedure(tmp_path, step_only):
    a = _seed(tmp_path)
    b = LocalStore(tmp_path)
    a.memory.procedures[0].steps[0].action = "verify the release"
    a.save()
    with pytest.raises(ConcurrentWriteError, match="Procedure changed"):
        if step_only:
            b.step_outcome("Deploy", step=1, success=True)
        else:
            b.procedure_feedback("Deploy", success=True)
    proc = LocalStore(tmp_path).procedures()[0]
    assert proc["success_count"] == 0
    assert "success_count" not in proc["steps"][0]


def test_failed_replacement_preserves_complete_original_file(tmp_path, monkeypatch):
    store = _seed(tmp_path)
    path = tmp_path / "entities" / "Ali.md"
    original = path.read_bytes()
    store.memory.entities[0].facts.append("new fact")

    def fail_replace(source, target):
        assert Path(source).parent == Path(target).parent
        assert b"new fact" in Path(source).read_bytes()
        assert path.read_bytes() == original
        raise OSError("simulated disk error")

    monkeypatch.setattr("local.persistence.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk error"):
        store.save()
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


def test_atomic_replacement_keeps_existing_permissions(tmp_path):
    path = tmp_path / "private.md"
    path.write_text("old")
    path.chmod(0o600)
    atomic_write(path, "новые данные")
    assert path.read_text() == "новые данные"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fact,query", [
    ("Люблю программирование", "ПРОГРАММИРОВАНИЕ"),
    ("Настроили резервирование", "резервирование"),
    ("Prefiero programación", "PROGRAMACIÓN"),
    ("Besucht das Café", "Cafe\u0301"),
])
def test_unicode_search_and_recall(tmp_path, fact, query):
    store = LocalStore(tmp_path)
    store.memory.entities.append(memfmt.Entity(name="User", facts=[fact]))
    store.save()
    again = LocalStore(tmp_path)
    assert again.search(query)[0]["facts"] == [fact]
    assert fact in again.recall(query)


def test_russian_search_finds_events_and_procedures(tmp_path):
    store = LocalStore(tmp_path)
    store.memory.episodes.append(memfmt.Episode(summary="Резервирование завершено"))
    store.memory.procedures.append(memfmt.Procedure(
        name="Резервирование", steps=[memfmt.Step(action="Создать копию")]))
    store.save()
    assert {hit["type"] for hit in LocalStore(tmp_path).search("резервирование")} == {
        "episode", "procedure"}
