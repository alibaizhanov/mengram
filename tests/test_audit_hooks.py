"""Hook and local-mode bugs from the 2026-10-23 audit, one section per bug.

Every test runs in temp dirs: MENGRAM_HOME, the hooks files and the memory
folder are all under tmp_path, and no test reaches the network.
"""
import io
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import cli
from cloud import policy
from local import checkpoint, intents, resume
from local import transcript as tr
from local.store import LocalStore


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MENGRAM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path / "cursor"))
    monkeypatch.delenv("MENGRAM_API_KEY", raising=False)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.delenv("MENGRAM_PARSE_ONLY", raising=False)
    monkeypatch.setattr(cli, "get_claude_code_settings_path", lambda: tmp_path / "claude" / "settings.json")
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "")


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


STEPS = [{"action": "twine upload dist/*"}, {"action": "tag the release and announce it"}]


def _release(folder):
    s = LocalStore(folder)
    s.save_extracted_procedure("Release to PyPI", "release", [dict(x) for x in STEPS])
    s.save()


# --- 1. step evidence survives a refresh -----------------------------------------

def test_step_evidence_is_a_record_and_is_not_wiped_by_the_next_extraction(tmp_path):
    _release(tmp_path)
    for _ in range(3):
        LocalStore(tmp_path).step_outcome("Release to PyPI", 1, success=False, reason="403")
    s = LocalStore(tmp_path)
    assert s.save_extracted_procedure("Release to PyPI", "release", [dict(x) for x in STEPS]) == "kept"
    s.save()
    step = LocalStore(tmp_path).procedures()[0]["steps"][0]
    assert step["fail_count"] == 3


def test_a_refresh_carries_step_history_for_steps_it_left_alone(tmp_path):
    _release(tmp_path)
    s = LocalStore(tmp_path)
    # Tracked but empty: nothing observed, so the wording may still change,
    # and a verbatim step keeps its (tracked) counters.
    s.memory.procedures[0].steps[0].success_count = 0
    s.memory.procedures[0].steps[0].fail_count = 0
    s.save()
    s = LocalStore(tmp_path)
    new = [{"action": "twine upload dist/*"}, {"action": "announce on the blog"}]
    assert s.save_extracted_procedure("Release to PyPI", "release", new) == "refreshed"
    steps = s.memory.procedures[0].steps
    assert steps[0].tracked and not steps[1].tracked
    assert steps[1].action == "announce on the blog"


# --- 2. store and gate agree -----------------------------------------------------------

def test_store_map_and_gate_read_the_same_reliability(tmp_path):
    _release(tmp_path)
    for _ in range(3):
        LocalStore(tmp_path).step_outcome("Release to PyPI", 1, success=False, reason="403")
    gate = policy.memfmt_procedures(str(tmp_path))[0]
    store = LocalStore(tmp_path).procedures()[0]
    assert policy.reliability_of(gate) == store["reliability"] == "20% reliable"
    from local import map as local_map
    assert local_map._reliability_word(LocalStore(tmp_path).memory.procedures[0]) == "20% reliable"


def test_decide_prints_the_counts_the_percentage_came_from(tmp_path):
    _release(tmp_path)
    for _ in range(3):
        LocalStore(tmp_path).step_outcome("Release to PyPI", 1, success=False, reason="403")
    verdict = policy.decide(policy.memfmt_procedures(str(tmp_path))[0], "twine upload dist/*")
    assert "20% reliable (0✓/3✗)" in verdict["reason"]
    assert "(0✓/3✗)" in verdict["say"]


def test_a_revised_workflow_reads_expected_in_the_gate_too(tmp_path):
    from memfmt import Revision
    _release(tmp_path)
    s = LocalStore(tmp_path)
    p = s.memory.procedures[0]
    p.evolution.append(Revision(version_before=1, version_after=2, reason="x", date="2026-10-01",
                                success_count=8, fail_count=0))
    p.version = 2
    s.save()
    gate = policy.memfmt_procedures(str(tmp_path))[0]
    assert policy.reliability_of(gate) == LocalStore(tmp_path).procedures()[0]["reliability"]
    assert policy.reliability_of(gate).endswith("expected")


