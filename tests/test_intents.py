"""Intent before the command, outcome after — joined by the host's call id.

The gate judges a command once, at PreToolUse. Until now that judgement was
thrown away and re-derived after the fact by matching text out of the
transcript, which also counted every `is_error` result as a failure —
including the classifier refusing a command and the user declining one,
neither of which ran. These tests pin the new contract: an intent is opened
for exactly the commands the recorder would record, closed by id, and only a
result that begins `Exit code N` ever costs a step.
"""
import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from local import intents
from local import transcript as tr

RELEASE_STEPS = [
    {"action": "run the test suite", "detail": "pytest -q tests/"},
    {"action": "build the artifacts", "detail": "python3 -m build"},
    {"action": "upload to PyPI", "detail": "twine upload dist/*"},
    {"action": "write the announcement post"},
]


def _folder(tmp_path):
    from local.store import LocalStore
    store = LocalStore(str(tmp_path))
    store.save_extracted_procedure("Release to PyPI", "shipping a version",
                                   [dict(s) for s in RELEASE_STEPS])
    store.save()
    return store


def _read(tmp_path):
    from local.store import LocalStore
    return LocalStore(str(tmp_path)).procedures()[0]


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


def _hook(monkeypatch, capsys, name, payload, memory):
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.delenv("MENGRAM_POLICY_PATTERN", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["mengram", name, "--memory", str(memory), "--verbose"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out).get("systemMessage", "") if out else ""


def _pre(command, tid="toolu_1", transcript=None):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": tid,
            "session_id": "s1", "transcript_path": transcript,
            "tool_input": {"command": command}}


def _post(command, tid="toolu_1", transcript=None, response=None):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": tid,
            "session_id": "s1", "transcript_path": transcript,
            "tool_input": {"command": command},
            "tool_response": response if response is not None else
            {"stdout": "ok", "stderr": "", "interrupted": False,
             "isImage": False, "noOutputExpected": False}}


# --- what an error result means --------------------------------------------

@pytest.mark.parametrize("body", [
    "Exit code 1\nTraceback (most recent call last)",
    "Exit code 127\n(eval):1: command not found: railway",
    "  Exit code 2",
])
def test_a_non_zero_exit_is_a_failure(body):
    assert tr.ran_and_failed(body)
    assert tr.classify(True, body) == "failed"


@pytest.mark.parametrize("body", [
    "Permission for this action was denied by the auto-mode classifier.",
    "Pushing to 'main' is blocked: this branch auto-deploys.",
    "The user doesn't want to proceed with this tool use.",
    "Command timed out after 2m 0.0s",
    "",
])
def test_a_command_that_never_ran_is_not_a_failure(body):
    assert not tr.ran_and_failed(body)
    assert tr.classify(True, body) == "not_run"


def test_a_success_is_a_success_whatever_the_body_says():
    assert tr.classify(False, "Exit code 1 appears in this output") == "ok"


def test_list_shaped_result_bodies_are_read():
    assert tr._body([{"type": "text", "text": "Exit code 1\nboom"}]) == "Exit code 1\nboom"
    assert tr.ran_and_failed(tr._body([{"type": "text", "text": "Exit code 1"}]))


def test_the_transcript_sweep_no_longer_charges_refusals(tmp_path):
    """The live bug: a refused command counted as a failed step."""
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "pytest -q tests/"), _result("a", "Exit code 1\nold")])
    _, cursor = tr.new_failures(t, {})                       # first run: history seen
    _write(t, [_use("b", "twine upload dist/*"),
               _result("b", "Permission for this action was denied by the auto-mode classifier."),
               _use("c", "pytest -q tests/"),
               _result("c", "The user doesn't want to proceed with this tool use."),
               _use("d", "python3 -m build"), _result("d", "Exit code 1\nno module build")],
           mode="a")
    failures, _ = tr.new_failures(t, cursor)
    assert failures == [("python3 -m build", "no module build")]


# --- finding one call's result by id ---------------------------------------

def test_result_for_finds_the_call_by_id(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "pytest -q"), _result("a", "Exit code 1\n3 failed"),
               _use("b", "twine upload dist/*"), _result("b", "uploaded", error=False),
               _use("c", "git push"), _result("c", "The user doesn't want to proceed.")])
    assert tr.result_for(t, "a") == ("failed", "Exit code 1\n3 failed")
    assert tr.result_for(t, "b") == ("ok", "uploaded")
    assert tr.result_for(t, "c")[0] == "not_run"
    assert tr.result_for(t, "nope") is None
    assert tr.result_for(tmp_path / "missing.jsonl", "a") is None


def test_result_for_reads_from_the_noted_offset(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, [_use("a", "x" * 70000), _result("a", "Exit code 1\nold")])
    offset = t.stat().st_size
    _write(t, [_use("b", "pytest -q"), _result("b", "Exit code 1\nfresh")], mode="a")
    assert tr.result_for(t, "b", offset) == ("failed", "Exit code 1\nfresh")


# --- the intent file --------------------------------------------------------

