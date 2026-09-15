"""The same memory under Cursor, in Cursor's dialect.

Cursor's hooks file is flat (`{"version": 1, "hooks": {event: [{command,
timeout}]}}`), a hook answers `{"additional_context": ...}` and nothing else,
and there is no session restart after compaction — the checkpoint has to come
back on the first tool call. These tests hold each of those, plus that
`setup` finds Cursor on its own.
"""
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from local import checkpoint


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MENGRAM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path / "cursor"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    monkeypatch.delenv("MENGRAM_API_KEY", raising=False)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "")
    yield


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


def _run(monkeypatch, fn, args, stdin):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit) as e:
        fn(args)
    assert e.value.code == 0
    return json.loads(out.getvalue())


def _fake_bin(tmp_path):
    p = tmp_path / "mengram"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


def _transcript(tmp_path):
    lines = [
        json.dumps({"role": "user", "content": "Add the Cursor hooks"}),
        json.dumps({"role": "assistant", "content": "Reading the docs first.",
                    "tool_calls": [{"function": {"name": "Edit", "arguments": json.dumps({"file_path": "/repo/cli.py"})}}]}),
        json.dumps({"role": "assistant", "content": [{"type": "output_text", "text": "Hooks file written."}]}),
    ]
    p = tmp_path / "cursor-transcript.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


CURSOR_BASE = {"conversation_id": "conv-1", "generation_id": "g1", "model": "x",
               "cursor_version": "3.20.21", "workspace_roots": ["/repo"], "user_email": "a@b"}


# --- install ------------------------------------------------------------------

def test_install_writes_cursors_flat_hooks_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "unused.json")
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-test")
    cli.cmd_hook_install(_Args(every=3, cursor=True))
    data = json.loads((tmp_path / "cursor" / "hooks.json").read_text())
    assert data["version"] == 1
    assert set(data["hooks"]) == {"sessionStart", "preCompact", "postToolUse", "afterAgentResponse"}
    for event, items in data["hooks"].items():
        assert isinstance(items, list) and "command" in items[0] and "type" not in items[0]
        assert items[0]["command"].endswith("--host cursor")
    assert "auto-restore" in data["hooks"]["postToolUse"][0]["command"]
    assert not (tmp_path / "unused.json").exists()
    assert "Cursor" in capsys.readouterr().out


def test_install_keeps_other_peoples_cursor_hooks(tmp_path, monkeypatch, capsys):
    (tmp_path / "cursor").mkdir()
    (tmp_path / "cursor" / "hooks.json").write_text(json.dumps(
        {"version": 1, "hooks": {"sessionStart": [{"command": "./mine.sh"}], "afterFileEdit": [{"command": "./fmt.sh"}]}}))
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "unused.json")
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-test")
    cli.cmd_hook_install(_Args(every=3, cursor=True))
    data = json.loads((tmp_path / "cursor" / "hooks.json").read_text())
    assert [h["command"] for h in data["hooks"]["sessionStart"]][0] == "./mine.sh"
    assert data["hooks"]["afterFileEdit"] == [{"command": "./fmt.sh"}]

    cli.cmd_hook_uninstall(_Args())
    data = json.loads((tmp_path / "cursor" / "hooks.json").read_text())
    assert data["hooks"]["sessionStart"] == [{"command": "./mine.sh"}]
    assert "preCompact" not in data["hooks"] and data["hooks"]["afterFileEdit"] == [{"command": "./fmt.sh"}]


# --- the handlers in Cursor's dialect -------------------------------------------

def test_precompact_then_first_tool_call_restores_once(tmp_path, monkeypatch):
    out = _run(monkeypatch, cli.cmd_auto_checkpoint, _Args(host="cursor"),
               {**CURSOR_BASE, "hook_event_name": "preCompact", "trigger": "auto",
                "transcript_path": str(_transcript(tmp_path))})
    assert out == {}   # preCompact is observational; nothing is said back
    snap = checkpoint.load("conv-1")
    assert snap["files"] == ["/repo/cli.py"] and snap["prompts"] == ["Add the Cursor hooks"]
    assert snap["last_assistant"] == "Hooks file written." and snap["cwd"] == "/repo"

    out = _run(monkeypatch, cli.cmd_auto_restore, _Args(host="cursor"),
               {**CURSOR_BASE, "hook_event_name": "postToolUse", "tool_name": "Read"})
    assert set(out) == {"additional_context"}
    assert "put the working state back" in out["additional_context"]
    assert "/repo/cli.py" in out["additional_context"]

    out = _run(monkeypatch, cli.cmd_auto_restore, _Args(host="cursor"),
               {**CURSOR_BASE, "hook_event_name": "postToolUse", "tool_name": "Read"})
    assert out == {}   # once


def test_session_start_takes_a_recent_workspace_checkpoint_but_not_an_old_one(tmp_path, monkeypatch):
    checkpoint.save({"session": "older-conv", "cwd": "/repo", "ts": time.time() - 3600,
                     "prompts": ["ship it"], "files": ["/repo/a.py"], "commands": [], "last_assistant": "done"})
    out = _run(monkeypatch, cli.cmd_auto_context, _Args(host="cursor", no_weekly=True),
               {**CURSOR_BASE, "hook_event_name": "sessionStart", "session_id": "conv-2", "composer_mode": "agent"})
    assert "/repo/a.py" in out["additional_context"]

    checkpoint.save({"session": "stale", "cwd": "/repo", "ts": time.time() - 3 * 86400,
                     "prompts": ["old"], "files": ["/repo/old.py"], "commands": [], "last_assistant": ""})
    out = _run(monkeypatch, cli.cmd_auto_context, _Args(host="cursor", no_weekly=True),
               {**CURSOR_BASE, "hook_event_name": "sessionStart", "session_id": "conv-3"})
    assert out == {}


def test_after_agent_response_feeds_save_with_the_text(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-key")

    class Fake:
        def __init__(self, *a, **k):
            pass
        def add(self, messages, **kw):
            seen["messages"] = messages
            return {"status": "accepted"}
    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Fake)
    out = _run(monkeypatch, cli.cmd_auto_save, _Args(host="cursor", every=1),
               {**CURSOR_BASE, "hook_event_name": "afterAgentResponse",
                "text": "Deployed to Railway; the health check at /health passed and REDIS_URL is set."})
    assert out == {}
    assert any("Deployed to Railway" in m.get("content", "") for m in seen["messages"])


def test_a_cursor_answer_is_never_claude_shaped(tmp_path, monkeypatch):
    out = _run(monkeypatch, cli.cmd_auto_checkpoint, _Args(host="cursor"),
               {**CURSOR_BASE, "hook_event_name": "preCompact", "trigger": "manual"})
    assert "continue" not in out and "hookSpecificOutput" not in out


# --- setup finds Cursor -----------------------------------------------------------

def test_setup_finds_cursor_and_installs_its_hooks_too(tmp_path, monkeypatch, capsys):
    (tmp_path / "cursor").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setattr(cli, "DEFAULT_HOME", tmp_path / "home" / ".mengram")
    monkeypatch.setattr(cli, "_save_api_key", lambda key: None)
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "home" / "settings.json")
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-test")
    monkeypatch.setattr(cli, "_detect_mcp_tools", lambda: [])
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    cli.cmd_setup(_Args(key="om-test", no_import=True, no_verify=True, every=3))
    out = capsys.readouterr().out
    assert "Cursor found on this machine" in out
    assert (tmp_path / "cursor" / "hooks.json").exists()
    assert "Restart Claude Code, Cursor" in out
