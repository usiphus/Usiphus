import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import usiphus_v19 as v19

def fixture():
    return {
        "schema_version": 1,
        "blueprint": [{"objective_id":"OBJ-1","specialty":"Urology","competency_id":"U1","cognitive_level":"apply","assessment_method":"MCQ"}],
        "mcqs": [{"item_id":"Q1","stem":"A synthetic adult patient presents with a defined clinical scenario for teaching purposes.","lead_in":"Which is the most appropriate next step?","options":[{"label":"A","text":"Option A","correct":True,"rationale":"Best answer"},{"label":"B","text":"Option B","rationale":"Less appropriate"},{"label":"C","text":"Option C","rationale":"Not supported"}],"answer_rationale":"Synthetic rationale.","objective_ids":["OBJ-1"],"evidence_ref":"SRC-1"}],
        "osce_stations": [], "challenges": [], "external_directory": [], "item_statistics": []
    }

class V19Tests(unittest.TestCase):
    def test_valid_mcq_is_ready_for_faculty_review(self):
        self.assertEqual(v19.analyze(fixture())["publication_gate"], "ready_for_faculty_review")
    def test_multiple_correct_options_block(self):
        p=fixture(); p["mcqs"][0]["options"][1]["correct"]=True
        self.assertEqual(v19.analyze(p)["publication_gate"], "blocked")
    def test_duplicate_stems_are_candidates_not_deleted(self):
        p=fixture(); p["mcqs"].append(dict(p["mcqs"][0], item_id="Q2"))
        report=v19.analyze(p)
        self.assertEqual(len(report["duplicate_candidates"]), 1)
        self.assertEqual(len(p["mcqs"]), 2)
    def test_osce_requires_station_materials(self):
        p=fixture(); p["osce_stations"]=[{"station_id":"OSCE-1"}]
        self.assertEqual(v19.analyze(p)["publication_gate"], "blocked")
    def test_challenge_media_requires_rights_and_provenance(self):
        p=fixture(); p["challenges"]=[{"challenge_id":"C1","case_stem":"Synthetic case","clues":["Clue 1"],"accepted_answers":["Answer"],"explanation":"Why it fits","media":"image.png"}]
        report=v19.analyze(p)
        self.assertTrue(any("media requires explicit license" in x["message"] for x in report["issues"]))
    def test_directory_cannot_embed_without_rights(self):
        p=fixture(); p["external_directory"]=[{"entry_id":"D1","provider":"Provider","url":"https://example.org","reuse_status":"link_only","embed_allowed":True}]
        self.assertEqual(v19.analyze(p)["publication_gate"], "blocked")
    def test_embedded_credentials_rejected(self):
        p=fixture(); p["external_directory"]=[{"entry_id":"D1","provider":"Provider","url":"https://user:pass@example.org","reuse_status":"unknown"}]
        self.assertEqual(v19.analyze(p)["publication_gate"], "blocked")
    def test_small_cohort_statistics_are_labeled_low_n(self):
        p=fixture(); p["item_statistics"]=[{"item_id":"Q1","item_version":"1","cohort_size":8,"correct_count":5,"missing_count":1}]
        stat=v19.analyze(p)["item_statistics"][0]
        self.assertEqual(stat["interpretation"], "descriptive_only_low_n")
        self.assertIn("No validity", stat["limitations"])
    def test_future_schema_rejected(self):
        p=fixture(); p["schema_version"]=2
        with self.assertRaises(v19.V19Error): v19.analyze(p)

if __name__ == "__main__":
    unittest.main()
