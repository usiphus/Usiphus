import json
import tempfile
import unittest
from pathlib import Path

import usiphus_competencies
from usiphus_generate import GenerationError
from usiphus_v10 import load_competencies


class CompetencyGeneratorTests(unittest.TestCase):
    def test_validate_adds_v10_compatible_unvalidated_competencies(self):
        response = {"competencies": [{
            "title": "Assess eye health",
            "description": "Assess eye health using source-supported principles.",
            "domain": "Clinical assessment",
            "level": "apply",
            "required": True,
            "weight": 1,
            "learning_objectives": ["Describe a source-supported assessment."],
            "source_ids": ["SRC-001"],
        }]}
        sources = {"SRC-001": {
            "title": "Local study notes",
            "reference": "https://example.org/notes",
        }}
        rows = usiphus_competencies.validate_competencies(
            json.dumps(response), "ophthalmology", sources, 1, 10,
        )
        self.assertEqual(rows[0]["id"], "AUTO-OPHTHALMOLOGY-0001")
        self.assertEqual(rows[0]["source_references"], ["https://example.org/notes"])
        self.assertEqual(rows[0]["competency_status"], "unvalidated_draft")
        self.assertTrue(rows[0]["required"])

    def test_unknown_source_is_rejected(self):
        response = {"competencies": [{
            "title": "Assess eye health",
            "description": "Assess eye health.",
            "domain": "Clinical assessment",
            "level": "apply",
            "required": True,
            "weight": 1,
            "learning_objectives": ["Describe assessment."],
            "source_ids": ["SRC-404"],
        }]}
        with self.assertRaises(GenerationError):
            usiphus_competencies.validate_competencies(
                json.dumps(response), "ophthalmology", {}, 1, 10,
            )

    def test_generation_is_incremental_and_stops_after_empty_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "notes.md"
            output = root / "curriculum.json"
            source.write_text("A source-grounded competency fact.", encoding="utf-8")
            config = {
                "specialty": "ophthalmology",
                "source_material": [{
                    "title": "Study notes",
                    "reference": "https://example.org/notes",
                    "path": str(source),
                }],
                "output": str(output),
                "base_curriculum": None,
                "batch_size": 5,
                "max_no_progress_batches": 2,
                "max_batches": None,
                "generator": {
                    "base_url": "http://localhost:11434",
                    "model": "test",
                },
            }
            calls = 0

            def fake_generator(*_args):
                nonlocal calls
                calls += 1
                if calls > 1:
                    return json.dumps({"competencies": []})
                return json.dumps({"competencies": [{
                    "title": "Assess eye health",
                    "description": "Assess eye health using source-supported principles.",
                    "domain": "Clinical assessment",
                    "level": "apply",
                    "required": True,
                    "weight": 1,
                    "learning_objectives": ["Describe a source-supported assessment."],
                    "source_ids": ["SRC-001"],
                }]})

            self.assertEqual(usiphus_competencies.run(config, fake_generator), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(calls, 3)
            self.assertEqual(result["generation_status"], "draft_unvalidated")
            self.assertEqual(len(result["competencies"]), 1)
            self.assertEqual(result["competencies"][0]["competency_status"], "unvalidated_draft")
            specialty, parsed = load_competencies(output)
            self.assertEqual(specialty, "ophthalmology")
            self.assertEqual(parsed[0].competency_id, "AUTO-OPHTHALMOLOGY-0001")


if __name__ == "__main__":
    unittest.main()
