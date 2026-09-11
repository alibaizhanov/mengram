"""The hosted client must not outlive the web worker waiting on it.

Production serves the API with gunicorn `--timeout 300` and two workers. The
OpenAI SDK defaults to a 600 second timeout and two retries, so a single slow
completion can hold a worker for half an hour. On 2026-09-11 two did, four
seconds apart, and the API went down: a user polling `/v1/profile` hit a
completion that never returned, and with two workers there was nothing left.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.extractor import llm_client as mod


class _FakeOpenAI:
    last_kwargs = None

    def __init__(self, **kwargs):
        _FakeOpenAI.last_kwargs = kwargs
        self.chat = None


@pytest.fixture
def fake_sdk(monkeypatch):
    import types
    fake = types.ModuleType("openai")
    fake.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    _FakeOpenAI.last_kwargs = None
    return _FakeOpenAI


def test_a_timeout_is_always_set(fake_sdk):
    mod.OpenAIClient(api_key="k")
    assert fake_sdk.last_kwargs["timeout"] == mod.OpenAIClient.DEFAULT_TIMEOUT


def test_retries_are_bounded(fake_sdk):
    mod.OpenAIClient(api_key="k")
    assert fake_sdk.last_kwargs["max_retries"] == 1


def test_the_worst_case_fits_inside_the_worker_budget():
    """Two attempts plus backoff must land under gunicorn's 300 s."""
    worst = mod.OpenAIClient.DEFAULT_TIMEOUT * (mod.OpenAIClient.DEFAULT_MAX_RETRIES + 1)
    assert worst < 300, f"{worst}s would outlive the worker"


def test_the_start_script_still_uses_the_budget_this_assumes():
    """If the worker timeout changes, this test should be the thing that notices."""
    start = (Path(__file__).parent.parent / "start.sh").read_text()
    assert "--timeout 300" in start


def test_the_caller_can_override(fake_sdk):
    mod.OpenAIClient(api_key="k", timeout=5.0, max_retries=0)
    assert fake_sdk.last_kwargs["timeout"] == 5.0
    assert fake_sdk.last_kwargs["max_retries"] == 0


def test_the_factory_passes_a_configured_timeout_through(fake_sdk):
    mod.create_llm_client({"provider": "openai",
                           "openai": {"api_key": "k", "timeout": 12, "max_retries": 0}})
    assert fake_sdk.last_kwargs["timeout"] == 12.0
    assert fake_sdk.last_kwargs["max_retries"] == 0


def test_the_factory_default_is_still_bounded(fake_sdk):
    mod.create_llm_client({"provider": "openai", "openai": {"api_key": "k"}})
    assert fake_sdk.last_kwargs["timeout"] == mod.OpenAIClient.DEFAULT_TIMEOUT
