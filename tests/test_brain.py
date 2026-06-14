"""Tests for MengramBrain graceful handling of AllModelsFailedError."""

import pytest

from engine.brain import MengramBrain
from engine.extractor.conversation_extractor import ExtractionResult
from engine.extractor.llm_client import AllModelsFailedError, LLMClient


class AlwaysFailsExtractor:
    """Stand-in for ConversationExtractor whose .extract() always raises."""

    def extract(self, conversation, existing_context="", prompt_version=None):
        raise AllModelsFailedError("all models failed: ['a/model', 'b/model']")


class _NullLLMClient(LLMClient):
    def complete(self, prompt, system="", response_format=None):
        return "{}"


def test_remember_skips_extraction_when_all_models_fail(tmp_path):
    brain = MengramBrain(vault_path=str(tmp_path), llm_client=_NullLLMClient(), use_vectors=False)
    brain.extractor = AlwaysFailsExtractor()

    result = brain.remember([{"role": "user", "content": "hello"}])

    assert result is not None
