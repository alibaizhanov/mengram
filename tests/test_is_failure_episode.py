"""Regression tests for #62 — is_failure_episode negator-veto bug.

A positive word about a different run / the local env / a workaround must not
suppress a real failure from the evolution path.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
_spec = importlib.util.spec_from_file_location("evo", Path(__file__).parent.parent / "cloud" / "evolution.py")
_evo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_evo)
is_failure = _evo.EvolutionEngine.is_failure_episode


def f(text):
    return is_failure("neutral", outcome=text)


# --- the three false-negatives from #62 (must now be True) ---
def test_workaround_is_failure():
    assert f("deploy crashed, worked around it by rolling back") is True

def test_negator_about_a_different_run():
    assert f("the migration failed; a previous run had worked") is True

def test_negator_describes_local_env_not_outcome():
    assert f("tests passing locally but production returns 500") is True


# --- resolution cases must stay non-failures ---
def test_resolved_is_not_failure():
    assert f("fixed the error, all good now") is False

def test_clean_success_is_not_failure():
    assert f("no error, deploy succeeded") is False
    assert f("deployment successful, no issues") is False


# --- plain failures / non-events unchanged ---
def test_plain_failure():
    assert f("server crashed, data lost") is True

def test_reverted_causing_outage():
    assert f("the reverted change caused an outage") is True

def test_non_event():
    assert f("just a normal note about the weather") is False


# --- valence primary signals untouched ---
def test_valence_signals():
    assert is_failure("negative") is True
    assert is_failure("positive") is False
