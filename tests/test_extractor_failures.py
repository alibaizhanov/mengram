"""How extraction fails (audit 2026-10-23).

A refusal must not crash, a timeout must not be paid for twice, cut-off JSON
must surface as a failure rather than "nothing to remember", and one bad
window must not cost the rest of the conversation. cloud.api connects to a
database on import, so its side is checked through the source."""
import json
import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.extractor import conversation_extractor as ce  # noqa: E402
from engine.extractor.conversation_extractor import (  # noqa: E402
    ConversationExtractor, ExtractionParseError, LLMClient)

API = (Path(__file__).parent.parent / "cloud" / "api.py").read_text()

GOOD = json.dumps({"entities": [{"name": "Ali", "type": "person",
                                 "facts": [{"fact": "Builds Mengram", "when": None}]}],
                   "relations": [], "knowledge": [], "episodes": [],
                   "procedures": [{"name": "Deploy", "trigger": "",
                                   "steps": [{"step": 1, "action": "push", "detail": ""}],
                                   "entities": []}]})
CONV = [{"role": "user", "content": "I build Mengram"}]


class StubLLM(LLMClient):
    """Replies (or raises) from a script, recording whether a schema was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, prompt, system="", response_format=None):
        self.calls.append({"schema": response_format is not None, "prompt": prompt})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class APITimeoutError(Exception):
    """Same class name as openai's / anthropic's timeout error."""


class RateLimitError(Exception):
    status_code = 429


class BadRequestError(Exception):
    status_code = 400


def test_refusal_is_an_empty_extraction_not_a_crash(caplog):
    llm = StubLLM(None)  # OpenAI refusal: message.content is None
    with caplog.at_level(logging.WARNING, logger="mengram"):
        result = ConversationExtractor(llm).extract(CONV)
    assert result.entities == [] and result.procedures == []
    assert len(llm.calls) == 1
    assert "no extraction content" in caplog.text


def test_empty_string_is_an_empty_extraction():
    assert ConversationExtractor(StubLLM("   ")).extract(CONV).entities == []


@pytest.mark.parametrize("err", [APITimeoutError("timed out"), RateLimitError("slow down"),
                                 TimeoutError("socket"), ConnectionError("reset")])
def test_transport_errors_propagate_without_a_second_call(err):
    llm = StubLLM(err, GOOD)
    with pytest.raises(type(err)):
        ConversationExtractor(llm).extract(CONV)
    assert len(llm.calls) == 1


def test_rejected_schema_falls_back_to_plain_completion():
    llm = StubLLM(BadRequestError("response_format unsupported"), GOOD)
    result = ConversationExtractor(llm).extract(CONV)
    assert [c["schema"] for c in llm.calls] == [True, False]
    assert result.entities[0].name == "Ali"
    # steps stay dicts
    assert result.procedures[0].steps == [{"step": 1, "action": "push", "detail": ""}]


def test_unparseable_output_is_retried_once_without_schema():
    llm = StubLLM('{"entities": [{"name": "Al', GOOD)
    result = ConversationExtractor(llm).extract(CONV)
    assert [c["schema"] for c in llm.calls] == [True, False]
    assert result.entities[0].name == "Ali"


def test_truncated_output_twice_raises_with_window_size(caplog):
    cut = GOOD[: len(GOOD) // 2]
    llm = StubLLM(cut, cut)
    with caplog.at_level(logging.WARNING, logger="mengram"):
        with pytest.raises(ExtractionParseError):
            ConversationExtractor(llm).extract(CONV)
    assert len(llm.calls) == 2
    assert "1 messages" in caplog.text and "truncated" in caplog.text


def test_json_that_is_not_an_object_is_unparseable():
    with pytest.raises(ExtractionParseError):
        ConversationExtractor(StubLLM("[1, 2]", "[]")).extract(CONV)


@pytest.mark.parametrize("given, used", [("v2", "v2"), ("V2", "v2"), (" v2 ", "v2"),
                                         ("v1", "v1"), ("v3", "v1")])
def test_prompt_version_is_normalized(given, used, caplog):
    llm = StubLLM(GOOD)
    with caplog.at_level(logging.WARNING, logger="mengram"):
        ConversationExtractor(llm).extract(CONV, prompt_version=given)
    v2_marker = ce.EXTRACTION_PROMPT_V2.split("{")[0][:200]
    assert (llm.calls[0]["prompt"].startswith(v2_marker)) == (used == "v2")
    assert ("Unknown extraction prompt_version" in caplog.text) == (given == "v3")


def test_prompt_templates_differ():
    # The version test above relies on the two prompts having distinct openings.
    assert ce.EXTRACTION_PROMPT_V2.split("{")[0][:200] != ce.EXTRACTION_PROMPT.split("{")[0][:200]


# ---- cloud/api.py: the windowed pipeline ----------------------------------

def _pipeline_src():
    start = API.index("def _run_extraction_pipeline(")
    return API[start:API.index("\n    @app.post", start)]


def test_a_failing_window_is_skipped_not_fatal():
    src = _pipeline_src()
    call = src.index("win_extraction = extractor.extract(")
    assert src.rfind("try:", 0, call) > src.index("for win_start in range(")
    handler = src[call:src.index("all_episodes.extend(win_extraction.episodes)")]
    assert "except Exception" in handler and "_windows_failed += 1" in handler and "continue" in handler


def test_all_windows_failing_still_fails_the_job():
    src = _pipeline_src()
    check = src.index("_windows_failed == _windows_total")
    # checked before anything is saved after the loop, and it raises into fail_job
    assert check < src.index("save_conversation_chunk")
    assert "raise RuntimeError" in src[check:check + 300]
    assert '"failed_windows": _windows_failed' in src


# ---- cloud/api.py: inputs reach the pipeline as many messages --------------

def test_add_file_sends_one_message_per_chunk():
    start = API.index("def process_file_in_background():")
    body = API[start:API.index("_run_extraction_pipeline(", start)]
    assert "for i, page_text in enumerate(page_texts):" in body
    assert "conversation.append(" in body
    assert "combined_text" not in body


def test_vapi_transcript_is_chunked():
    start = API.index("async def vapi_save(")
    body = API[start:API.index("threading.Thread(target=process_in_background", start)]
    assert re.search(r"for c in chunk_text\(_sanitize_text\(transcript\), 4000\)", body)