# --- 3. install quotes values and checks the real argument list ----------------------

def _real_bin(tmp_path, dirname="bin"):
    """A `mengram` that runs this checkout's CLI, like the installed script."""
    d = tmp_path / dirname
    d.mkdir(parents=True, exist_ok=True)
    p = d / "mengram"
    p.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'cli.py'))} \"$@\"\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def _commands(path):
    data = json.loads(path.read_text())
    return {ev: [h["command"] for g in groups for h in g["hooks"]] for ev, groups in data["hooks"].items()}


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_a_user_id_with_a_space_is_quoted_and_verified(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "my memory"
    (folder / "procedures").mkdir(parents=True)
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_real_bin(tmp_path)))
    cli.cmd_hook_install(_Args(memory=str(folder), every=3, user_id="Ali B"))
    out = capsys.readouterr().out
    assert "Verified" in out and "WARNING" not in out
    policy_cmd = _commands(tmp_path / "claude" / "settings.json")["PreToolUse"][0]
    argv = shlex.split(policy_cmd)
    assert argv[argv.index("--user-id") + 1] == "Ali B"
    assert argv[argv.index("--memory") + 1] == str(folder.resolve())


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_the_check_catches_arguments_the_parser_rejects(tmp_path):
    prog = shlex.quote(str(_real_bin(tmp_path)))
    ok, _ = cli._hook_command_runs(f"{prog} auto-policy --user-id Ali B")
    assert ok is False                    # `--help` used to make this pass
    ok, detail = cli._hook_command_runs(f"{prog} auto-policy --user-id 'Ali B'")
    assert ok is True, detail


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_a_hook_with_a_bad_argument_list_still_fails_open(tmp_path):
    """Exit 2 from a PreToolUse hook blocks the tool call; it must be 0."""
    r = subprocess.run([str(_real_bin(tmp_path)), "auto-policy", "--user-id", "Ali", "B"],
                       input="{}", capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, MENGRAM_HOME=str(tmp_path / "home")))
    assert r.returncode == 0


# --- 4. an unreadable settings file is never overwritten ---------------------------

def _fake_bin(tmp_path):
    p = tmp_path / "fakebin" / "mengram"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.mark.parametrize("target", ["claude", "codex", "cursor"])
def test_install_refuses_to_rewrite_a_file_it_cannot_parse(tmp_path, monkeypatch, capsys, target):
    folder = tmp_path / "memory"
    (folder / "procedures").mkdir(parents=True)
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    path = {"claude": tmp_path / "claude" / "settings.json", "codex": tmp_path / "codex" / "hooks.json",
            "cursor": tmp_path / "cursor" / "hooks.json"}[target]
    path.parent.mkdir(parents=True, exist_ok=True)
    broken = '{"permissions": {"allow": ["Bash(ls)"]},, "model": "opus"}'
    path.write_text(broken)
    with pytest.raises(SystemExit) as e:
        cli.cmd_hook_install(_Args(memory=str(folder), every=3,
                                   codex=target == "codex", cursor=target == "cursor"))
    assert e.value.code == 1
    assert path.read_text() == broken
    assert str(path) in capsys.readouterr().err


def test_install_keeps_other_settings_and_a_backup(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "memory"
    (folder / "procedures").mkdir(parents=True)
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(_fake_bin(tmp_path)))
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True)
    original = {"permissions": {"allow": ["Bash(ls)"]}, "model": "opus"}
    path.write_text(json.dumps(original))
    cli.cmd_hook_install(_Args(memory=str(folder), every=3))
    data = json.loads(path.read_text())
    assert data["permissions"] == original["permissions"] and data["model"] == "opus"
    assert json.loads((path.parent / "settings.json.bak").read_text()) == original
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


