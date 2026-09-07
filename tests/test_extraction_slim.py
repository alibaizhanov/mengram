"""Offline checks for #7; real model quality is gated by the extraction evals."""
import json
import unittest
from unittest.mock import Mock, patch

from engine.extractor import conversation_extractor as ce
from evals.run_extraction_evals import build_extractor, run_case


class SlimTests(unittest.TestCase):
    def test_selection_and_unchanged_schema(self):
        empty = {key: [] for key in ("entities", "relations", "knowledge", "episodes", "procedures")}
        client = Mock()
        client.complete.return_value = json.dumps(empty)
        extractor = ce.ConversationExtractor(client)
        for version, template in [("v1", ce.EXTRACTION_PROMPT), ("v2", ce.EXTRACTION_PROMPT_V2),
                                  ("slim", ce.EXTRACTION_PROMPT_SLIM), ("unknown", ce.EXTRACTION_PROMPT)]:
            extractor.extract([{"role": "user", "content": "hello"}], prompt_version=version)
            client.complete.assert_called_with(
                template.format(existing_context="", conversation="User: hello"),
                response_format=ce.EXTRACTION_SCHEMA,
            )
        with patch.object(ce, "EXTRACTION_PROMPT_VERSION", "slim"):
            extractor.extract_from_text("hello")
            self.assertIn("all five arrays", client.complete.call_args.args[0])
        client.complete.side_effect = [RuntimeError("no structured output"), json.dumps(empty)]
        extractor.extract([{"role": "user", "content": "hello"}], prompt_version="slim")
        self.assertEqual(client.complete.call_args.kwargs, {})
        self.assertIn("Procedures require", client.complete.call_args.args[0])

    def test_truncated_output_cannot_pass_as_empty(self):
        client = Mock()
        extractor = ce.ConversationExtractor(client)
        case = {"conversation": [], "require_complete_json": True, "expect_no_output": True}
        for raw in ['{"entities": [', '{}']:
            client.complete.return_value = raw
            self.assertTrue(run_case(extractor, case, prompt_version="slim"))
        client.complete.return_value = json.dumps({key: [] for key in
            ("entities", "relations", "knowledge", "episodes", "procedures")})
        self.assertEqual(run_case(extractor, case, prompt_version="slim"), [])
        malformed = json.loads(client.complete.return_value)
        malformed["entities"] = [{"name": "User", "type": "person", "facts": ["wrong type"]}]
        client.complete.return_value = json.dumps(malformed)
        self.assertTrue(any("schema violation" in f for f in run_case(
            extractor, {"conversation": [], "require_complete_json": True}, prompt_version="slim")))
        client.complete.return_value = '{"episodes": [{"summary": "fabricated"}]}'
        self.assertTrue(run_case(extractor, {"conversation": [], "expect_no_output": True}))

    def test_bleed_through_in_relations_is_rejected(self):
        client = Mock()
        client.complete.return_value = json.dumps({"relations": [{
            "from": "User", "to": "Railway", "type": "uses", "description": "invented"
        }]})
        failures = run_case(ce.ConversationExtractor(client), {
            "conversation": [], "must_not_extract": ["railway"]})
        self.assertTrue(any("forbidden keyword" in f for f in failures))

    def test_ollama_model_reaches_existing_client(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "ollama", "LLM_MODEL": "phi4-mini:3.8b",
                                      "OLLAMA_BASE_URL": "http://localhost:11434"}):
            extractor = build_extractor()
        self.assertEqual(extractor.llm.model, "phi4-mini:3.8b")


if __name__ == "__main__":
    unittest.main()
