"""The receipt: one line per thing memory did, summed into a sentence."""
import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli  # noqa: E402
from cloud import policy  # noqa: E402
from local import receipt  # noqa: E402


# --- the ledger ---------------------------------------------------------------

def test_note_appends_and_load_reads_back():
    receipt.note("recall", "s1")
    receipt.note("gate", "s1", procedure="deploy", reliability="untested")
    receipt.note("step", "s1", procedure="deploy", step=2, ok=False, source="transcript")
    events = receipt.load()
    assert [e["kind"] for e in events] == ["recall", "gate", "step"]
    assert events[1]["procedure"] == "deploy"
    assert events[2]["source"] == "transcript"


def test_unknown_kinds_and_garbage_lines_are_ignored():
    receipt.note("bogus", "s1")
    p = receipt.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write("not json\n")
        f.write(json.dumps({"kind": "recall", "session": "s1"}) + "\n")
    assert [e["kind"] for e in receipt.load()] == ["recall"]


def test_note_never_raises_when_the_home_is_unwritable(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(receipt, "path", lambda: blocker / "receipts.jsonl")
    receipt.note("recall", "s1")          # parent is a file: mkdir fails, quietly
    assert receipt.load() == []


def test_ledger_is_trimmed_past_its_bound(monkeypatch):
    monkeypatch.setattr(receipt, "MAX_BYTES", 400)
    for _ in range(20):
        receipt.note("recall", "s1")
    assert 0 < len(receipt.load()) < 20
    assert receipt.path().stat().st_size <= 400


# --- the sentence -------------------------------------------------------------

def _ev(kind, session="s1", ts=None, **f):
    e = {"kind": kind, "session": session, "ts": ts or time.time()}
    e.update(f)
    return e


def test_phrase_lists_only_what_happened():
    s = receipt.summarise([_ev("recall"), _ev("recall"), _ev("gate")])
    assert receipt.phrase(s) == ("recalled memories on 2 prompts · "
                                 "asked before 1 workflow with a weak record")


def test_phrase_counts_steps_and_transcript_failures():
    s = receipt.summarise([_ev("step", ok=True, source="event"),
                           _ev("step", ok=False, source="transcript"),
                           _ev("step", ok=False, source="event"),
                           _ev("save")])
    assert receipt.phrase(s) == ("recorded 3 step outcomes (1 ok, 2 failed) · "
                                 "caught 1 failure the host never reported · "
                                 "saved 1 turn to memory")


def test_nothing_happened_is_silence():
    assert receipt.phrase(receipt.summarise([])) is None
    assert receipt.session_line("now", []) is None


def test_previous_session_skips_the_current_one():
    events = [_ev("recall", "old", ts=1), _ev("gate", "prev", ts=2), _ev("recall", "now", ts=3)]
    sid, evs = receipt.previous_session("now", events)
    assert sid == "prev" and [e["kind"] for e in evs] == ["gate"]
    assert "asked before 1 workflow" in receipt.session_line("now", events)


def test_since_windows_by_time():
    now = 1_000_000.0
    events = [_ev("recall", ts=now - 10 * 86400), _ev("recall", ts=now - 100)]
    assert len(receipt.since(7, events, now=now)) == 1


# --- the hooks leave lines behind --------------------------------------------

def _folder(tmp_path):
    from local.store import LocalStore
    root = tmp_path / "memory"
    root.mkdir()
    (root / ".mengram").mkdir()
    (root / ".mengram" / "config.json").write_text(json.dumps({"llm": {"provider": "mock"}}))
    store = LocalStore(root)
    store.save_extracted_procedure(
        "Release to PyPI", "a version is tagged",
        [{"action": "run the tests", "detail": "pytest -q tests/"},
         {"action": "build", "detail": "python -m build"},
         {"action": "upload", "detail": "twine upload dist/*"}])
    store.save()
    return root


def _run(monkeypatch, capsys, argv, payload):
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["mengram", *argv])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    return capsys.readouterr().out.strip()


