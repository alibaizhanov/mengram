"""`mengram local map` — the page that shows what a folder holds.

Rendering is pure (memory in, HTML out), so the tests build a folder with the
store's own API and check the page for what a person would look for.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli  # noqa: E402
from local.config import write_config  # noqa: E402
from local.map import MAP_FILE, render_map, summarise, write_map  # noqa: E402
from local.store import LocalStore  # noqa: E402


def _seeded(root: Path) -> LocalStore:
    from engine.extractor.conversation_extractor import (ExtractedEntity, ExtractedEpisode, ExtractedFact,
                                                          ExtractedProcedure, ExtractionResult)
    root.mkdir(parents=True, exist_ok=True)
    write_config(root, {"llm": {"provider": "mock"}})
    store = LocalStore(root)
    store.add_extraction(ExtractionResult(
        entities=[ExtractedEntity("Railway", "tool", [ExtractedFact("auto-deploys from main"),
                                                     ExtractedFact("<script>alert(1)</script> is not a fact")]),
                  ExtractedEntity("Ali", "person", [ExtractedFact("builds Mengram")])],
        episodes=[ExtractedEpisode(summary="Deployed 2.34.0 to Railway", context="release day",
                                   outcome="health check passed", emotional_valence="positive",
                                   participants=["Ali"], happened_at="2026-09-07")],
        procedures=[ExtractedProcedure("Deploy to Railway", "a change lands on main",
                                       [{"action": "push to main"}, {"action": "wait for the pool"},
                                        {"action": "verify /health"}])]))
    store.save()
    store.procedure_feedback("Deploy to Railway", True)
    store.procedure_feedback("Deploy to Railway", False, failed_at_step=3, reason="pool was still warming up")
    store = LocalStore(root)  # reload from disk: the page renders what the files say
    return store


def test_render_shows_the_three_views_and_escapes(tmp_path):
    store = _seeded(tmp_path / "m")
    quarantine = [{"procedure": "Deploy to Railway", "version": 1, "date": "2026-09-07",
                   "reason": "probe /health only after the pool is up",
                   "violated_assumption": "the pool is warm when /health is probed",
                   "proposed_steps": [{"action": "push"}, {"action": "run migrations first"}],
                   "regressions": [{"procedure": "Hotfix deploy", "reason": "adds 'run migrations first' that Hotfix deploy never does"}]}]
    html = render_map(store, quarantine, model="mock")
    for needle in ("Who you are", "What happened", "What your agent learned", "Railway", "Ali",
                   "Deployed 2.34.0 to Railway", "health check passed", "Deploy to Railway",
                   "push to main", "verify /health", "1✓/1✗", "Quarantine", "Hotfix deploy",
                   "the pool is warm when /health is probed", "Nothing on this page was uploaded"):
        assert needle in html, needle
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # self-contained: no external fetches
    assert "http://" not in html.split("<body>")[0] and "https://" not in html.split("<body>")[0]
    s = summarise(store, quarantine)
    assert s["entities"] == 2 and s["facts"] == 3 and s["episodes"] == 1 and s["procedures"] == 1
    assert s["runs"] == 2 and s["quarantined"] == 1 and s["tested"] == 1


def test_render_on_an_empty_folder(tmp_path):
    root = tmp_path / "e"
    root.mkdir()
    html = render_map(LocalStore(root), [])
    assert "No entities yet" in html and "No episodes yet" in html and "No workflows yet" in html


def test_write_map_lands_in_the_folder_and_memfmt_still_validates(tmp_path):
    store = _seeded(tmp_path / "m")
    path = write_map(store, [])
    assert path == tmp_path / "m" / MAP_FILE and path.stat().st_size > 5000
    memfmt = pytest.importorskip("memfmt")
    if not hasattr(memfmt, "load"):
        pytest.skip("memfmt namespace shadow")
    reloaded = memfmt.load(tmp_path / "m")   # the HTML file is not part of the format and is ignored
    assert len(reloaded.entities) == 2 and len(reloaded.procedures) == 1


def _run(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["mengram", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = int(e.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_map_writes_the_page(tmp_path, monkeypatch, capsys):
    root = tmp_path / "m"
    _seeded(root)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    code, out, _ = _run(monkeypatch, capsys, ["local", "map", "--memory", str(root)])
    assert code == 0 and "map written" in out and (root / MAP_FILE).exists()
    custom = tmp_path / "elsewhere" / "map.html"
    code, out, _ = _run(monkeypatch, capsys, ["local", "map", "--memory", str(root), "--out", str(custom)])
    assert code == 0 and custom.exists()


def test_import_ends_with_the_map(tmp_path, monkeypatch, capsys):
    import importer
    root = tmp_path / "m"
    root.mkdir()
    write_config(root, {"llm": {"provider": "mock"}})
    projects = tmp_path / "claude-projects"
    proj = projects / "-Users-me-Projects-shop"
    proj.mkdir(parents=True)
    rows = []
    for turn in range(3):
        rows.append({"type": "user", "timestamp": f"2026-09-01T10:0{turn}:00Z",
                     "message": {"role": "user", "content": f"I deploy the shop backend to Railway and verify /health after every push, turn {turn}."}})
        rows.append({"type": "assistant", "timestamp": f"2026-09-01T10:0{turn}:30Z",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "Pushed; Railway built it; /health returned 200."}]}})
    (proj / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(importer, "CLAUDE_PROJECTS_DIR", projects)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    code, out, _ = _run(monkeypatch, capsys, ["import", "claude-code", "--memory", str(root), "--yes"])
    assert code == 0 and "Map of the folder" in out and (root / MAP_FILE).exists()


def test_map_shows_last_success_and_flags_staleness(tmp_path):
    from local.store import LocalStore
    store = _seeded(tmp_path / "m")
    p = store.memory.procedures[0]
    p.last_succeeded = "2026-01-01"          # months ago
    store.save()
    html = render_map(LocalStore(tmp_path / "m"), [])
    assert "Last success:</b> 2026-01-01" in html and "unverified for" in html
    p = store.memory.procedures[0]
    import datetime as dt
    p.last_succeeded = dt.date.today().isoformat()
    store.save()
    html = render_map(LocalStore(tmp_path / "m"), [])
    assert "unverified for" not in html
