import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import usiphus_evidence as evidence


def packet():
    return {
        "sources": [{
            "source_id": "guideline-1", "canonical_url": "https://example.org/guideline",
            "title": "Clinical guideline", "source_type": "professional_guideline",
            "published_on": "2025-01-01", "guideline_version": "2025",
            "licence_or_reuse_status": "permission_confirmed",
            "review_status": "faculty_reviewed", "access_status": "full_text_reviewed",
        }],
        "snippets": [{
            "snippet_id": "snippet-1", "source_id": "guideline-1",
            "text": "The guideline recommends the documented approach.",
            "locator": "Section 4.2", "matched_objective_ids": ["topic-1-LO01"],
        }],
    }


class EvidenceTests(unittest.TestCase):
    def test_reviewed_source_and_located_snippet_are_eligible(self):
        result = evidence.validate_evidence_packet(packet())
        self.assertTrue(result["evidence_ready"])
        self.assertEqual(result["eligible_snippet_count"], 1)

    def test_metadata_only_source_is_not_evidence(self):
        value = packet()
        value["sources"][0]["access_status"] = "metadata_only"
        result = evidence.validate_evidence_packet(value)
        self.assertFalse(result["evidence_ready"])

    def test_open_access_flag_without_full_text_review_is_not_evidence(self):
        value = packet()
        value["sources"][0]["access_status"] = "open_access_flag_article_licence_check_required"
        result = evidence.validate_evidence_packet(value)
        self.assertFalse(result["evidence_ready"])

    def test_arbitrary_rights_note_does_not_pass_as_permission(self):
        value = packet()
        value["sources"][0]["licence_or_reuse_status"] = "probably fine to reuse"
        result = evidence.validate_evidence_packet(value)
        self.assertFalse(result["evidence_ready"])
        self.assertTrue(any(
            "reuse/access rights" in error
            for error in result["source_errors"]["guideline-1"]
        ))

    def test_cc_by_claim_without_confirmed_rights_does_not_pass(self):
        value = packet()
        value["sources"][0]["licence_or_reuse_status"] = (
            "CC BY 4.0 stated by article; independently verify"
        )
        result = evidence.validate_evidence_packet(value)
        self.assertFalse(result["evidence_ready"])

    def test_candidate_source_cannot_be_made_eligible_by_rights_metadata(self):
        value = packet()
        value["sources"][0]["review_status"] = "candidate_unreviewed"
        value["sources"][0]["licence_or_reuse_status"] = "permission_confirmed"
        result = evidence.validate_evidence_packet(value)
        self.assertFalse(result["evidence_ready"])

    def test_claim_must_match_source_and_locator(self):
        card = {"prompt": "What is the principle?", "answer": "A sufficiently detailed educational answer.",
                "evidence_claims": [{"claim_id": "c1", "text": "The stated recommendation.",
                                    "source_id": "guideline-1", "snippet_id": "snippet-1",
                                    "locator": "Wrong section"}]}
        result = evidence.validate_card_evidence(card, packet())
        self.assertFalse(result["valid"])
        self.assertTrue(any("locator" in x for x in result["errors"]))

    def test_high_risk_claim_without_mapping_fails(self):
        card = {"prompt": "What is the first-line emergency action?",
                "answer": "Give 5 mg immediately.", "evidence_claims": []}
        result = evidence.validate_card_evidence(card, packet())
        self.assertFalse(result["valid"])
        self.assertTrue(result["high_risk_flags"])


    def test_packet_loader_attaches_topic_packet_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            value = packet()
            value.update({"topic_id": "topic-1", "specialty": "ophthalmology"})
            from pathlib import Path
            (Path(directory) / "topic-1.json").write_text(
                json.dumps(value), encoding="utf-8"
            )
            curriculum = {
                "specialty": "ophthalmology",
                "topics": [
                    {"id": "topic-1", "name": "Topic one"},
                    {"id": "topic-2", "name": "Topic two"},
                ],
            }
            inventory = evidence.load_topic_evidence_packets(curriculum, directory)
            self.assertEqual(inventory["loaded_topic_ids"], ["topic-1"])
            self.assertEqual(inventory["topics_without_packets"], ["topic-2"])
            self.assertTrue(curriculum["topics"][0]["evidence_packet"])

    def test_preflight_rejects_candidate_packet_before_inference(self):
        value = packet()
        value["sources"][0]["review_status"] = "candidate_unreviewed"
        with self.assertRaisesRegex(ValueError, "No model request was made"):
            evidence.preflight_topic_evidence({
                "id": "topic-1", "evidence_packet": value
            })

    def test_objective_map_exposes_evidence_gaps(self):
        result = evidence.build_objective_evidence_map(
            [{"id": "topic-1", "learning_objectives": ["Objective one", "Objective two"]}],
            packet())
        self.assertEqual(result["objectives_with_candidate_evidence"], 1)
        self.assertEqual(result["objectives_with_evidence_gaps"], ["topic-1-LO02"])


if __name__ == "__main__":
    unittest.main()