# --- 5. state survives a slow cloud --------------------------------------------------

def _transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Ship the fix"}}) + "\n"
                 + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "a", "name": "Edit", "input": {"file_path": "/repo/x.py"}}]}})
                 + "\n")
    return p


def _run(monkeypatch, fn, args, payload):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit) as e:
        fn(args)
    assert e.value.code == 0
    return out.getvalue()


def test_the_checkpoint_is_deleted_only_after_it_was_printed(tmp_path, monkeypatch):
    checkpoint.save(checkpoint.snapshot(_transcript(tmp_path), "s1", cwd="/repo"))
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "om-key")
    seen = {}

    class SlowMem:
        def __init__(self, api_key=None, base_url=None, source=None, host=None, timeout=None):
            seen["timeout"] = timeout

        def get_profile(self, user_id=None):
            # Where a killed hook used to lose the work: it is still on disk.
            seen["on_disk"] = checkpoint.path_for("s1").exists()
            return {}

    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", SlowMem)
    out = _run(monkeypatch, cli.cmd_auto_context, _Args(no_weekly=True),
               {"session_id": "s1", "source": "compact", "cwd": "/repo"})
    assert seen["on_disk"] is True
    assert "Ship the fix" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert not checkpoint.path_for("s1").exists()
    assert seen["timeout"] < cli.HOOK_TIMEOUTS["auto-context"]


def test_the_task_card_is_saved_before_the_draft_is_asked_for(tmp_path, monkeypatch):
    seen = {}

    class Mem:
        def __init__(self, api_key=None, base_url=None, source=None, host=None, timeout=None):
            seen["timeout"] = timeout

        def draft_resume(self, payload):
            seen["saved_first"] = bool(resume.all_cards())
            return {"task": "Ship the fix"}

    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", Mem)
    card = cli._write_resume_card(_Args(), {"transcript_path": str(_transcript(tmp_path)),
                                            "cwd": str(tmp_path), "session_id": "s1"}, api_key="om-key")
    assert seen["saved_first"] is True
    assert card["task"] == "Ship the fix"
    assert resume.all_cards()[0]["task"] == "Ship the fix"
    assert seen["timeout"] < cli.HOOK_TIMEOUTS["auto-save"]


# --- 6. one outcome cursor per transcript ------------------------------------------

def _use(tid, command):
    return {"message": {"content": [{"type": "tool_use", "name": "Bash", "id": tid, "input": {"command": command}}]}}


def _fail(tid):
    return {"message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": True,
                                     "content": "Exit code 1\n403 Forbidden"}]}}


def _append(path, *entries):
    with open(path, "a") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _sweep(folder, transcript):
    return cli._record_transcript_failures(str(transcript), folder, policy.memfmt_procedures(str(folder)),
                                           LocalStore(folder), "s")


def _fails(folder):
    return LocalStore(folder).procedures()[0]["steps"][0].get("fail_count") or 0


def test_sessions_side_by_side_keep_their_own_read_position(tmp_path):
    folder = tmp_path / "memory"
    _release(folder)
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _append(a, _use("a0", "ls"))
    _append(b, _use("b_old", "twine upload dist/*"), _fail("b_old"))   # b's past
    _sweep(folder, a)                                    # first read of a: nothing
    _append(a, _use("a1", "twine upload dist/*"), _fail("a1"))
    _sweep(folder, b)                                    # first read of b: its past is not charged
    assert _fails(folder) == 0
    _sweep(folder, a)                                    # a's new failure, from a's own position
    assert _fails(folder) == 1
    _append(b, _use("b1", "twine upload dist/*"), _fail("b1"))
    _sweep(folder, b)
    _sweep(folder, a)
    _sweep(folder, b)
    assert _fails(folder) == 2
    cursor = tr.read_cursor(folder)
    assert set(cursor["transcripts"]) == {str(a), str(b)} and "offset" not in cursor


# --- 7. intents under parallel calls -----------------------------------------------

