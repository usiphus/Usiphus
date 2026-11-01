import sys
import unittest
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import usiphus_v17 as v17

class V17Tests(unittest.TestCase):
    def setUp(self):
        self.curriculum = {"specialty":"urology","competencies":[
            {"id":"U1","title":"Retention","domain":"Emergency","level":"apply","required":True,"weight":2,"learning_objectives":["Recognize retention"]},
            {"id":"U2","title":"Stones","domain":"Endourology","level":"understand","required":True,"weight":1,"learning_objectives":["Explain stones"]}]}
        self.cards = [{"card_id":"C1","specialty":"urology","prompt":"Acute retention"}]
        self.mapping = {"candidates":[{"card_id":"C1","competency_id":"U1","status":"verified","method":"faculty-approved","reviewer":"reviewer-label","reviewed_at":"2026-01-01T10:00:00Z"}]}
    def test_duplicate_ids(self):
        self.assertFalse(v17.validate_contract("card",[{"card_id":"C1","prompt":"a"},{"card_id":"c1","prompt":"b"}])["valid"])
    def test_migration_preserves_payload(self):
        data={"specialty":"urology","competencies":[]}
        self.assertEqual(v17.migrate_document("curriculum",data)["data"],data)
    def test_verified_requires_provenance(self):
        report,_,_=v17.analyze_curriculum(self.curriculum,self.cards,self.mapping)
        self.assertEqual(report["summary"]["verified_required_competencies"],1)
        bad={"candidates":[{"card_id":"C1","competency_id":"U1","status":"verified"}]}
        report,_,_=v17.analyze_curriculum(self.curriculum,self.cards,bad)
        self.assertEqual(report["summary"]["verified_required_competencies"],0)
    def test_conflict_excluded(self):
        rows={"candidates":self.mapping["candidates"]+[{"card_id":"C1","competency_id":"U1","status":"pending"}]}
        report,_,_=v17.analyze_curriculum(self.curriculum,self.cards,rows)
        self.assertEqual(report["summary"]["verified_required_competencies"],0)
        self.assertEqual(report["summary"]["mapping_conflicts"],1)
    def test_impact(self):
        old={"specialty":"urology","competencies":[{"id":"U1","title":"Old"}]}
        new={"specialty":"urology","competencies":[{"id":"U1","title":"New"}]}
        _,impact,_=v17.analyze_curriculum(new,self.cards,self.mapping,[{"item_id":"Q1","competency_ids":["U1"]}],[{"station_id":"S1","competency_ids":["U1"]}],old)
        self.assertEqual(impact["affected_artifacts"]["cards"],["C1"])
        self.assertEqual(impact["affected_artifacts"]["assessment_items"],["Q1"])
        self.assertEqual(impact["affected_artifacts"]["osce_stations"],["S1"])

    def test_exact_duplicate_prompts_are_review_candidates(self):
        cards = {
            "C1": {"prompt": "Acute urinary retention"},
            "C2": {"prompt": "  acute   urinary RETENTION "},
            "C3": {"prompt": "Different topic"},
        }
        groups = v17.find_exact_duplicate_cards(cards)
        self.assertEqual(groups[0]["card_ids"], ["C1", "C2"])
        self.assertTrue(groups[0]["review_required"])

    def test_dependency_graph_contains_assessment_edges(self):
        report, _, _ = v17.analyze_curriculum(
            self.curriculum, self.cards, self.mapping,
            [{"item_id": "Q1", "competency_ids": ["U1"]}],
        )
        self.assertTrue(any(edge["relationship"] == "assessed_by"
                            for edge in report["dependency_graph"]["edges"]))

    def test_pilot_metrics_aggregate_only_consented_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.csv"
            path.write_text(
                "workflow_id,participant_role,task,started_at,completed_at,mapping_corrections,"
                "evidence_traceability_issue,release_reproducible,friction_note,consented_for_aggregate\n"
                "W1,faculty,review,2026-01-01T10:00:00Z,2026-01-01T10:30:00Z,2,yes,no,Needs clearer labels,yes\n"
                "W2,learner,practice,2026-01-01T11:00:00Z,2026-01-01T11:10:00Z,0,no,yes,,no\n",
                encoding="utf-8",
            )
            summary = v17.summarize_pilot_metrics(path)
            self.assertEqual(summary["consented_task_rows"], 1)
            self.assertEqual(summary["excluded_without_aggregate_consent"], 1)
            self.assertEqual(summary["median_duration_minutes"], 30.0)
            self.assertEqual(summary["mapping_corrections"], 2)
            self.assertEqual(summary["tasks_with_traceability_issues"], 1)
            self.assertEqual(summary["release_reproducibility_rate"], 0.0)

    def test_manifest_diff(self):
        self.assertEqual(v17.compare_release_manifests({"a":1},{"a":2})["changed_field_count"],1)

if __name__ == "__main__":
    unittest.main()
