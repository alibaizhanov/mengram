import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace


def patched(client):
    return patch.dict(sys.modules, {"mengram": SimpleNamespace(Mengram=MagicMock(return_value=client))})

sys.path.insert(0, str(Path(__file__).parent))

from mengram_crewai import (
    MengramSearchTool, MengramSaveTool, MengramProceduresTool, mengram_tools,
)


def fake_client():
    m = MagicMock()
    m.search_all.return_value = {
        "semantic": [{"entity": "Ali", "type": "person", "facts": ["prefers dark mode", "uses Vim"]}],
        "episodic": [{"summary": "shipped the deploy fix"}],
        "procedural": [{"name": "deploy", "version": 3}],
    }
    m.add_text.return_value = {"status": "queued"}
    m.procedures.return_value = [{
        "name": "deploy", "version": 3, "success_count": 11, "fail_count": 1,
        "steps": [{"step": 1, "action": "run migrations", "detail": "before push"}],
        "metadata": {"preconditions": [{"assumption": "db reachable"}]},
    }]
    return m


def test_factory_returns_three_tools():
    tools = mengram_tools(api_key="om-x", user_id="u1")
    assert [t.name for t in tools] == ["search_memory", "save_memory", "get_procedures"]
    assert all(t.user_id == "u1" for t in tools)


def test_search_formats_results():
    with patched(fake_client()):
        out = MengramSearchTool(api_key="om-x")._run(query="preferences")
    assert "Ali (person)" in out and "dark mode" in out
    assert "event — shipped the deploy fix" in out
    assert "procedure — deploy" in out


def test_search_empty():
    c = fake_client(); c.search_all.return_value = {"semantic": [], "episodic": [], "procedural": []}
    with patched(c):
        out = MengramSearchTool(api_key="om-x")._run(query="nothing")
    assert "No memories" in out


def test_save():
    c = fake_client()
    with patched(c):
        out = MengramSaveTool(api_key="om-x", user_id="u2")._run(content="Ali uses Vim")
    assert "Saved" in out
    c.add_text.assert_called_once_with("Ali uses Vim", user_id="u2", source="crewai")


def test_procedures_include_track_record_and_preconditions():
    with patched(fake_client()):
        out = MengramProceduresTool(api_key="om-x")._run(task="deploy")
    assert "v3" in out and "11 successes / 1 failures" in out
    assert "verify first" in out and "db reachable" in out


def test_procedures_without_metadata():
    c = fake_client()
    c.procedures.return_value = [{"name": "release", "version": 1, "steps": [],
                                  "success_count": 0, "fail_count": 0, "metadata": None}]
    with patched(c):
        out = MengramProceduresTool(api_key="om-x")._run(task="release")
    assert "release" in out and "verify first" not in out


def test_tools_are_crewai_base_tools():
    from crewai.tools import BaseTool
    for t in mengram_tools(api_key="om-x"):
        assert isinstance(t, BaseTool)
