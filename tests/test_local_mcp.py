"""Local mode, step 4: the MCP surface over a folder, tested without a transport."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.local_mcp_server import TOOLS, handle_tool, create_server  # noqa: E402
from engine.extractor.conversation_extractor import (  # noqa: E402
    ExtractedEntity, ExtractedFact, ExtractedProcedure, ExtractionResult,
)
from local.config import write_config  # noqa: E402
from local.store import LocalStore  # noqa: E402


def _folder(tmp_path, model=True):
    root = tmp_path / "memory"
    root.mkdir(parents=True)
    if model:
        write_config(root, {"llm": {"provider": "mock"}})
    store = LocalStore(root)
    store.add_extraction(ExtractionResult(
        entities=[ExtractedEntity("Railway", "service", [ExtractedFact("auto-deploys from main")])],
        procedures=[ExtractedProcedure("Deploy to Railway", "a change lands on main",
                                       [{"action": "push to main"}, {"action": "verify /health"}], ["Railway"])]))
    store.save()
    return store


def test_the_surface_is_the_connectors_four_plus_feedback():
    assert [t["name"] for t in TOOLS] == ["remember", "recall", "context_for", "list_procedures", "procedure_feedback"]
    for t in TOOLS:
        json.dumps(t["inputSchema"])   # schemas are plain JSON


def test_recall_and_context_for(tmp_path):
    store = _folder(tmp_path)
    assert "Railway" in handle_tool(store, "recall", {"query": "railway deploy"})
    assert handle_tool(store, "recall", {"query": "zzzz"}) == "Nothing relevant in memory."
    assert handle_tool(store, "recall", {}) .startswith("Nothing to recall")
    ctx = handle_tool(store, "context_for", {"task": "deploy the api to railway"})
    assert "# Who this is for" in ctx and "# Relevant memory" in ctx and "Deploy to Railway" in ctx


def test_list_procedures_reads_like_a_record(tmp_path):
    store = _folder(tmp_path)
    store.procedure_feedback("Deploy to Railway", success=True)
    store.procedure_feedback("Deploy to Railway", success=False, failed_at_step=2, reason="cold pool")
    text = handle_tool(store, "list_procedures", {})
    assert "## Deploy to Railway (v1 · 50% reliable, 1✓/1✗)" in text
    assert "1. push to main (2✓/0✗)" in text
    assert "Last failure: " in text and "cold pool" in text
    assert handle_tool(store, "list_procedures", {"query": "nothing-here"}) == "No learned workflows yet."


def test_feedback_records_and_revises(tmp_path):
    store = _folder(tmp_path)
    out = handle_tool(store, "procedure_feedback", {"name": "Deploy to Railway", "success": True})
    assert out.startswith("Recorded: Deploy to Railway v1 is now 1✓/0✗")
    out = handle_tool(store, "procedure_feedback", {"name": "Deploy to Railway", "success": False,
                                                   "failed_at_step": 2, "context": "connection refused"})
    assert "1✓/1✗" in out and ("Revised" in out or "No revision" in out or "Revision failed" in out)
    assert handle_tool(store, "procedure_feedback", {"name": "nope", "success": True}).startswith("procedure not found")
    assert handle_tool(store, "procedure_feedback", {"name": "x"}).startswith("Pass 'name'")


def test_remember_needs_a_model_and_says_so(tmp_path):
    store = _folder(tmp_path, model=False)
    out = handle_tool(store, "remember", {"text": "I work at Uzum Bank"})
    assert out.startswith("No model configured")
    store2 = _folder(tmp_path / "b")
    out = handle_tool(store2, "remember", {"text": "I work at Uzum Bank"})
    assert out.startswith("Remembered:")
    assert handle_tool(store2, "remember", {}).startswith("Nothing to remember")


def test_unknown_tool_is_text_not_an_exception(tmp_path):
    assert handle_tool(_folder(tmp_path), "explode", {}) == "Unknown tool: explode"


def test_server_builds_with_instructions_naming_the_folder(tmp_path):
    store = _folder(tmp_path)
    server = create_server(store.root)
    assert server.name == "mengram-local"
    assert str(store.root.resolve()) in (server.instructions or "")
