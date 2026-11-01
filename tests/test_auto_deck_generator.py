import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usiphus_auto_deck as generator


class AutomatedDeckGeneratorTests(unittest.TestCase):
    def test_curriculum_requires_specialty_and_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({"specialty": "ophthalmology", "topics": [{"name": "Cataract"}]}))
            self.assertEqual(generator.load_curriculum(path)["specialty"], "ophthalmology")

    def test_curriculum_rejects_empty_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({"specialty": "ophthalmology", "topics": []}))
            with self.assertRaises(ValueError):
                generator.load_curriculum(path)

    def test_json_extraction_handles_fences(self):
        value = generator._extract_json('```json\n{"cards": []}\n```')
        self.assertEqual(value, {"cards": []})

    def test_topic_cards_get_stable_ids_and_unreviewed_status(self):
        response = {"choices": [{"message": {"content": json.dumps({"cards": [
            {"prompt": "What is cataract?", "answer": "An opacity of the crystalline lens."}
        ]})}}]}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _limit): return json.dumps(response).encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()):
            cards = generator.generate_topic_cards(
                {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                model="test-model", api_key="test", count=1, max_output_tokens=1024)
        self.assertEqual(cards[0]["card_id"], "AI-CATARACT-001")
        self.assertEqual(cards[0]["evidence_status"], "unreviewed")

    def test_cli_uses_curriculum_defaults(self):
        args = generator.parse_args(["--generate", "--curriculum", "curriculum.json"])
        self.assertIsNone(args.specialty)
        self.assertIsNone(args.level)
        self.assertEqual(args.max_api_calls, 11)
        self.assertEqual(args.max_output_tokens, 2500)

    def test_generation_rejects_more_cards_than_requested(self):
        response = {"choices": [{"message": {"content": json.dumps({"cards": [
            {"prompt": "Q1?", "answer": "Answer one is sufficiently long."},
            {"prompt": "Q2?", "answer": "Answer two is sufficiently long."}
        ]})}}]}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _limit): return json.dumps(response).encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(ValueError, "requested at most 1"):
                generator.generate_topic_cards(
                    {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                    model="test-model", api_key="test", count=1, max_output_tokens=1024)

    def test_api_call_limit_blocks_before_network_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({
                "specialty": "ophthalmology",
                "topics": [{"name": "A"}, {"name": "B"}]
            }))
            args = generator.parse_args([
                "--generate", "--curriculum", str(path),
                "--max-api-calls", "1", "--output-dir", str(Path(tmp) / "out")
            ])
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
                with patch.object(generator.urllib.request, "urlopen") as request:
                    with self.assertRaisesRegex(ValueError, "max-api-calls"):
                        generator.build(args)
                    request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
