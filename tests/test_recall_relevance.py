"""Recall must be allowed to say nothing.

Vector search returns its nearest neighbours for any query, however far away
they are, so a three-word question pulled three entities out of the store just
as confidently as a detailed one. The cases below are real: they are what a
running Mengram injected into a live session, alongside the prompts that
produced them.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from cloud.relevance import filter_results, is_related, words


# --- tokenising ------------------------------------------------------------

def test_words_are_found_in_any_alphabet():
    """An ASCII-only pattern sees nothing in a Russian prompt, and a guard that
    sees no words would silence recall rather than filter it."""
    assert words("что за релевантности?") == {"релевантности"}
    assert "claude" in words("почему именно с claude code")


def test_glue_words_are_not_evidence():
    assert words("what is this about") == set()
    assert words("что это такое") == {"такое"}


# --- the real injections from a live session -------------------------------

SESSION = [
    # (prompt, entities the cloud injected, which of them share a word)
    ("посмотри reddit",
     [("WebSocket", ["is handled by admin-panel-backend"]),
      ("Basic Memory", ["uses flat notes", "costs $14/month"]),
      ("GitHub", ["hosts the repository github.com/alibaizhanov/obsidian-mem"])],
     []),
    ("что за релевантности?",
     [("SQLite", ["supports cosine similarity", "is used as a vector store"]),
      ("ObsidianMem v2", ["is a competitor to Mem0"]),
      ("GitHub", ["hosts the repository for ObsidianMem v2"])],
     []),
    ("давай перезапускай проверяй",
     [("Spring Boot", ["used for backend development"]),
      ("Uzum", ["is a bank", "hires backend developers"])],
     []),
    ("почему именно с claude code",
     [("Claude Desktop", ["works with MCP Server", "automatically configures through CLI"]),
      ("Uzum", ["is a bank"])],
     []),
]


@pytest.mark.parametrize("prompt,injected,_expected", SESSION)
def test_unrelated_memories_are_dropped(prompt, injected, _expected):
    results = [{"entity": e, "facts": f} for e, f in injected]
    kept = [r["entity"] for r in filter_results(prompt, results)]
    for entity in kept:
        assert words(entity) & words(prompt), f"{entity} kept without a shared word"


def test_a_memory_about_the_thing_asked_about_survives():
    results = [{"entity": "Claude Code", "facts": ["runs hooks in a plain shell"]},
               {"entity": "Uzum", "facts": ["is a bank"]}]
    kept = [r["entity"] for r in filter_results("почему именно с claude code", results)]
    assert kept == ["Claude Code"]


def test_facts_count_as_well_as_the_name():
    results = [{"entity": "Some project", "facts": ["deployed on Railway from main"]}]
    assert filter_results("how does the railway deploy work", results)


def test_order_is_preserved():
    results = [{"entity": f"railway {i}", "facts": []} for i in range(4)]
    assert [r["entity"] for r in filter_results("railway", results)] == \
           [r["entity"] for r in results]


def test_a_prompt_with_no_content_words_recalls_nothing():
    """The shortest prompts are where vector search has least to go on.

    Passing them through unfiltered would leave exactly the noisiest cases
    untouched: "та теперь?" pulled three entities about a bank backend.
    """
    results = [{"entity": "Uzum", "facts": ["is a bank"]}]
    assert filter_results("та теперь?", results) == []
    assert is_related(set(), "anything", ["at all"]) is False


# --- the hook ---------------------------------------------------------------

class _FakeMem:
    results = []

    def __init__(self, api_key=None, base_url=None):
        pass

    def search(self, prompt, user_id="default", limit=3, graph_depth=1):
        return list(_FakeMem.results)


def _run(monkeypatch, capsys, prompt, results, env=None):
    import cloud.client
    _FakeMem.results = results
    monkeypatch.setenv("MENGRAM_API_KEY", "k")
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.delenv("MENGRAM_RECALL_LEXICAL_GUARD", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cloud.client, "CloudMemory", _FakeMem)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt})))
    monkeypatch.setattr(sys, "argv", ["mengram", "auto-recall", "--verbose"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    return capsys.readouterr().out


def test_hook_injects_nothing_when_nothing_is_related(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "что за релевантности?",
               [{"entity": "SQLite", "facts": ["supports cosine similarity"]}])
    assert "none related" in out
    assert "SQLite" not in out


def test_hook_still_injects_what_is_related(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "почему именно с claude code",
               [{"entity": "Claude Code", "facts": ["runs hooks in a plain shell"]},
                {"entity": "Uzum", "facts": ["is a bank"]}])
    assert "Claude Code" in out and "runs hooks in a plain shell" in out
    assert "Uzum" not in out


def test_the_guard_can_be_turned_off(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "что за релевантности?",
               [{"entity": "SQLite", "facts": ["supports cosine similarity"]}],
               env={"MENGRAM_RECALL_LEXICAL_GUARD": "0"})
    assert "SQLite" in out
