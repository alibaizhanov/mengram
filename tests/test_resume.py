"""`mengram resume`: a task card that follows the task, not the directory.

E6 (experiments/QUEUE.md). The card is keyed by remote + branch so it is found
from another Orca worktree; it says which commit its last check ran on; the
agent's task/done/remaining stays a draft until a person confirms it.
"""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from local import resume


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MENGRAM_HOME", str(tmp_path / "home"))
    r = tmp_path / "repo"; r.mkdir()
    def git(*a):
        return subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("remote", "add", "origin", "git@github.com:acme/app.git")
    (r / "a.py").write_text("x = 1\n"); git("add", "."); git("commit", "-q", "-m", "one")
    return r, git


def _transcript(path: Path, with_tests=True):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "Prepare the Dify example for publication"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Editing the workflow."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/examples/dify-support/workflow.yml"}}]}}),
    ]
    if with_tests:
        lines += [
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python3 -m pytest -q tests/test_dify.py"}}]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "....\n4 passed in 0.31s\n"}]}}),
        ]
    lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Local tests pass; the Dify import is not checked yet."}]}}))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_key_is_remote_plus_branch_not_the_directory(repo, tmp_path):
    r, git = repo
    info = resume.repo_info(r)
    assert info["remote"] == "github.com/acme/app" and info["branch"] == "main" and info["head"]
    k1 = resume.card_key(info)
    # a second worktree of the same branch elsewhere → same key
    wt = tmp_path / "wt"; git("worktree", "add", "-q", str(wt), "main", "--force") if False else None
    other = dict(info, root="/somewhere/else")
    assert resume.card_key(other) == k1
    assert resume.card_key(dict(info, branch="feature/x")) != k1
    assert resume._norm_remote("https://github.com/Acme/App.git") == "github.com/acme/app"


def test_build_records_files_commands_and_the_last_test_result(repo, tmp_path):
    r, _ = repo
    card = resume.build(_transcript(tmp_path / "t.jsonl"), "s1", str(r), host="claude-code")
    assert card["files"] == ["/repo/examples/dify-support/workflow.yml"]
    assert card["last_check"]["command"].startswith("python3 -m pytest") and card["last_check"]["result"].startswith("4 passed")
    assert card["last_check"]["head"] == card["head"] and card["draft"] is True and card["task"] == ""
    assert resume.save(card) and resume.load(card["key"])["session"] == "s1"


def test_staleness_names_the_commit_the_check_ran_on(repo, tmp_path):
    r, git = repo
    card = resume.build(_transcript(tmp_path / "t.jsonl"), "s1", str(r))
    assert "exact commit" in resume.staleness(card, r)
    (r / "a.py").write_text("x = 2\n"); git("commit", "-qam", "two")
    st = resume.staleness(card, r)
    assert "1 commit(s) later" in st and card["last_check"]["head"] in st
    block = resume.render(card, r)
    assert "describes the earlier state" in block and "Sources: session s1" in block


def test_load_for_finds_the_card_from_another_worktree_and_says_so(repo, tmp_path):
    r, git = repo
    card = resume.build(_transcript(tmp_path / "t.jsonl"), "s1", str(r)); resume.save(card)
    wt = tmp_path / "wt-feature"
    git("worktree", "add", "-q", "-b", "feature/orca", str(wt))
    found, relation = resume.load_for(wt)
    assert found["key"] == card["key"] and relation == "other-branch"
    assert "newest card for the same repository" in resume.render(found, wt, relation)
    assert "from branch main" in resume.headline(found, relation)
    old = dict(card, key="old", ts=time.time() - 30 * 86400); resume.save(old)
    assert resume.load_for(tmp_path / "nowhere")[1] == "none"


def test_draft_is_a_draft_until_confirmed_and_is_not_redrafted_after(repo, tmp_path):
    r, _ = repo
    card = resume.build(_transcript(tmp_path / "t.jsonl"), "s1", str(r))
    assert resume.needs_draft(card)
    resume.apply_draft(card, {"task": "Prepare the Dify example", "done": ["three workflows"], "remaining": ["import into Dify"]})
    assert card["draft"] and "agent draft" in resume.render(card, r) and not resume.needs_draft(card)
    resume.confirm(card, task="Prepare the Dify example for publication", done=["three workflows", "API round-trip checked"], remaining=["import into Dify"])
    assert card["draft"] is False and "agent draft" not in resume.render(card, r)
    again = resume.build(_transcript(tmp_path / "t.jsonl"), "s2", str(r), previous=card)
    assert again["task"] == "Prepare the Dify example for publication" and again["draft"] is False
    assert not resume.needs_draft(again)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None


def test_stop_hook_writes_the_card_even_without_an_account(repo, tmp_path, monkeypatch):
    r, _ = repo
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "")
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    t = _transcript(tmp_path / "t.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s7", "transcript_path": str(t), "cwd": str(r),
                                                              "last_assistant_message": "Local tests pass; the Dify import is not checked yet."})))
    out = io.StringIO(); monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit):
        cli.cmd_auto_save(_Args(every=1))
    card, rel = resume.load_for(r)
    assert rel == "exact" and card["session"] == "s7" and card["last_check"]["result"].startswith("4 passed")


def test_session_start_injects_the_card_first(repo, tmp_path, monkeypatch):
    r, _ = repo
    card = resume.build(_transcript(tmp_path / "t.jsonl"), "s1", str(r))
    resume.apply_draft(card, {"task": "Prepare the Dify example", "done": [], "remaining": ["import into Dify"]}); resume.save(card)
    monkeypatch.setattr(cli, "_load_cloud_api_key", lambda: "")
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "new", "source": "startup", "cwd": str(r)})))
    out = io.StringIO(); monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit):
        cli.cmd_auto_context(_Args(no_weekly=True))
    d = json.loads(out.getvalue())
    ctx = d["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("[Mengram resume") and "import into Dify" in ctx and "agent draft" in ctx
    assert "Mengram resume" in d.get("systemMessage", "")


def test_files_written_from_the_shell_count_and_an_in_flight_test_does_not_hide_the_last_result(repo, tmp_path):
    r, _ = repo
    assert resume.files_from_commands(["cat > local/resume.py <<'EOF'\nx\nEOF", "sed -i '' 's/a/b/' cli.py && echo ok > /dev/null",
                                      "python3 x.py | tee results/e1.log"]) == ["local/resume.py", "cli.py", "results/e1.log"]
    lines = [
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q tests/"}}]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "652 passed, 15 deselected in 10.29s"}]}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest -q tests/test_new.py"}}]}}),
    ]
    p = tmp_path / "t2.jsonl"; p.write_text("\n".join(lines) + "\n")
    lc = resume.last_test_result(p)
    assert lc["command"] == "pytest -q tests/" and lc["result"].startswith("652 passed")


def test_render_shows_the_test_command_not_the_heredoc_behind_it(repo, tmp_path):
    r, _ = repo
    card = resume.build(_transcript(tmp_path / "t.jsonl"), "s1", str(r))
    card["last_check"]["command"] = "cd x && python3 - <<'EOF'\nlots of code\nEOF"
    block = resume.render(card, r)
    assert "Last check: `cd x && python3 - <<'EOF'` →" in block and "lots of code" not in block