def test_open_close_round_trip(tmp_path):
    intents.open_intent(tmp_path, tool_use_id="t1", procedure="Release to PyPI",
                        step=3, command="twine upload dist/*", offset=12)
    assert [i["tool_use_id"] for i in intents.open_intents(tmp_path)] == ["t1"]
    got = intents.close_intent(tmp_path, "t1")
    assert got["procedure"] == "Release to PyPI" and got["step"] == 3 and got["offset"] == 12
    assert intents.open_intents(tmp_path) == []
    assert intents.close_intent(tmp_path, "t1") is None


def test_reopening_the_same_id_replaces_it(tmp_path):
    intents.open_intent(tmp_path, tool_use_id="t1", procedure="A", step=1, command="a")
    intents.open_intent(tmp_path, tool_use_id="t1", procedure="B", step=2, command="b")
    assert [i["procedure"] for i in intents.open_intents(tmp_path)] == ["B"]


def test_the_file_is_bounded_and_forgets_the_stale(tmp_path):
    for n in range(intents.MAX_OPEN + 5):
        intents.open_intent(tmp_path, tool_use_id=f"t{n}", procedure="A", step=1, command="a")
    assert len(intents.open_intents(tmp_path)) == intents.MAX_OPEN
    old = intents.open_intents(tmp_path)
    old[0]["opened_at"] = time.time() - intents.MAX_AGE_SECONDS - 1
    intents.save(tmp_path, old)
    assert len(intents.open_intents(tmp_path)) == intents.MAX_OPEN - 1


def test_a_corrupt_file_reads_as_empty(tmp_path):
    intents.path(tmp_path).parent.mkdir(parents=True)
    intents.path(tmp_path).write_text("{not json")
    assert intents.open_intents(tmp_path) == []


# --- PreToolUse notes the intent -------------------------------------------

def test_pretooluse_notes_which_step_the_command_is(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("x", "ls")])
    _hook(monkeypatch, capsys, "auto-policy", _pre("twine upload dist/*", "toolu_9", str(t)), tmp_path)
    [intent] = intents.open_intents(tmp_path)
    assert intent["tool_use_id"] == "toolu_9"
    assert intent["procedure"] == "Release to PyPI" and intent["step"] == 3
    assert intent["transcript_path"] == str(t) and intent["offset"] == t.stat().st_size


