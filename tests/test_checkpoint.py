"""The working state must survive compaction, exactly.

The host's compaction summary is lossy by design, and what it drops is
invisible from inside the session: the agent carries on from the summary as
if nothing were missing. These tests hold that the checkpoint written at
PreCompact keeps the things that locate the work — the last prompts, the
files touched, the last commands, where the assistant left off — and that
SessionStart hands them back verbatim, once, with or without an account.
"""
import io
import os
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from local import checkpoint, receipt


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MENGRAM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MENGRAM_API_KEY", raising=False)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "")
    yield


def _line(role, content):
    return json.dumps({"type": role, "message": {"role": role, "content": content}})


def _claude_transcript(tmp_path):
    lines = [
        _line("user", "Add a PreCompact hook to the CLI"),
        _line("assistant", [
            {"type": "text", "text": "Reading the hook wiring first."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "grep -n SessionStart cli.py"}},
        ]),
        _line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "734: ..."}]),
        _line("assistant", [
            {"type": "tool_use", "id": "t2", "name": "Edit",
             "input": {"file_path": "/repo/cli.py", "old_string": "a", "new_string": "b"}},
            {"type": "tool_use", "id": "t3", "name": "Write",
             "input": {"file_path": "/repo/local/checkpoint.py", "content": "..."}},
        ]),
        _line("user", "<system-reminder>hook output, not the person</system-reminder>"),
        _line("user", "Also cover Codex"),
        _line("assistant", [
            {"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "pytest -q tests/test_checkpoint.py"}},
            {"type": "text", "text": "Tests written; running them now."},
        ]),
        json.dumps({"type": "progress", "data": "not a message"}),
        "this line is not json",
    ]
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


# --- reading the transcript ---------------------------------------------------

def test_snapshot_keeps_what_locates_the_work(tmp_path):
    snap = checkpoint.snapshot(_claude_transcript(tmp_path), "s1", cwd="/repo", trigger="auto")
    assert snap["prompts"] == ["Add a PreCompact hook to the CLI", "Also cover Codex"]
    assert snap["files"] == ["/repo/cli.py", "/repo/local/checkpoint.py"]
    assert snap["commands"] == ["grep -n SessionStart cli.py", "pytest -q tests/test_checkpoint.py"]
    assert snap["last_assistant"] == "Tests written; running them now."
    assert snap["cwd"] == "/repo" and snap["trigger"] == "auto" and snap["session"] == "s1"


def test_tool_results_and_hook_wrappers_are_not_prompts(tmp_path):
    snap = checkpoint.snapshot(_claude_transcript(tmp_path), "s1")
    assert not any("tool_result" in p or p.startswith("<") for p in snap["prompts"])


def test_only_the_tail_of_a_long_transcript_is_read(tmp_path):
    p = tmp_path / "long.jsonl"
    with open(p, "w") as fh:
        fh.write(_line("user", "the very first prompt, long gone") + "\n")
        for i in range(20000):
            fh.write(_line("assistant", [{"type": "text", "text": "x" * 60}]) + "\n")
        fh.write(_line("user", "the last prompt") + "\n")
    snap = checkpoint.snapshot(p, "s1")
    assert snap["prompts"] == ["the last prompt"]


def test_codex_rollout_lines_are_read_too(tmp_path):
    lines = [
        json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "Fix the failing build"}]}}),
        json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                    "arguments": json.dumps({"command": ["npm", "test"]})}}),
        json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "apply_patch",
                    "arguments": json.dumps({"path": "src/index.ts"})}}),
        json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "Build fixed, running tests."}]}}),
        json.dumps({"type": "session_meta", "payload": {"id": "abc"}}),
    ]
    p = tmp_path / "rollout.jsonl"
    p.write_text("\n".join(lines) + "\n")
    snap = checkpoint.snapshot(p, "c1", host="codex")
    assert snap["prompts"] == ["Fix the failing build"]
    assert snap["commands"] == ["npm test"]
    assert snap["files"] == ["src/index.ts"]
    assert snap["last_assistant"] == "Build fixed, running tests."
    assert snap["host"] == "codex"


def test_a_missing_transcript_is_an_empty_snapshot(tmp_path):
    snap = checkpoint.snapshot(tmp_path / "nope.jsonl", "s1")
    assert checkpoint.is_empty(snap)


# --- storing and reading back -------------------------------------------------

def test_saved_state_is_read_back_once(tmp_path):
    snap = checkpoint.snapshot(_claude_transcript(tmp_path), "s1", cwd="/repo")
    assert checkpoint.save(snap) is not None
    first = checkpoint.consume("s1", "/repo")
    assert first and first["files"] == snap["files"]
    # A /clear later in the same session must not replay old work.
    assert checkpoint.consume("s1", "/repo") is None


