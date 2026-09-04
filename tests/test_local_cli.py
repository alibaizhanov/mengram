"""Local mode, step 3: the CLI and the Claude Code hooks against a folder.

`provider: mock` in the folder's config makes the engine's MockLLMClient the
model, so every command runs with no key and no network.
"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli  # noqa: E402
from local.config import write_config  # noqa: E402


def _run(monkeypatch, capsys, argv, stdin=None):
    monkeypatch.setattr(sys, "argv", ["mengram", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = int(e.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.delenv("MENGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    root = tmp_path / "memory"
    root.mkdir()
    write_config(root, {"llm": {"provider": "mock"}})
    return root


# --- mengram local … -------------------------------------------------------------

def test_init_creates_the_folder_and_config(tmp_path, monkeypatch, capsys):
    root = tmp_path / "mem"
    code, out, err = _run(monkeypatch, capsys, ["local", "init", str(root), "--provider", "ollama", "--model", "llama3.1:8b"])
    assert code == 0
    assert (root / "MEMORY.md").exists()
    cfg = json.loads((root / ".mengram" / "config.json").read_text())
    assert cfg["llm"] == {"provider": "ollama", "ollama": {"model": "llama3.1:8b"}}
    assert "MENGRAM_MEMORY_DIR" in out


def test_init_rejects_an_unknown_provider(tmp_path, monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["local", "init", str(tmp_path / "m"), "--provider", "gpt9"])
    assert code == 1 and "unknown provider" in err


def test_add_search_procedures_feedback_stat(folder, monkeypatch, capsys):
    mem = ["--memory", str(folder)]
    code, out, _ = _run(monkeypatch, capsys, ["local", "add", "I work at Uzum Bank as a backend developer", *mem])
    assert code == 0 and "entities +" in out
    code, out, _ = _run(monkeypatch, capsys, ["local", "search", "uzum", *mem])
    assert code == 0 and "Uzum Bank" in out
    code, out, _ = _run(monkeypatch, capsys, ["local", "stat", *mem])
    assert code == 0 and "entities" in out and "model: mock" in out


def test_add_without_a_model_says_so(tmp_path, monkeypatch, capsys):
    root = tmp_path / "m"
    root.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code, out, err = _run(monkeypatch, capsys, ["local", "add", "hello", "--memory", str(root)])
    assert code == 2 and "no model configured" in err


def test_missing_folder_is_a_clear_error(tmp_path, monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["local", "search", "x", "--memory", str(tmp_path / "nope")])
    assert code == 1 and "mengram local init" in err


def test_feedback_records_and_revises_with_the_model(folder, monkeypatch, capsys):
    from local.store import LocalStore
    from engine.extractor.conversation_extractor import ExtractedProcedure, ExtractionResult
    store = LocalStore(folder)
    store.add_extraction(ExtractionResult(procedures=[ExtractedProcedure(
        "Deploy to Railway", "a change lands on main", [{"action": "push to main"}, {"action": "verify /health"}])]))
    store.save()
    mem = ["--memory", str(folder)]
    code, out, _ = _run(monkeypatch, capsys, ["local", "feedback", "Deploy to Railway", "--success", *mem])
    assert code == 0 and "1✓/0✗" in out
    # a failure with context asks the (mock) model; whatever it says, the counts are recorded
    code, out, err = _run(monkeypatch, capsys, ["local", "feedback", "Deploy to Railway", "--failure",
                                                "--step", "2", "--context", "connection refused", *mem])
    assert code in (0, 1)
    assert "1✓/1✗" in out
    code, out, _ = _run(monkeypatch, capsys, ["local", "procedures", *mem])
    assert code == 0 and "Deploy to Railway" in out and "last failure: " in out
    code, out, _ = _run(monkeypatch, capsys, ["local", "quarantine", *mem])
    assert code == 0


def test_env_var_selects_the_folder(folder, monkeypatch, capsys):
    monkeypatch.setenv("MENGRAM_MEMORY_DIR", str(folder))
    code, out, _ = _run(monkeypatch, capsys, ["local", "stat"])
    assert code == 0 and "model: mock" in out


# --- the hooks, all local ------------------------------------------------------------

def _seeded(folder):
    from local.store import LocalStore
    from engine.extractor.conversation_extractor import (ExtractedEntity, ExtractedFact,
                                                          ExtractedProcedure, ExtractionResult)
    store = LocalStore(folder)
    store.add_extraction(ExtractionResult(
        entities=[ExtractedEntity("Railway", "service", [ExtractedFact("auto-deploys from main")])],
        procedures=[ExtractedProcedure("Deploy to Railway", "a change lands on main",
                                       [{"action": "push to main"}, {"action": "verify /health"}])]))
    store.save()
    return store


def test_auto_recall_reads_the_folder_without_a_key(folder, monkeypatch, capsys):
    _seeded(folder)
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "how do we deploy to railway?"})
    code, out, _ = _run(monkeypatch, capsys, ["auto-recall", "--memory", str(folder)], stdin=payload)
    assert code == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "Railway" in ctx and "Deploy to Railway" in ctx


def test_auto_context_loads_the_profile_from_the_folder(folder, monkeypatch, capsys):
    _seeded(folder)
    monkeypatch.setenv("MENGRAM_MEMORY_DIR", str(folder))
    code, out, _ = _run(monkeypatch, capsys, ["auto-context", "--no-weekly"], stdin="{}")
    assert code == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "Memory folder: 1 entities" in ctx and "Railway" in ctx


def test_auto_save_extracts_into_the_folder(folder, monkeypatch, capsys, tmp_path):
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "s1", "stop_hook_active": False,
                          "last_assistant_message": "Sure — I deployed it to Railway and the health check passed."})
    code, out, _ = _run(monkeypatch, capsys, ["auto-save", "--every", "1", "--memory", str(folder), "--verbose"],
                        stdin=payload)
    assert code == 0 and "saved (local)" in out
    from local.store import LocalStore
    assert LocalStore(folder).stats()["entities"] >= 1


def test_auto_save_without_a_model_is_silent_but_honest(tmp_path, monkeypatch, capsys):
    root = tmp_path / "m"
    root.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = json.dumps({"session_id": "s2", "last_assistant_message": "a long enough assistant message here"})
    code, out, _ = _run(monkeypatch, capsys, ["auto-save", "--every", "1", "--memory", str(root), "--verbose"],
                        stdin=payload)
    assert code == 0 and "no model configured (local)" in out


def test_auto_policy_gates_a_folder_workflow(folder, monkeypatch, capsys):
    _seeded(folder)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})
    code, out, _ = _run(monkeypatch, capsys, ["auto-policy", "--memory", str(folder)], stdin=payload)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_install_in_local_mode_needs_no_key(folder, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "settings.json")
    code, out, _ = _run(monkeypatch, capsys, ["hook", "install", "--memory", str(folder)])
    assert code == 0 and "local mode" in out
    settings = json.loads((tmp_path / "settings.json").read_text())
    cmds = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert len(cmds) == 4 and all("--memory" in c and str(folder.resolve()) in c for c in cmds)
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def test_hook_install_without_key_or_folder_points_at_local_mode(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("MENGRAM_API_KEY", raising=False)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    code, out, err = _run(monkeypatch, capsys, ["hook", "install"])
    assert code == 1 and "--memory" in err
