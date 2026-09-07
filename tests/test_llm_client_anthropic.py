"""AnthropicClient against SDK 1.x response shapes: a ThinkingBlock may come first."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.extractor.llm_client import _anthropic_text, AnthropicClient  # noqa: E402


def test_text_is_taken_from_text_blocks_not_the_thinking_block():
    resp = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="let me think"),
        SimpleNamespace(type="text", text='{"entities": []}'),
    ])
    assert _anthropic_text(resp) == '{"entities": []}'


def test_plain_text_only_response_still_works():
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="OK")])
    assert _anthropic_text(resp) == "OK"


def test_complete_does_not_send_temperature(monkeypatch):
    calls = {}

    class _Messages:
        def create(self, **kw):
            calls.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")])

    c = AnthropicClient.__new__(AnthropicClient)
    c.client = SimpleNamespace(messages=_Messages())
    c.model = "claude-sonnet-5"
    assert c.complete("x") == "hi"
    assert "temperature" not in calls and calls["model"] == "claude-sonnet-5"
