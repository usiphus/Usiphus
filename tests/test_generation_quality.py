import unittest
from unittest.mock import patch

from usiphus_auto_deck import generate_validated_topic_cards
from usiphus_generation_quality import (
    build_generation_coverage_report,
    validate_curriculum_blueprint,
    validate_topic_cards,
)


OBJECTIVES = [
    "Explain core anatomy and mechanisms relevant to this topic.",
    "Assess the patient and construct a prioritized differential diagnosis.",
    "Select investigations and outline safe evidence-informed management.",
]


def topic():
    return {
        "id": "urology-01",
        "name": "Urologic assessment",
        "domain_id": "urology-foundations",
        "domain": "Urologic foundations",
        "learning_objectives": OBJECTIVES,
        "coverage_tags": ["urology assessment"],
        "exam_emphasis": ["diagnosis", "investigations", "management"],
        "reference_sources": [],
        "card_count": 6,
    }


def cards():
    rows = []
    kinds = ["basic", "mcq", "mcq", "clinical_case", "viva", "osce"]
    for index, kind in enumerate(kinds):
        row = {
            "prompt": f"How should a clinician approach urology assessment task {index + 1}?",
            "answer": "This structured explanation links the clinical finding to the objective and outlines the relevant reasoning safely.",
            "learning_objective": OBJECTIVES[index % 3],
            "card_type": kind,
            "difficulty": "intermediate",
            "cognitive_level": "apply",
            "exam_domain": "clinical reasoning",
            "tags": ["urology assessment"],
            "references": [],
        }
        if kind == "mcq":
            row.update({
                "options": ["Option alpha", "Option beta", "Option gamma", "Option delta"],
                "correct_option": "Option alpha",
                "answer": "Option alpha is the best answer because it follows the stated clinical reasoning and addresses the relevant objective without unsupported assumptions.",
            })
        if kind == "clinical_case":
            row["prompt"] = (
                "A fictional adult presents with a focused urological symptom, "
                "relevant examination findings and stable observations. What is the "
                "most appropriate next assessment step?"
            )
        if kind == "viva":
            row["answer"] = (
                "I would structure the answer around the key definition, relevant "
                "mechanism, focused assessment, differential diagnosis, investigations "
                "and safe management principles."
            )
        if kind == "osce":
            row["prompt"] = (
                "OSCE station: You are the candidate. Assess the patient, explain "
                "your findings and communicate the safe next step:
            )
            row["rubric"] = [
                "Introduces the task and confirms the patient's identity using the scenario.",
                "Obtains the relevant focused history and explains the examination.",
                "Summarizes findings and communicates a safe next step.",
            ]
        rows.append(row)
    return rows


class GenerationQualityTests(unittest.TestCase):
    def test_curriculum_preflight_counts_domains_topics_and_objectives(self):
        curriculum = {
            "specialty": "urology",
            "curriculum_domains": [{"id": "urology-foundations"}],
            "topics": [topic()],
        }
        result = validate_curriculum_blueprint(curriculum)
        self.assertEqual(result["topic_count"], 1)
        self.assertEqual(result["objective_count"], 3)

    def test_curriculum_preflight_rejects_topic_without_objectives(self):
        curriculum = {
            "specialty": "urology",
            "curriculum_domains": [{"id": "urology-foundations"}],
            "topics": [{**topic(), "learning_objectives": []}],
        }
        with self.assertRaisesRegex(ValueError, "learning_objectives"):
            validate_curriculum_blueprint(curriculum)

    def test_topic_validation_enforces_mix_and_objective_coverage(self):
        rows = cards()
        result = validate_topic_cards(
            topic(), rows, specialty="urology",
            requested_mix={"basic": 1, "mcq": 2, "clinical_case": 1, "viva": 1, "osce": 1},
        )
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["objectives_covered"], 3)
        self.assertTrue(all(card["evidence_status"] == "unreviewed" for card in rows))

    def test_topic_validation_rejects_missing_objective_coverage(self):
        rows = cards()
        for row in rows:
            row["learning_objective"] = OBJECTIVES[0]
        with self.assertRaisesRegex(ValueError, "no generated card covers objectives"):
            validate_topic_cards(
                topic(), rows, specialty="urology",
                requested_mix={"basic": 1, "mcq": 2, "clinical_case": 1, "viva": 1, "osce": 1},
            )

    def test_topic_validation_rejects_wrong_exam_mix(self):
        rows = cards()
        rows[0]["card_type"] = "mcq"
        rows[0]["options"] = ["Option alpha", "Option beta", "Option gamma", "Option delta"]
        rows[0]["correct_option"] = "Option alpha"
        rows[0]["answer"] = (
            "Option alpha is the best answer because it follows the stated clinical "
            "reasoning and addresses the relevant objective without unsupported assumptions."
        )
        with self.assertRaisesRegex(ValueError, "requested exam-format mix"):
            validate_topic_cards(
                topic(), rows, specialty="urology",
                requested_mix={"basic": 1, "mcq": 2, "clinical_case": 1, "viva": 1, "osce": 1},
            )


    def test_generator_retries_with_specific_quality_feedback(self):
        invalid = cards()
        invalid[0]["learning_objective"] = "An unrelated objective."
        valid = cards()
        with patch(
            "usiphus_auto_deck.generate_topic_cards",
            side_effect=[invalid, valid],
        ) as generate:
            generated, report = generate_validated_topic_cards(
                topic(), specialty="urology", level="medical students",
                model="test-model", ollama_url="http://localhost:11434",
                count=6, max_output_tokens=2048,
            )
        self.assertTrue(report["coverage_complete"])
        self.assertEqual(len(generated), 6)
        self.assertEqual(generate.call_count, 2)
        self.assertIn("_quality_feedback", generate.call_args_list[1].args[0])

    def test_coverage_report_identifies_missing_curriculum_topics(self):
        curriculum = {"specialty": "urology", "topics": [topic(), {**topic(), "id": "urology-02"}]}
        report = build_generation_coverage_report(
            curriculum,
            [{"topic_id": "urology-01", "coverage_complete": True, "objectives_covered": 3}],
            cards(),
        )
        self.assertFalse(report["all_topics_covered"])
        self.assertEqual(report["topic_gaps"], ["urology-02"])


if __name__ == "__main__":
    unittest.main()
