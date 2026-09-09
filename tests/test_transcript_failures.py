"""Failures come out of the transcript, because they arrive nowhere else.

PostToolUse fires only after a command that succeeded, so a track record built
from events alone can only climb. Left that way, a workflow that had been
breaking all week would cross the reliability bar and the gate would fall
silent about it — confidently, on half the evidence.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from local import transcript as tr


def _use(tid, command):
    return {"message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": tid, "input": {"command": command}}]}}


def _result(tid, body, error=True):
    return {"message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": body}]}}


def _write(path, entries, mode="w"):
    with open(path, mode) as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


# --- what went wrong -------------------------------------------------------

def test_reason_strips_the_exit_code_and_keeps_the_last_line():
    assert tr._reason("Exit code 1\nTraceback\nAssertionError: nope") == "AssertionError: nope"


def test_reason_is_none_when_there_is_nothing_to_say():
    assert tr._reason("Exit code 7") is None
    assert tr._reason("") is None


def test_reason_is_bounded():
    assert len(tr._reason("Exit code 1\n" + "x" * 900)) == 200


# --- reading forward -------------------------------------------------------

def test_a_first_run_records_no_history(tmp_path):
    """Charging steps for failures from days ago would be a loud surprise."""
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "pytest -q"), _result("a", "Exit code 1\nboom")])
    failures, cursor = tr.new_failures(str(t), {})
    assert failures == []
    assert cursor["offset"] == t.stat().st_size


def test_failures_appended_after_the_cursor_are_found(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "pytest -q"), _result("a", "Exit code 1\nold")])
    _, cursor = tr.new_failures(str(t), {})
    _write(t, [_use("b", "twine upload dist/*"), _result("b", "Exit code 1\n403 Forbidden")], mode="a")
    failures, cursor = tr.new_failures(str(t), cursor)
    assert failures == [("twine upload dist/*", "403 Forbidden")]


def test_the_same_failure_is_never_charged_twice(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "x")])
    _, cursor = tr.new_failures(str(t), {})
    _write(t, [_use("b", "pytest -q"), _result("b", "Exit code 1\nboom")], mode="a")
    first, cursor = tr.new_failures(str(t), cursor)
    assert len(first) == 1
    # Rewind the cursor the way a replaced file would, and read it all again.
    again, _ = tr.new_failures(str(t), {"offset": 0, "seen": cursor["seen"]})
    assert again == []


def test_history_within_the_lookback_is_marked_seen_on_the_first_run(tmp_path):
    """Otherwise the next read reaches back past the cursor and charges for it."""
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "pytest -q"), _result("a", "Exit code 1\nold failure")])
    _, cursor = tr.new_failures(str(t), {})
    assert "a" in cursor["seen"]
    _write(t, [_use("b", "ls")], mode="a")
    assert tr.new_failures(str(t), cursor)[0] == []


def test_a_tool_use_before_the_cursor_is_still_resolved(tmp_path):
    """The cursor can land between a call and its result; the lookback covers it."""
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "pytest -q tests/")])
    _, cursor = tr.new_failures(str(t), {})          # cursor now sits after the call
    _write(t, [_result("a", "Exit code 1\n2 failed")], mode="a")
    failures, _ = tr.new_failures(str(t), cursor)
    assert failures == [("pytest -q tests/", "2 failed")]


def test_a_replaced_shorter_file_does_not_crash(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "x" * 400), _result("a", "Exit code 1\nboom")])
    _, cursor = tr.new_failures(str(t), {})
    _write(t, [_use("b", "pytest -q"), _result("b", "Exit code 1\nfresh")])   # truncates
    failures, _ = tr.new_failures(str(t), cursor)
    assert failures == [("pytest -q", "fresh")]


def test_successes_and_other_tools_are_ignored(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "x")])
    _, cursor = tr.new_failures(str(t), {})
    _write(t, [
        _use("b", "pytest -q"), _result("b", "all good", error=False),
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "zzz",
                                  "is_error": True, "content": "a browser tool failed"}]}},
    ], mode="a")
    assert tr.new_failures(str(t), cursor)[0] == []


def test_broken_lines_and_missing_files_are_survivable(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "x")])
    _, cursor = tr.new_failures(str(t), {})
    with open(t, "a") as fh:
        fh.write("{ not json\n")
    _write(t, [_use("b", "pytest -q"), _result("b", "Exit code 1\nboom")], mode="a")
    assert len(tr.new_failures(str(t), cursor)[0]) == 1
    assert tr.new_failures(str(tmp_path / "gone.jsonl"), {}) == ([], {})


def test_the_seen_list_cannot_grow_without_bound(tmp_path):
    tr.write_cursor(tmp_path, {"offset": 5, "seen": [str(i) for i in range(tr.MAX_SEEN + 50)]})
    assert len(tr.read_cursor(tmp_path)["seen"]) == tr.MAX_SEEN
    assert tr.read_cursor(tmp_path)["offset"] == 5


def test_no_cursor_file_reads_as_empty(tmp_path):
    assert tr.read_cursor(tmp_path) == {}


# --- through the hook ------------------------------------------------------

def _folder(tmp_path):
    from local.store import LocalStore
    store = LocalStore(str(tmp_path))
    store.save_extracted_procedure("Release to PyPI", "shipping a version", [
        {"action": "run the test suite", "detail": "pytest -q tests/"},
        {"action": "upload to PyPI", "detail": "twine upload dist/*"}])
    store.save()
    return store


def _run(monkeypatch, capsys, tmp_path, transcript, command="echo hi"):
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": command},
               "tool_response": {"stdout": "", "interrupted": False},
               "transcript_path": str(transcript)}
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv",
                        ["mengram", "auto-outcome", "--memory", str(tmp_path), "--verbose"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    return json.loads(capsys.readouterr().out.strip())["systemMessage"]


def _read(tmp_path):
    from local.store import LocalStore
    return LocalStore(str(tmp_path)).procedures()[0]


def test_the_hook_charges_a_step_for_a_transcript_failure(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "ls")])
    _run(monkeypatch, capsys, tmp_path, t)                       # sets the cursor
    _write(t, [_use("b", "pytest -q tests/"),
               _result("b", "Exit code 1\n3 failed, 12 passed")], mode="a")
    assert "1 failure(s) from the transcript" in _run(monkeypatch, capsys, tmp_path, t)
    proc = _read(tmp_path)
    assert proc["steps"][0]["fail_count"] == 1
    assert proc["last_failure"] == "3 failed, 12 passed"
    # The workflow's own totals are untouched: one step failed, not a whole run.
    assert proc["success_count"] == 0 and proc["fail_count"] == 0


def test_the_hook_does_not_charge_the_same_failure_twice(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "ls")])
    _run(monkeypatch, capsys, tmp_path, t)
    _write(t, [_use("b", "pytest -q tests/"), _result("b", "Exit code 1\nboom")], mode="a")
    _run(monkeypatch, capsys, tmp_path, t)
    msg = _run(monkeypatch, capsys, tmp_path, t)
    assert "failure(s)" not in msg
    assert _read(tmp_path)["steps"][0]["fail_count"] == 1


def test_failures_are_harvested_even_when_this_command_is_not_a_workflow(monkeypatch, capsys, tmp_path):
    """A failure is not about the command that happens to run after it."""
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "ls")])
    _run(monkeypatch, capsys, tmp_path, t, command="cat README.md")
    _write(t, [_use("b", "twine upload dist/*"), _result("b", "Exit code 1\n403")], mode="a")
    msg = _run(monkeypatch, capsys, tmp_path, t, command="cat README.md")
    assert "no known tool" in msg and "1 failure(s)" in msg
    assert _read(tmp_path)["steps"][1]["fail_count"] == 1


def test_a_missing_transcript_path_is_not_an_error(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    payload = {"tool_name": "Bash", "tool_input": {"command": "twine upload dist/*"},
               "tool_response": {"stdout": "ok", "interrupted": False}}
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv",
                        ["mengram", "auto-outcome", "--memory", str(tmp_path), "--verbose"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    assert "recorded as success" in capsys.readouterr().out
