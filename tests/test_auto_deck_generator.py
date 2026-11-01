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
                model="test-model", api_key="test", count=1)
        self.assertEqual(cards[0]["card_id"], "AI-CATARACT-001")
        self.assertEqual(cards[0]["evidence_status"], "unreviewed")


if __name__ == "__main__":
    unittest.main()
