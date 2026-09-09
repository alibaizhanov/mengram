"""An install that cannot run must never report success.

Hooks are built to fail silently, which is right: a broken hook must not stop
you working. The cost is that a hook which never launches looks exactly like a
hook with nothing to say. `mengram hook install` wrote a bare `mengram` into
Claude Code's settings, which resolves only when the install happened to land
on PATH; `hook status` then read that file back and said "installed", and
`doctor` pinged the cloud and said "OK". All three could be true while nothing
had run for months. These tests hold that door shut.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli


def _fake_script(tmp_path, name="mengram"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


# --- which program gets written into the settings --------------------------

def test_the_running_script_is_what_gets_written(tmp_path, monkeypatch):
    script = _fake_script(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(script), "hook", "install"])
    assert cli._resolve_mengram_bin() == str(script.resolve())


def test_a_module_invocation_falls_back_to_path(tmp_path, monkeypatch):
    # `python cli.py hook install`: argv[0] is not the console script.
    monkeypatch.setattr(sys, "argv", ["cli.py", "hook", "install"])
    monkeypatch.setattr(cli.shutil, "which", lambda n: "/somewhere/bin/mengram")
    assert cli._resolve_mengram_bin() == "/somewhere/bin/mengram"


def test_bare_name_only_as_a_last_resort(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli.py"])
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    assert cli._resolve_mengram_bin() == "mengram"


def test_a_non_executable_file_is_not_the_program(tmp_path, monkeypatch):
    plain = tmp_path / "mengram"
    plain.write_text("not executable")
    monkeypatch.setattr(sys, "argv", [str(plain)])
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    assert cli._resolve_mengram_bin() == "mengram"


def test_paths_with_spaces_are_quoted():
    assert cli._shell_quote("/Users/a b/bin/mengram") == '"/Users/a b/bin/mengram"'
    assert cli._shell_quote("/usr/bin/mengram") == "/usr/bin/mengram"


# --- asking the question the way Claude Code asks it -----------------------

def test_a_command_that_does_not_resolve_is_reported_as_broken():
    ok, detail = cli._hook_command_runs("definitely-not-a-real-program-9f3a auto-save")
    assert ok is False and detail


def test_a_command_that_resolves_is_reported_as_working():
    ok, detail = cli._hook_command_runs(sys.executable)
    assert ok is True and detail == ""


def test_the_check_uses_a_plain_shell_not_the_users_profile(tmp_path, monkeypatch):
    """The bug in one test: on PATH here, still broken where hooks run."""
    script = _fake_script(tmp_path, "mengram-probe")
    # Visible to a PATH lookup inside this process...
    monkeypatch.setenv("PATH", os.defpath)
    assert cli.shutil.which("mengram-probe") is None
    # ...and the shell agrees, which is the point: both must be asked.
    ok, _ = cli._hook_command_runs("mengram-probe auto-save")
    assert ok is False
    assert script.exists()


# --- what status and doctor read ------------------------------------------

def _settings(tmp_path, command):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": command}]}]}}))
    return p


def test_a_broken_installed_hook_is_found(tmp_path, monkeypatch):
    p = _settings(tmp_path, "/nonexistent/bin/mengram auto-save --every 3")
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: p)
    broken = cli._broken_hook_commands()
    assert len(broken) == 1 and "auto-save" in broken[0][0]


def test_a_working_installed_hook_is_not_reported(tmp_path, monkeypatch):
    p = _settings(tmp_path, f"{_fake_script(tmp_path)} auto-save --every 3")
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: p)
    assert cli._broken_hook_commands() == []


def test_other_peoples_hooks_are_left_alone(tmp_path, monkeypatch):
    p = _settings(tmp_path, "~/.claude/hooks/block-push-to-main.sh")
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: p)
    assert cli._broken_hook_commands() == []


def test_no_hooks_installed_is_not_a_failure(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    p.write_text("{}")
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: p)
    assert cli._broken_hook_commands() == []


def test_unreadable_settings_do_not_crash(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    p.write_text("{ not json")
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: p)
    assert cli._broken_hook_commands() == []


def test_doctor_fails_before_touching_the_cloud(tmp_path, monkeypatch, capsys):
    """A cloud round-trip says nothing about whether the hooks can launch."""
    p = _settings(tmp_path, "/nonexistent/bin/mengram auto-save")
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: p)
    monkeypatch.setattr(cli, "_load_cloud_api_key",
                        lambda: (_ for _ in ()).throw(AssertionError("cloud was contacted")))
    with pytest.raises(SystemExit) as e:
        cli.cmd_doctor(type("A", (), {})())
    assert e.value.code == 1
    assert "cannot run" in capsys.readouterr().err


# --- install writes something that works -----------------------------------

def test_install_writes_the_full_path_and_verifies_it(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "memory"
    (folder / "procedures").mkdir(parents=True)
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: settings)
    script = _fake_script(tmp_path)
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(script))
    args = type("A", (), {"memory": str(folder), "every": 3, "user_id": None,
                          "no_policy": False})()
    cli.cmd_hook_install(args)
    written = json.loads(settings.read_text())
    cmds = [h["command"] for gs in written["hooks"].values() for g in gs for h in g["hooks"]]
    assert cmds and all(c.startswith(str(script)) for c in cmds)
    assert "Verified" in capsys.readouterr().out
