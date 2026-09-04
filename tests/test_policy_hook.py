"""Execution policy from a procedure's record — pure logic plus the hook's JSON."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import cli
from cloud import policy


DEPLOY = {
    "id": "p1", "name": "deploy to Railway", "version": 3,
    "trigger_condition": "a change lands on main",
    "steps": [
        {"action": "push to main", "detail": "the webhook does the rest"},
        {"action": "watch the boot log"},
        {"action": "verify /health", "detail": "expect 200 within 60s"},
    ],
}


def _with(counts, **extra):
    p = dict(DEPLOY, **extra)
    p["success_count"], p["fail_count"] = counts
    return p


# --- what counts as a workflow command -------------------------------------

def test_lookup_commands_are_not_workflows():
    assert not policy.looks_like_workflow("ls -la")
    assert not policy.looks_like_workflow("cat README.md")
    assert not policy.looks_like_workflow("")


def test_workflow_commands_match_default_pattern():
    for cmd in ("git push origin main", "railway up", "twine upload dist/*",
                "kubectl apply -f k8s/", "rm -rf build", "alembic migrate head"):
        assert policy.looks_like_workflow(cmd), cmd


def test_pattern_override_and_wildcard():
    assert policy.looks_like_workflow("make lint", pattern=r"\bmake\b")
    assert not policy.looks_like_workflow("git push", pattern=r"\bmake\b")
    assert policy.looks_like_workflow("anything at all", pattern=".*")


def test_bad_pattern_falls_back_to_default():
    assert policy.looks_like_workflow("git push", pattern="([")


# --- the lexical guard on top of semantic search ---------------------------

def test_procedure_must_share_a_word_with_the_command():
    assert policy.procedure_matches(DEPLOY, "git push origin main && curl /health")
    assert not policy.procedure_matches(DEPLOY, "kubectl get pods")


def test_best_match_skips_unrelated_results():
    unrelated = {"name": "rotate API keys", "steps": [{"action": "open dashboard"}]}
    assert policy.best_match([unrelated, DEPLOY], "git push origin main") is DEPLOY
    assert policy.best_match([unrelated], "git push origin main") is None


# --- the decision ---------------------------------------------------------

def test_untested_asks():
    v = policy.decide(_with((0, 0)), "git push origin main")
    assert v and v["decision"] == "ask"
    assert "never been run" in v["reason"]
    assert "untested" in v["reliability"]


def test_inherited_record_asks():
    # A revision with no runs of its own, standing on the previous version.
    v = policy.decide(_with((0, 0), reliability="61% expected"), "git push origin main")
    assert v and "no runs of its own" in v["reason"]
    assert "61%" in v["reason"]


def test_below_bar_asks_and_names_the_counts():
    v = policy.decide(_with((2, 3)), "git push origin main")
    assert v and "below the 70% bar" in v["reason"]
    assert "2✓/3✗" in v["reason"]


def test_proven_record_is_silent():
    assert policy.decide(_with((11, 1)), "git push origin main") is None


def test_bar_is_configurable():
    proc = _with((11, 1))                      # 86% reliable
    assert policy.decide(proc, "git push origin main", min_reliable=90) is not None
    assert policy.decide(proc, "git push origin main", min_reliable=80) is None


def test_unrelated_procedure_never_asks():
    assert policy.decide(_with((0, 0)), "kubectl get pods") is None


def test_reliability_is_taken_from_the_record_when_present():
    # The server's label wins over a local recompute, so one record means one thing.
    assert policy.reliability_of({"reliability": "42% reliable", "success_count": 9}) == "42% reliable"
    assert policy.reliability_of({"success_count": 0, "fail_count": 0}) == "untested"


def test_plan_carries_the_last_failure_when_known():
    proc = _with((2, 3), last_failure="the pool was cold", last_failed="2026-07-30")
    v = policy.decide(proc, "git push origin main")
    assert "Last failure: 2026-07-30: the pool was cold" in v["plan"]


def test_plan_carries_steps_and_preconditions():
    proc = _with((0, 0), metadata={"preconditions": ["migrations applied"]})
    v = policy.decide(proc, "git push origin main")
    assert "1. push to main — the webhook does the rest" in v["plan"]
    assert "Preconditions: migrations applied" in v["plan"]


# --- the hook's JSON -------------------------------------------------------

def test_hook_output_is_empty_when_allowed():
    assert policy.hook_output(None) is None


def test_hook_output_shape_for_ask():
    v = policy.decide(_with((0, 0)), "git push origin main")
    out = policy.hook_output(v)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "ask"
    assert hso["permissionDecisionReason"].startswith("Mengram:")
    assert "[Mengram] Matched learned workflow" in hso["additionalContext"]
    assert "systemMessage" not in out


def test_hook_never_denies():
    for counts in ((0, 0), (0, 5), (1, 9)):
        v = policy.decide(_with(counts), "git push origin main")
        assert v["decision"] == "ask"


# --- the CLI command end to end (no network) --------------------------------

class _FakeMem:
    def __init__(self, api_key=None, base_url=None):
        self.calls = []

    def procedures(self, query=None, limit=20, user_id="default"):
        self.calls.append(query)
        return [_with((0, 0))]


def _run_hook(monkeypatch, capsys, payload, env=None, argv=()):
    monkeypatch.setenv("MENGRAM_API_KEY", "k")
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    monkeypatch.delenv("MENGRAM_POLICY_PATTERN", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    import cloud.client
    monkeypatch.setattr(cloud.client, "CloudMemory", _FakeMem)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["mengram", "auto-policy", *argv])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    return capsys.readouterr().out.strip()


def _bash(command):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}}


def test_cli_asks_for_untested_workflow(monkeypatch, capsys):
    out = _run_hook(monkeypatch, capsys, _bash("git push origin main"))
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_cli_is_silent_for_lookup_commands(monkeypatch, capsys):
    assert _run_hook(monkeypatch, capsys, _bash("ls -la")) == ""


def test_cli_is_silent_for_other_tools(monkeypatch, capsys):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read",
               "tool_input": {"file_path": "/x"}}
    assert _run_hook(monkeypatch, capsys, payload) == ""


def test_cli_is_silent_on_garbage_input(monkeypatch, capsys):
    monkeypatch.setenv("MENGRAM_API_KEY", "k")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    monkeypatch.setattr(sys, "argv", ["mengram", "auto-policy"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_verbose_marker(monkeypatch, capsys):
    out = _run_hook(monkeypatch, capsys, _bash("ls"), argv=("--verbose",))
    assert json.loads(out)["systemMessage"].startswith("[mengram:auto-policy]")


def test_cli_local_memfmt_folder(monkeypatch, capsys, tmp_path):
    memfmt = pytest.importorskip("memfmt")
    if not hasattr(memfmt, "load"):
        pytest.skip("memfmt is a namespace package here, not the library")
    (tmp_path / "procedures").mkdir()
    (tmp_path / "procedures" / "deploy.md").write_text(
        "---\nmemfmt_type: procedure\nversion: 2\nsuccess_count: 0\nfail_count: 0\n---\n\n"
        "# deploy to Railway (v2 · untested)\n\n**When** — a change lands on main\n\n"
        "## Steps\n\n1. push to main\n2. verify /health\n"
    )
    out = _run_hook(monkeypatch, capsys, _bash("git push origin main"),
                    env={"MENGRAM_MEMORY_DIR": str(tmp_path)})
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "deploy to Railway" in data["hookSpecificOutput"]["additionalContext"]


# --- install wiring ---------------------------------------------------------

def test_install_adds_pretooluse_group_with_bash_matcher():
    settings = {}
    cli._upsert_hook(settings, "PreToolUse", "mengram auto-policy",
                     {"type": "command", "command": "mengram auto-policy", "timeout": 10},
                     matcher="Bash")
    group = settings["hooks"]["PreToolUse"][0]
    assert group["matcher"] == "Bash"
    assert group["hooks"][0]["command"] == "mengram auto-policy"


def test_upsert_keeps_existing_matcher_on_update():
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash|Edit", "hooks": [
        {"type": "command", "command": "mengram auto-policy --verbose"}]}]}}
    found = cli._upsert_hook(settings, "PreToolUse", "mengram auto-policy",
                             {"type": "command", "command": "mengram auto-policy"},
                             matcher="Bash")
    assert found
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash|Edit"