def test_a_resumed_session_with_a_new_id_finds_its_directory(tmp_path):
    checkpoint.save({"session": "old", "cwd": "/repo", "ts": time.time(), "prompts": ["p"],
                     "files": [], "commands": [], "last_assistant": ""})
    checkpoint.save({"session": "other", "cwd": "/elsewhere", "ts": time.time(), "prompts": ["q"],
                     "files": [], "commands": [], "last_assistant": ""})
    got = checkpoint.consume("brand-new-id", "/repo")
    assert got and got["session"] == "old"


def test_a_stale_checkpoint_is_not_offered_to_a_resume(tmp_path):
    checkpoint.save({"session": "old", "cwd": "/repo", "ts": time.time() - 30 * 86400,
                     "prompts": ["p"], "files": [], "commands": [], "last_assistant": ""})
    assert checkpoint.consume("new", "/repo") is None


def test_old_checkpoints_are_pruned(tmp_path):
    for i in range(checkpoint.MAX_FILES + 10):
        checkpoint.save({"session": f"s{i}", "cwd": "/r", "ts": time.time() + i,
                         "prompts": ["p"], "files": [], "commands": [], "last_assistant": ""})
    assert len(list(checkpoint.directory().glob("*.json"))) == checkpoint.MAX_FILES


def test_render_is_verbatim_and_labelled(tmp_path):
    snap = checkpoint.snapshot(_claude_transcript(tmp_path), "s1", cwd="/repo")
    text = checkpoint.render(snap)
    assert "working state saved before compaction" in text
    assert "Add a PreCompact hook to the CLI" in text
    assert "/repo/local/checkpoint.py" in text
    assert "$ pytest -q tests/test_checkpoint.py" in text
    assert "Tests written; running them now." in text


# --- the hooks end to end -----------------------------------------------------

def _run_hook(monkeypatch, fn, args, stdin: dict):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit) as e:
        fn(args)
    assert e.value.code == 0
    return json.loads(out.getvalue())


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


