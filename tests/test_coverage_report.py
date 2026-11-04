import unittest

from scripts.coverage_report import compute_coverage


class IndependentCoverageReportTests(unittest.TestCase):
    def setUp(self):
        self.curriculum = {"specialty": "urology", "topics": [{
            "id": "uro-1",
            "name": "Foundations",
            "learning_objectives": ["Explain anatomy", "Assess the patient"],
        }]}

    def test_reports_complete_objective_coverage(self):
        generated = {"cards": [
            {"card_id": "c1", "topic_id": "uro-1", "learning_objective": "Explain anatomy", "card_type": "basic"},
            {"card_id": "c2", "topic_id": "uro-1", "learning_objective": "Assess the patient", "card_type": "mcq"},
        ]}
        report = compute_coverage(self.curriculum, generated)
        self.assertEqual(report["summary"]["objective_coverage_percent"], 100.0)
        self.assertEqual(report["topic_gaps"], [])
        self.assertFalse(report["clinical_accuracy_verified"])

    def test_flags_uncovered_objective(self):
        generated = {"cards": [
            {"card_id": "c1", "topic_id": "uro-1", "learning_objective": "Explain anatomy", "card_type": "basic"},
        ]}
        report = compute_coverage(self.curriculum, generated)
        self.assertEqual(report["summary"]["objective_coverage_percent"], 50.0)
        self.assertEqual(report["topic_gaps"], ["uro-1"])

    def test_flags_unknown_topic_and_duplicate_card_id(self):
        generated = {"cards": [
            {"card_id": "same", "topic_id": "uro-1", "learning_objective": "Explain anatomy"},
            {"card_id": "same", "topic_id": "missing", "learning_objective": "unknown"},
        ]}
        report = compute_coverage(self.curriculum, generated)
        self.assertTrue(any("Duplicate card_id" in x for x in report["errors"]))
        self.assertTrue(any("unknown/missing topic_id" in x for x in report["errors"]))

    def test_rejects_duplicate_objectives_in_curriculum(self):
        curriculum = {"topics": [{
            "id": "uro-1", "learning_objectives": ["Same", " same "]
        }]}
        with self.assertRaisesRegex(ValueError, "duplicate learning objectives"):
            compute_coverage(curriculum, {"cards": []})


if __name__ == "__main__":
    unittest.main()
