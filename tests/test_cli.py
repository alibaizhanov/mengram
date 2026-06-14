"""Unit tests for cli.py helper functions used by Claude Code hooks."""

import json
import sys

import pytest

import cli


def test_load_cloud_base_url_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MENGRAM_URL", "http://192.168.2.99:8420/")
    monkeypatch.setattr(cli, "DEFAULT_HOME", tmp_path / ".mengram")
    assert cli._load_cloud_base_url() == "http://192.168.2.99:8420"


def test_load_cloud_base_url_from_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MENGRAM_URL", raising=False)
    home = tmp_path / ".mengram"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({
        "api_key": "om-test",
        "base_url": "http://192.168.2.99:8420/",
    }))
    monkeypatch.setattr(cli, "DEFAULT_HOME", home)
    assert cli._load_cloud_base_url() == "http://192.168.2.99:8420"


def test_load_cloud_base_url_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MENGRAM_URL", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_HOME", tmp_path / ".mengram")
    assert cli._load_cloud_base_url() == "https://mengram.io"


def test_hook_marker_format():
    assert cli._hook_marker("auto-recall", "no API key") == "[mengram:auto-recall] no API key"


def _run_emit(monkeypatch, capsys, **kwargs):
    """Helper: call _emit_hook_exit, capture stdout, return parsed JSON payload."""
    with pytest.raises(SystemExit) as exc_info:
        cli._emit_hook_exit(**kwargs)
    assert exc_info.value.code == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out)


class Args:
    def __init__(self, verbose=False):
        self.verbose = verbose


def test_emit_hook_exit_silent_no_context(capsys):
    payload = _run_emit(None, capsys, hook_event_name="Stop", args=Args(verbose=False),
                         hook_name="auto-save", status="no API key")
    assert payload == {"continue": True, "suppressOutput": True}


def test_emit_hook_exit_verbose_no_context(capsys):
    payload = _run_emit(None, capsys, hook_event_name="Stop", args=Args(verbose=True),
                         hook_name="auto-save", status="saved")
    assert payload["continue"] is True
    assert payload["systemMessage"] == "[mengram:auto-save] saved"
    assert "suppressOutput" not in payload


def test_emit_hook_exit_with_context_silent(capsys):
    payload = _run_emit(None, capsys, hook_event_name="UserPromptSubmit", args=Args(verbose=False),
                         hook_name="auto-recall", status="found 1 memories", context="some context")
    assert payload["hookSpecificOutput"] == {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "some context",
    }
    assert "systemMessage" not in payload


def test_emit_hook_exit_with_context_verbose(capsys):
    payload = _run_emit(None, capsys, hook_event_name="UserPromptSubmit", args=Args(verbose=True),
                         hook_name="auto-recall", status="found 1 memories", context="some context")
    assert payload["systemMessage"] == "[mengram:auto-recall] found 1 memories"
    assert payload["hookSpecificOutput"]["additionalContext"] == (
        "[mengram:auto-recall] found 1 memories\n\nsome context"
    )


def test_emit_hook_exit_verbose_stop_has_no_additional_context(capsys):
    payload = _run_emit(None, capsys, hook_event_name="Stop", args=Args(verbose=True),
                         hook_name="auto-save", status="saved")
    assert "hookSpecificOutput" not in payload