def test_pretooluse_notes_nothing_for_a_command_that_is_no_step(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    _hook(monkeypatch, capsys, "auto-policy", _pre("echo done", "toolu_9"), tmp_path)
    _hook(monkeypatch, capsys, "auto-policy", _pre("git status", "toolu_10"), tmp_path)
    assert intents.open_intents(tmp_path) == []


def test_pretooluse_notes_nothing_without_a_call_id(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    payload = _pre("twine upload dist/*")
    del payload["tool_use_id"]
    _hook(monkeypatch, capsys, "auto-policy", payload, tmp_path)
    assert intents.open_intents(tmp_path) == []


def test_the_gate_still_asks_after_noting_the_intent(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_pre("twine upload dist/*"))))
    monkeypatch.setattr(sys, "argv", ["mengram", "auto-policy", "--memory", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert len(intents.open_intents(tmp_path)) == 1


# --- PostToolUse closes it by id --------------------------------------------

def test_posttooluse_closes_the_intent_and_records_its_step(monkeypatch, capsys, tmp_path):
    """The step comes from the intent, not from matching the text again."""
    _folder(tmp_path)
    intents.open_intent(tmp_path, tool_use_id="toolu_9", procedure="Release to PyPI",
                        step=2, command="anything")
    msg = _hook(monkeypatch, capsys, "auto-outcome", _post("make wheel", "toolu_9"), tmp_path)
    assert "'Release to PyPI' step 2 recorded as success" in msg
    assert _read(tmp_path)["steps"][1]["success_count"] == 1
    assert intents.open_intents(tmp_path) == []


def test_posttooluse_without_an_intent_still_matches_the_text(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    msg = _hook(monkeypatch, capsys, "auto-outcome", _post("twine upload dist/*", "toolu_9"), tmp_path)
    assert "step 3 recorded as success" in msg


def test_an_unclear_outcome_closes_the_intent_and_records_nothing(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    intents.open_intent(tmp_path, tool_use_id="toolu_9", procedure="Release to PyPI",
                        step=3, command="twine upload dist/*")
    msg = _hook(monkeypatch, capsys, "auto-outcome",
                _post("twine upload dist/*", "toolu_9", response={"stdout": "", "interrupted": True}),
                tmp_path)
    assert "outcome unclear" in msg
    assert intents.open_intents(tmp_path) == []
    assert _read(tmp_path)["steps"][2].get("success_count") in (None, 0)


# --- an earlier intent whose command failed --------------------------------

def _seed_cursor(tmp_path, t):
    """Start the transcript sweep past the current history, as a real session would."""
    _, cursor = tr.new_failures(t, {})
    tr.write_cursor(tmp_path, cursor)


def test_a_failed_command_is_charged_from_its_intent(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("z", "ls")])
    _seed_cursor(tmp_path, t)
    offset = t.stat().st_size
    intents.open_intent(tmp_path, tool_use_id="toolu_fail", procedure="Release to PyPI",
                        step=1, command="pytest -q tests/", transcript_path=str(t), offset=offset)
    # The command failed: no PostToolUse for it, only a transcript line.
    _write(t, [_use("toolu_fail", "pytest -q tests/"),
               _result("toolu_fail", "Exit code 1\n3 failed, 12 passed"),
               _use("toolu_next", "ls -la"), _result("toolu_next", "files", error=False)], mode="a")
    msg = _hook(monkeypatch, capsys, "auto-outcome", _post("ls -la", "toolu_next", str(t)), tmp_path)
    assert "1 failure(s) from open intents" in msg
    proc = _read(tmp_path)
    assert proc["steps"][0]["fail_count"] == 1
    assert proc["last_failure"] == "3 failed, 12 passed"
    assert intents.open_intents(tmp_path) == []
    # The sweep saw the same id and did not charge it again.
    assert "toolu_fail" in tr.read_cursor(tmp_path)["seen"]
    msg = _hook(monkeypatch, capsys, "auto-outcome", _post("ls -la", "toolu_next2", str(t)), tmp_path)
    assert "failure" not in msg
    assert _read(tmp_path)["steps"][0]["fail_count"] == 1


def test_a_refused_command_is_closed_and_charged_nothing(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("z", "ls")])
    _seed_cursor(tmp_path, t)
    intents.open_intent(tmp_path, tool_use_id="toolu_no", procedure="Release to PyPI",
                        step=3, command="twine upload dist/*", transcript_path=str(t),
                        offset=t.stat().st_size)
    _write(t, [_use("toolu_no", "twine upload dist/*"),
               _result("toolu_no", "The user doesn't want to proceed with this tool use.")], mode="a")
    _hook(monkeypatch, capsys, "auto-outcome", _post("ls", "toolu_next", str(t)), tmp_path)
    assert intents.open_intents(tmp_path) == []
    proc = _read(tmp_path)
    assert proc["steps"][2].get("fail_count") in (None, 0)
    assert not proc.get("last_failure")


def test_a_success_on_record_is_closed_but_not_counted_twice(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("z", "ls")])
    _seed_cursor(tmp_path, t)
    intents.open_intent(tmp_path, tool_use_id="toolu_ok", procedure="Release to PyPI",
                        step=3, command="twine upload dist/*", transcript_path=str(t),
                        offset=t.stat().st_size)
    _write(t, [_use("toolu_ok", "twine upload dist/*"), _result("toolu_ok", "done", error=False)],
           mode="a")
    _hook(monkeypatch, capsys, "auto-outcome", _post("ls", "toolu_next", str(t)), tmp_path)
    assert intents.open_intents(tmp_path) == []
    assert _read(tmp_path)["steps"][2].get("success_count") in (None, 0)


def test_an_intent_with_no_result_yet_stays_open(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("z", "ls")])
    _seed_cursor(tmp_path, t)
    intents.open_intent(tmp_path, tool_use_id="toolu_running", procedure="Release to PyPI",
                        step=1, command="pytest -q tests/", transcript_path=str(t),
                        offset=t.stat().st_size)
    _hook(monkeypatch, capsys, "auto-outcome", _post("ls", "toolu_next", str(t)), tmp_path)
    assert [i["tool_use_id"] for i in intents.open_intents(tmp_path)] == ["toolu_running"]


def test_an_intent_nobody_resolved_for_a_day_is_dropped(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    t = tmp_path / "t.jsonl"
    _write(t, [_use("z", "ls")])
    _seed_cursor(tmp_path, t)
    intents.open_intent(tmp_path, tool_use_id="toolu_lost", procedure="Release to PyPI",
                        step=1, command="pytest -q tests/", transcript_path=str(t))
    stale = intents.open_intents(tmp_path)
    stale[0]["opened_at"] = time.time() - cli._INTENT_UNRESOLVED_SECONDS - 1
    intents.save(tmp_path, stale)
    _hook(monkeypatch, capsys, "auto-outcome", _post("ls", "toolu_next", str(t)), tmp_path)
    assert intents.open_intents(tmp_path) == []
    assert _read(tmp_path)["steps"][0].get("fail_count") in (None, 0)


def test_a_failure_from_a_finished_session_is_charged_by_a_later_one(monkeypatch, capsys, tmp_path):
    """The intent carries its own transcript path: the next session settles it."""
    _folder(tmp_path)
    old = tmp_path / "old.jsonl"
    _write(old, [_use("toolu_old", "python3 -m build"),
                 _result("toolu_old", "Exit code 1\nno module named build")])
    intents.open_intent(tmp_path, tool_use_id="toolu_old", procedure="Release to PyPI",
                        step=2, command="python3 -m build", transcript_path=str(old), offset=0)
    new = tmp_path / "new.jsonl"
    _write(new, [_use("toolu_n", "ls")])
    _hook(monkeypatch, capsys, "auto-outcome", _post("ls", "toolu_n", str(new)), tmp_path)
    assert _read(tmp_path)["steps"][1]["fail_count"] == 1
    assert intents.open_intents(tmp_path) == []
