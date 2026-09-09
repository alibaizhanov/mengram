"""The other half of the gate: recording what a command actually did.

`auto-policy` asks about a workflow whose record is weak. Nothing gave a
workflow a record until `auto-outcome`, which is why every procedure in a real
folder read `untested` forever. These tests pin the two things that make the
record worth trusting: it is written only when the command really is one step
of the procedure, and one command is credited to one step rather than to the
whole workflow.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from cloud import policy
from cloud.reliability import from_steps

RELEASE = {
    "id": "p1", "name": "Release to PyPI", "version": 1,
    "steps": [
        {"action": "run the test suite", "detail": "pytest -q tests/"},
        {"action": "build the artifacts", "detail": "python3 -m build"},
        {"action": "upload to PyPI", "detail": "twine upload dist/*"},
        {"action": "write the announcement post"},
    ],
}


# --- which step is this command? -------------------------------------------

def test_command_matches_the_step_that_names_the_same_tool():
    assert policy.match_step(RELEASE, "python3 -m twine upload dist/*")[0] == 3
    assert policy.match_step(RELEASE, "python3 -m build --wheel")[0] == 2


def test_path_segments_count_as_shared_words():
    # "tests/" and "tests/test_policy_hook.py" are not the same token.
    assert policy.match_step(RELEASE, "pytest -q tests/test_policy_hook.py")[0] == 1


def test_prose_steps_never_match_a_command():
    prose = {"name": "Launch", "steps": [{"action": "write the launch post"},
                                         {"action": "decide the price"}]}
    assert policy.match_step(prose, "git push origin main") is None


def test_a_shared_tool_alone_is_not_enough():
    # `git` appears in both, but nothing else does: one word is a coincidence.
    proc = {"name": "x", "steps": [{"action": "tag the release", "detail": "git tag -a v1"}]}
    assert policy.match_step(proc, "git status") is None


def test_command_without_a_known_tool_matches_nothing():
    assert policy.match_step(RELEASE, "echo done") is None
    assert policy.shell_verbs("echo done") == set()


def test_best_step_match_prefers_the_stronger_overlap():
    other = {"name": "Other", "steps": [{"action": "check", "detail": "twine check"}]}
    proc, step, _ = policy.best_step_match([other, RELEASE], "twine upload dist/*")
    assert (proc["name"], step) == ("Release to PyPI", 3)


def test_best_step_match_returns_none_when_nothing_clears_the_bar():
    assert policy.best_step_match([RELEASE], "ls -la") is None


def test_a_verbose_step_does_not_match_by_chance():
    """Raw overlap rewards long steps; coverage is what stops that."""
    wordy = {"name": "Audit", "steps": [{
        "action": "audit the mengram codebase",
        "detail": ("walk every module under cloud and local, build the dist, "
                   "run python3 against each entry point, and write up what "
                   "the audit found in the projects notes"),
    }]}
    assert policy.match_step(wordy, "rm -rf dist build && python3 -m build") is None


def test_heredoc_bodies_are_not_part_of_the_command():
    """`cat >> notes.md <<EOF ... pip install ... EOF` installs nothing."""
    proc = {"name": "Setup", "steps": [{"action": "install the library",
                                        "detail": "pip install mengram-ai"}]}
    carrier = "cat >> notes.md <<'EOF'\npip install mengram-ai is how you start\nEOF"
    assert policy.match_step(proc, carrier) is None
    assert policy._command_text(carrier) == "cat >> notes.md "


def test_enormous_inline_scripts_are_capped():
    assert len(policy._command_text("python3 - " + "x" * 5000)) == 500


def test_a_step_naming_no_tool_needs_to_be_nearly_all_there():
    """"push to main" is a real step, and `git push origin main` is it."""
    plain = {"name": "Deploy", "steps": [{"action": "push to main"}]}
    assert policy.match_step(plain, "git push origin main")[0] == 1
    # Same step, now diluted by commentary the command says nothing about.
    diluted = {"name": "Deploy", "steps": [{
        "action": "push to main",
        "detail": "the webhook picks it up and rebuilds the container image"}]}
    assert policy.match_step(diluted, "git push origin main") is None


# --- did it work? ----------------------------------------------------------

@pytest.mark.parametrize("response,expected", [
    ({"exit_code": 0}, True),
    ({"exit_code": 1}, False),
    ({"exitCode": 2}, False),
    ({"is_error": True}, False),
    ({"isError": False}, True),
    ({"stdout": "fine"}, None),          # no verdict in the transcript
    ({"exit_code": 0, "interrupted": True}, None),
    ({"exit_code": True}, None),         # a bool is not an exit code
    ("not a dict", None),
])
def test_bash_outcome(response, expected):
    assert cli._bash_outcome(response) is expected


def test_stderr_alone_is_not_a_failure():
    # Plenty of healthy tools write to stderr.
    assert cli._bash_outcome({"exit_code": 0, "stderr": "warning: deprecated"}) is True


def test_failure_reason_is_the_last_line_and_bounded():
    assert cli._failure_reason({"stderr": "trace\n  more\nboom: it broke"}) == "boom: it broke"
    assert cli._failure_reason({"stderr": "", "stdout": "fallback"}) == "fallback"
    assert cli._failure_reason({"stderr": "x" * 500}).__len__() == 200
    assert cli._failure_reason({}) is None


# --- one command credits one step ------------------------------------------

def _read(tmp_path):
    """A fresh view of the folder — the hook wrote to disk, not to our object."""
    from local.store import LocalStore
    return LocalStore(str(tmp_path)).procedures()


def _folder(tmp_path):
    from local.store import LocalStore
    store = LocalStore(str(tmp_path))
    store.save_extracted_procedure("Release to PyPI", "shipping a version",
                                   [dict(s) for s in RELEASE["steps"]])
    store.save()
    return store


def test_step_outcome_moves_the_step_and_not_the_workflow(tmp_path):
    store = _folder(tmp_path)
    store.step_outcome("Release to PyPI", 3, success=True)
    proc = store.procedures()[0]
    assert proc["success_count"] == 0 and proc["fail_count"] == 0
    steps = proc["steps"]
    assert steps[2]["success_count"] == 1
    # Steps nobody watched stay unmeasured rather than counted as zero runs.
    assert steps[3].get("success_count") in (None, 0)
    assert steps[0].get("fail_count") in (None, 0)


def test_step_failure_records_why(tmp_path):
    store = _folder(tmp_path)
    store.step_outcome("Release to PyPI", 1, success=False, reason="3 failed, 12 passed")
    proc = store.procedures()[0]
    assert proc["steps"][0]["fail_count"] == 1
    assert proc["last_failure"] == "3 failed, 12 passed"
    assert proc["last_failed"]


def test_step_outcome_rejects_nonsense(tmp_path):
    store = _folder(tmp_path)
    assert "error" in store.step_outcome("Release to PyPI", 99, success=True)
    assert "error" in store.step_outcome("Release to PyPI", 0, success=True)
    assert "error" in store.step_outcome("no such workflow", 1, success=True)


# --- the record the gate then reads ----------------------------------------

def test_reliability_falls_back_to_the_weakest_watched_step():
    proc = {"name": "x", "steps": [{"action": "a", "success_count": 9, "fail_count": 0},
                                   {"action": "b", "success_count": 1, "fail_count": 2}]}
    assert from_steps(proc["steps"]) == (1, 2)
    assert policy.reliability_of(proc) == "40% reliable"


def test_unwatched_steps_are_ignored_not_counted_as_failures():
    assert from_steps([{"action": "a"}, {"action": "b"}]) == (0, 0)
    assert policy.reliability_of({"name": "x", "steps": [{"action": "a"}]}) == "untested"


def test_a_real_whole_run_record_still_wins():
    proc = {"name": "x", "success_count": 4, "fail_count": 0,
            "steps": [{"action": "a", "success_count": 0, "fail_count": 9}]}
    assert policy.reliability_of(proc) == "83% reliable"


def test_step_evidence_can_make_the_gate_ask(tmp_path):
    store = _folder(tmp_path)
    store.step_outcome("Release to PyPI", 1, success=False, reason="boom")
    proc = policy.memfmt_procedures(str(tmp_path))[0]
    verdict = policy.decide(proc, "pytest -q tests/")
    assert verdict and verdict["decision"] == "ask"


# --- the hook end to end ---------------------------------------------------

def _run(monkeypatch, capsys, payload, memory):
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv",
                        ["mengram", "auto-outcome", "--memory", str(memory), "--verbose"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    return json.loads(capsys.readouterr().out.strip())["systemMessage"]


def _bash(command, response):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_response": response}


def test_hook_records_a_success(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    msg = _run(monkeypatch, capsys, _bash("twine upload dist/*", {"exit_code": 0}), tmp_path)
    assert "step 3 recorded as success" in msg
    assert _read(tmp_path)[0]["steps"][2]["success_count"] == 1


def test_hook_records_a_failure_with_its_reason(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    payload = _bash("pytest -q tests/", {"exit_code": 1, "stderr": "2 failed"})
    assert "step 1 recorded as failure" in _run(monkeypatch, capsys, payload, tmp_path)
    proc = _read(tmp_path)[0]
    assert proc["steps"][0]["fail_count"] == 1
    assert proc["last_failure"] == "2 failed"


def test_hook_writes_nothing_when_the_outcome_is_unclear(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    msg = _run(monkeypatch, capsys, _bash("twine upload dist/*", {"stdout": "?"}), tmp_path)
    assert "outcome unclear" in msg
    assert _read(tmp_path)[0]["steps"][2].get("success_count") in (None, 0)


def test_hook_ignores_unrelated_commands_and_other_tools(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    assert "no known tool" in _run(
        monkeypatch, capsys, _bash("ls -la /tmp", {"exit_code": 0}), tmp_path)
    assert "no step matched" in _run(
        monkeypatch, capsys, _bash("git status", {"exit_code": 0}), tmp_path)
    other = {"hook_event_name": "PostToolUse", "tool_name": "Read",
             "tool_input": {"file_path": "x"}, "tool_response": {"exit_code": 0}}
    assert "not Bash" in _run(monkeypatch, capsys, other, tmp_path)


def test_hook_stays_silent_without_verbose(monkeypatch, capsys, tmp_path):
    _folder(tmp_path)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO(json.dumps(_bash("twine upload dist/*", {"exit_code": 0}))))
    monkeypatch.setattr(sys, "argv", ["mengram", "auto-outcome", "--memory", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    assert "systemMessage" not in capsys.readouterr().out