def test_precompact_writes_and_session_start_after_compact_restores(tmp_path, monkeypatch):
    transcript = _claude_transcript(tmp_path)
    out = _run_hook(monkeypatch, cli.cmd_auto_checkpoint, _Args(),
                    {"hook_event_name": "PreCompact", "session_id": "s9",
                     "transcript_path": str(transcript), "cwd": "/repo", "trigger": "auto"})
    assert out.get("continue") is True
    assert checkpoint.path_for("s9").exists()

    out = _run_hook(monkeypatch, cli.cmd_auto_context, _Args(),
                    {"hook_event_name": "SessionStart", "session_id": "s9",
                     "source": "compact", "cwd": "/repo"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "working state saved before compaction" in ctx
    assert "/repo/local/checkpoint.py" in ctx
    assert "Also cover Codex" in ctx
    # Said to the person too — the one moment memory visibly does something.
    line = out["systemMessage"]
    assert "put the working state back after compaction" in line
    assert "2 files you edited" in line and "Also cover Codex" in line

    kinds = [e["kind"] for e in receipt.load()]
    assert kinds == ["checkpoint", "restore"]
    assert "restored the working state after 1 compaction" in receipt.phrase(receipt.summarise(receipt.load()))


def test_a_fresh_start_does_not_replay_a_checkpoint(tmp_path, monkeypatch):
    checkpoint.save(checkpoint.snapshot(_claude_transcript(tmp_path), "s9", cwd="/repo"))
    out = _run_hook(monkeypatch, cli.cmd_auto_context, _Args(),
                    {"hook_event_name": "SessionStart", "session_id": "s9",
                     "source": "startup", "cwd": "/repo"})
    assert "hookSpecificOutput" not in out
    assert checkpoint.path_for("s9").exists()   # kept for the resume it belongs to


def test_restore_survives_a_cloud_failure(tmp_path, monkeypatch):
    checkpoint.save(checkpoint.snapshot(_claude_transcript(tmp_path), "s9", cwd="/repo"))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-key")

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("cloud down")
    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Boom)
    out = _run_hook(monkeypatch, cli.cmd_auto_context, _Args(),
                    {"hook_event_name": "SessionStart", "session_id": "s9",
                     "source": "compact", "cwd": "/repo"})
    assert "Also cover Codex" in out["hookSpecificOutput"]["additionalContext"]


def test_precompact_without_a_transcript_is_silent(tmp_path, monkeypatch):
    out = _run_hook(monkeypatch, cli.cmd_auto_checkpoint, _Args(),
                    {"hook_event_name": "PreCompact", "session_id": "s9", "trigger": "manual"})
    assert out == {"continue": True, "suppressOutput": True}
    assert not list(checkpoint.directory().glob("*.json")) if checkpoint.directory().exists() else True


# --- installing ---------------------------------------------------------------

def _fake_bin(tmp_path):
    p = tmp_path / "mengram"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


def test_install_adds_the_precompact_hook(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: settings)
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-test")
    cli.cmd_hook_install(_Args(every=3))
    data = json.loads(settings.read_text())
    cmds = [h["command"] for g in data["hooks"]["PreCompact"] for h in g["hooks"]]
    assert any("auto-checkpoint" in c for c in cmds)
    assert "Checkpoint" in capsys.readouterr().out


def test_install_into_codex_writes_its_hooks_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "unused.json")
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-test")
    cli.cmd_hook_install(_Args(every=3, codex=True))
    data = json.loads((tmp_path / "codex" / "hooks.json").read_text())
    assert set(data["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreCompact", "Stop"}
    pre = data["hooks"]["PreCompact"][0]["hooks"][0]
    assert "auto-checkpoint --host codex" in pre["command"]
    stop = data["hooks"]["Stop"][0]["hooks"][0]
    assert "auto-save --every 3 --host codex" in stop["command"]
    assert "timeout" not in pre and pre["statusMessage"]
    assert not (tmp_path / "unused.json").exists()   # Claude Code settings untouched
    assert "Codex" in capsys.readouterr().out

    cli.cmd_hook_uninstall(_Args())
    data = json.loads((tmp_path / "codex" / "hooks.json").read_text())
    assert not any(data.get("hooks", {}).get(ev) for ev in ("SessionStart", "UserPromptSubmit", "PreCompact", "Stop"))


def test_inside_orca_codex_hooks_go_to_the_real_home(tmp_path, monkeypatch):
    """Orca points CODEX_HOME at its runtime copy and rebuilds it from
    ~/.codex; a hook written into the copy would be dropped."""
    runtime = str(tmp_path / "orca" / "codex-runtime-home" / "home")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", runtime)
    monkeypatch.setenv("ORCA_CODEX_HOME", runtime)
    assert cli.get_codex_hooks_path() == tmp_path / "home" / ".codex" / "hooks.json"
    monkeypatch.delenv("ORCA_CODEX_HOME")
    assert cli.get_codex_hooks_path() == Path(runtime) / "hooks.json"


def _sandbox_setup(tmp_path, monkeypatch):
    """`cmd_setup` writes the key to ~/.mengram/config.json and the shell
    profile through paths fixed at import time — HOME alone does not move
    them. On 2026-09-15 this test overwrote the developer's real key with
    "om-test". Everything setup writes is redirected here."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setattr(cli, "DEFAULT_HOME", tmp_path / "home" / ".mengram")
    monkeypatch.setattr(cli, "_save_api_key", lambda key: None)
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "home" / "settings.json")
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-test")
    monkeypatch.setattr(cli, "_detect_mcp_tools", lambda: [])
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)


def test_setup_writes_nothing_outside_the_sandbox(tmp_path, monkeypatch):
    _sandbox_setup(tmp_path, monkeypatch)
    real = Path.home() if "HOME" not in os.environ else None   # HOME is patched; use the constant below
    import cli as _cli
    assert str(_cli.DEFAULT_HOME).startswith(str(tmp_path))
    assert str(_cli._cloud_config_path()).startswith(str(tmp_path))


def test_setup_finds_codex_and_installs_its_hooks_too(tmp_path, monkeypatch, capsys):
    """One command on the welcome page covers both tools: setup --key installs
    the Claude Code hooks and, when Codex has been run on this machine, its
    hooks as well — no second command to find."""
    _sandbox_setup(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (tmp_path / "codex").mkdir()
    cli.cmd_setup(_Args(key="om-test", no_import=True, no_verify=True, every=3))
    out = capsys.readouterr().out
    assert "Codex found on this machine" in out
    assert (tmp_path / "codex" / "hooks.json").exists()
    assert "Restart Claude Code, Codex" in out


def test_setup_without_codex_says_nothing_about_it(tmp_path, monkeypatch, capsys):
    _sandbox_setup(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    cli.cmd_setup(_Args(key="om-test", no_import=True, no_verify=True, every=3))
    assert "Codex" not in capsys.readouterr().out


def test_headline_names_what_came_back():
    snap = {"trigger": "auto", "files": ["a.py"], "prompts": ["Fix the failing build please, it broke after the merge of the auth branch this morning"],
            "commands": ["npm test"], "last_assistant": "x"}
    line = checkpoint.headline(snap)
    assert line.startswith("🧠 Mengram put the working state back after compaction: 1 file you edited, your last request («")
    assert "…»)" in line and "the last commands run" in line
    assert checkpoint.headline({"trigger": None, "prompts": [], "files": [], "commands": []}).endswith(
        "after the break: where you left off. The summary may have dropped it; this is exact.")


def test_restore_line_survives_a_cloud_failure(tmp_path, monkeypatch):
    checkpoint.save(checkpoint.snapshot(_claude_transcript(tmp_path), "s9", cwd="/repo"))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-key")

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("cloud down")
    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Boom)
    out = _run_hook(monkeypatch, cli.cmd_auto_context, _Args(),
                    {"hook_event_name": "SessionStart", "session_id": "s9",
                     "source": "compact", "cwd": "/repo"})
    assert "put the working state back" in out["systemMessage"]