def test_a_closed_intent_is_closed_once_and_charged_once(tmp_path):
    folder = tmp_path / "memory"
    _release(folder)
    t = tmp_path / "t.jsonl"
    _append(t, _use("x", "ls"))
    intents.open_intent(folder, tool_use_id="f1", procedure="Release to PyPI", step=1,
                        command="twine upload dist/*", transcript_path=str(t), offset=0)
    _append(t, _use("f1", "twine upload dist/*"), _fail("f1"))
    store = LocalStore(folder)
    seen_a, seen_b = [], []
    # Two outcome hooks that both listed f1 as open before either closed it.
    first = cli._settle_open_intents(folder, store, "other", seen_a)
    intents.save(folder, intents.load(folder) + [{"tool_use_id": "f1", "procedure": "Release to PyPI",
                                                    "step": 1, "transcript_path": str(t), "offset": 0,
                                                    "opened_at": __import__("time").time()}])
    second = cli._settle_open_intents(folder, LocalStore(folder), "other", seen_a)
    assert "1 failure" in first and second == ""
    assert _fails(folder) == 1


def test_intent_writes_use_unique_temporaries(tmp_path, monkeypatch):
    import threading
    errors = []

    def worker(n):
        try:
            for i in range(25):
                intents.open_intent(tmp_path, tool_use_id=f"{n}-{i}", procedure="p", step=1, command="git status")
                intents.close_intent(tmp_path, f"{n}-{i}")
        except Exception as e:          # os.replace on a shared .tmp used to raise here
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert intents.load(tmp_path) == []


# --- 8. hooks under a path with a space are found -----------------------------------

def test_quoted_program_paths_are_detected(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "memory"
    (folder / "procedures").mkdir(parents=True)
    script = _fake_bin(tmp_path)
    spaced = tmp_path / "Application Support" / "mengram"
    spaced.parent.mkdir()
    spaced.write_text(script.read_text())
    spaced.chmod(script.stat().st_mode)
    monkeypatch.setattr(cli, "_resolve_mengram_bin", lambda: str(spaced))
    settings = tmp_path / "claude" / "settings.json"
    cli.cmd_hook_install(_Args(memory=str(folder), every=3))
    cli.cmd_hook_install(_Args(memory=str(folder), every=3))      # reinstall: no duplicates
    cmds = _commands(settings)
    assert all(len(v) == 1 for v in cmds.values()), cmds
    capsys.readouterr()
    cli.cmd_hook_status(_Args())
    status = capsys.readouterr().out
    assert "Auto-save:      installed" in status and "Policy gate:    installed" in status
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))   # uninstall clears counters there
    cli.cmd_hook_uninstall(_Args())
    assert "hooks" not in json.loads(settings.read_text())


def test_hook_matching():
    assert cli._hook_matches("'/a b/mengram' auto-save --every 3", "mengram auto-save")
    assert cli._hook_matches('"/a b/mengram" auto-save', "mengram auto-save")
    assert cli._hook_matches("mengram auto-save", "mengram auto-save")
    assert not cli._hook_matches("mengram auto-save", "mengram auto-recall")
    assert not cli._hook_matches("~/.claude/hooks/block-push.sh auto-save", "mengram auto-save")


# --- 9. a detached HEAD does not share a card --------------------------------------

def test_detached_worktrees_get_their_own_card():
    a = {"remote": "github.com/acme/app", "branch": "HEAD", "root": "/w/one"}
    b = {"remote": "github.com/acme/app", "branch": "HEAD", "root": "/w/two"}
    assert resume.card_key(a) != resume.card_key(b)
    # Committing while detached moves HEAD, not the worktree: same card.
    assert resume.card_key(dict(a, head="abc")) == resume.card_key(dict(a, head="def"))
    on_branch = {"remote": "github.com/acme/app", "branch": "main", "root": "/w/one"}
    assert resume.card_key(on_branch) == resume.card_key(dict(on_branch, root="/w/two"))
