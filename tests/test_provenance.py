"""Every fact says where it came from; a host does not inherit another host's paths.

From the r/cursor thread (2026-09-16): a Cursor workspace path recorded on a
Mac landed in a Claude Code session on a Linux box and the agent tried to cd
into it. And: a recalled fact should say which tool wrote it and when.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from cloud import provenance as prov


# --- pure rules -------------------------------------------------------------------

def test_host_header_parses_os_and_tool():
    assert prov.parse_host("darwin/claude-code") == ("darwin", "claude-code")
    assert prov.parse_host("linux") == ("linux", "")
    assert prov.parse_host("") is None and prov.parse_host("weird value/with spaces") is None
    assert prov.host_of({"X-Mengram-Host": "windows/cursor"}) == ("windows", "cursor")
    assert prov.host_of({}) is None


def test_what_is_tied_to_a_host():
    assert prov.host_specific("workspace is at /Users/ali/Projects/mengram") == "path"
    assert prov.host_specific("repo lives in C:\\work\\app") == "path"
    assert prov.host_specific("hooks are configured in ~/.cursor/hooks.json") in ("path", "tool file")
    assert prov.host_specific("keeps rules in .cursorrules") == "tool file"
    assert prov.host_specific("uses Cursor for front-end work") == "cursor"
    assert prov.host_specific("prefers PostgreSQL 16") is None
    assert prov.host_specific("has a dog called Pixel") is None


def test_the_mac_path_does_not_reach_the_linux_box():
    facts = ["workspace is at /Users/ali/Projects/mengram", "prefers PostgreSQL 16", "uses Cursor for front-end work"]
    metas = [{"source": "cursor", "os": "darwin"}, {"source": "cursor", "os": "darwin"}, {"source": "cursor", "os": "darwin"}]
    kept, kept_meta, dropped = prov.filter_for_host(facts, metas, ("linux", "claude-code"))
    assert kept == ["prefers PostgreSQL 16", "uses Cursor for front-end work"] and dropped == 1 and len(kept_meta) == 2
    # a Cursor settings file recorded from Cursor stays out of Claude Code even on the same OS
    kept, _, dropped = prov.filter_for_host(["hooks live in ~/.cursor/hooks.json"], [{"source": "cursor", "os": "darwin"}], ("darwin", "claude-code"))
    assert kept == [] and dropped == 1
    # the same host sees everything
    kept, _, dropped = prov.filter_for_host(facts, metas, ("darwin", "cursor"))
    assert kept == facts and dropped == 0
    # no host declared: nothing is filtered
    kept, _, dropped = prov.filter_for_host(facts, metas, None)
    assert kept == facts and dropped == 0
    # no provenance on the facts: nothing is filtered either
    kept, _, dropped = prov.filter_for_host(facts, [{}, {}, {}], ("linux", "claude-code"))
    assert kept == facts and dropped == 0


def test_meta_summary_and_tag():
    import datetime
    m = prov.meta_summary({"source": "claude-code", "os": "darwin", "cwd": "/Users/ali/x", "session_id": "s1"},
                          created_at=datetime.datetime(2026, 9, 15, 22, 1), access_count=3,
                          last_accessed=datetime.datetime(2026, 9, 16, 8, 0))
    assert m == {"source": "claude-code", "os": "darwin", "cwd": "/Users/ali/x", "session": "s1",
                 "when": "2026-09-15", "recalled": 3, "last_recalled": "2026-09-16"}
    assert prov.tag(m) == "claude-code, 2026-09-15"
    assert prov.meta_summary(None)["source"] == "api"
    assert prov.tag({}) == ""


# --- the hooks --------------------------------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


def test_hook_save_records_tool_session_cwd_and_os(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-key")
    monkeypatch.setenv("MENGRAM_HOME", str(tmp_path))
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)

    class Fake:
        def __init__(self, *a, **k):
            pass

        def add(self, messages, **kw):
            seen.update(kw)
            return {"status": "accepted"}
    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Fake)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "We deploy to Fly.io from main"}}) + "\n"
                          + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted, main auto-deploys to Fly.io."}]}}) + "\n")
    stdin = {"session_id": "sess-9", "hook_event_name": "Stop", "transcript_path": str(transcript), "cwd": "/Users/ali/Projects/app",
             "last_assistant_message": "Noted, main auto-deploys to Fly.io."}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with pytest.raises(SystemExit):
        cli.cmd_auto_save(_Args(every=1))
    assert seen["source"] == "claude-code"
    assert seen["metadata"]["session_id"] == "sess-9" and seen["metadata"]["cwd"] == "/Users/ali/Projects/app"
    assert seen["metadata"]["os"] in ("darwin", "linux", "windows")


def test_hook_recall_says_who_it_is_and_tags_each_fact(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-key")
    monkeypatch.setenv("MENGRAM_HOME", str(tmp_path))
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setenv("MENGRAM_RECALL_LEXICAL_GUARD", "0")

    class Fake:
        def __init__(self, *a, **k):
            seen["host"] = k.get("host")

        def search(self, *a, **k):
            return [{"entity": "app", "type": "project", "score": 0.9,
                     "facts": ["deploys to Fly.io from main", "uses PostgreSQL 16"],
                     "facts_meta": [{"source": "codex", "when": "2026-09-15"}, {}]}]
    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Fake)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s", "prompt": "where do we deploy the app?", "cwd": "/x"})))
    out = io.StringIO(); monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit):
        cli.cmd_auto_recall(_Args())
    assert seen["host"] and seen["host"].endswith("/claude-code")
    text = out.getvalue()
    assert "deploys to Fly.io from main  (codex, 2026-09-15)" in text
    assert "uses PostgreSQL 16\n" in text or "uses PostgreSQL 16\\n" in text or "uses PostgreSQL 16" in text