def test_gate_and_outcome_write_the_receipt(monkeypatch, capsys, tmp_path):
    root = _folder(tmp_path)
    monkeypatch.setenv("MENGRAM_POLICY_PATTERN", ".*")
    out = _run(monkeypatch, capsys, ["auto-policy", "--memory", str(root)],
               {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "sess-1",
                "tool_use_id": "toolu_1", "tool_input": {"command": "twine upload dist/*"}})
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    _run(monkeypatch, capsys, ["auto-outcome", "--memory", str(root)],
         {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "sess-1",
          "tool_use_id": "toolu_1", "tool_input": {"command": "twine upload dist/*"},
          "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False}})
    events = receipt.load()
    assert [(e["kind"], e["session"]) for e in events] == [("gate", "sess-1"), ("step", "sess-1")]
    assert events[1]["ok"] is True and events[1]["step"] == 3


def test_session_start_shows_last_session_once(monkeypatch, capsys, tmp_path):
    root = _folder(tmp_path)
    receipt.note("recall", "sess-1")
    receipt.note("gate", "sess-1", procedure="Release to PyPI", reliability="untested")
    out = _run(monkeypatch, capsys, ["auto-context", "--memory", str(root), "--no-weekly"],
               {"hook_event_name": "SessionStart", "session_id": "sess-2", "source": "startup"})
    msg = json.loads(out)["systemMessage"]
    assert msg.startswith("🧠 Mengram, last session: recalled memories on 1 prompt · asked before 1 workflow")
    # A compaction is the same session continuing: nothing is said.
    out = _run(monkeypatch, capsys, ["auto-context", "--memory", str(root), "--no-weekly"],
               {"hook_event_name": "SessionStart", "session_id": "sess-2", "source": "compact"})
    assert "systemMessage" not in json.loads(out)


def test_session_start_without_a_key_still_shows_the_receipt(monkeypatch, capsys):
    monkeypatch.delenv("MENGRAM_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "")
    receipt.note("step", "sess-1", procedure="deploy", step=1, ok=False, source="transcript")
    out = _run(monkeypatch, capsys, ["auto-context", "--no-weekly"],
               {"hook_event_name": "SessionStart", "session_id": "sess-2"})
    data = json.loads(out)
    assert "caught 1 failure the host never reported" in data["systemMessage"]
    assert data["continue"] is True


def test_receipt_command_prints_last_session_and_window(monkeypatch, capsys):
    receipt.note("recall", "sess-1")
    receipt.note("save", "sess-1")
    monkeypatch.setattr(sys, "argv", ["mengram", "receipt"])
    cli.main()
    out = capsys.readouterr().out
    assert "Last session (" in out
    assert "recalled memories on 1 prompt · saved 1 turn to memory" in out
    assert "Past 7 days, 1 session:" in out


def test_receipt_command_when_empty_points_at_the_hooks(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mengram", "receipt"])
    cli.main()
    assert "mengram hook install" in capsys.readouterr().out


# --- the gate speaks ----------------------------------------------------------

def test_verdict_carries_a_line_to_say_with_the_last_failure():
    proc = {"name": "deploy to Railway", "version": 3, "success_count": 2, "fail_count": 3,
            "steps": [{"action": "push to main", "detail": "git push origin main"}],
            "last_failure": "the pool was cold", "last_failed": "2026-07-30"}
    v = policy.decide(proc, "git push origin main")
    assert v["say"] == ("Mengram flagged this: 'deploy to Railway' is 43% reliable (2✓/3✗); "
                        "last failure 2026-07-30: the pool was cold.")
    assert v["plan"].endswith("Say exactly: " + v["say"])


def test_untested_workflow_says_so_without_counts():
    proc = {"name": "deploy", "steps": [{"action": "push", "detail": "git push origin main"}]}
    v = policy.decide(proc, "git push origin main")
    assert v["say"] == "Mengram flagged this: 'deploy' is untested."
