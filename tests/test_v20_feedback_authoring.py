import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import usiphus_v20 as v20

def fixture():
    return {"schema_version":1,
      "feedback":[{"feedback_id":"F1","role":"learner","purpose":"Improve synthetic teaching material","notice_acknowledged":True,"visibility":"faculty_only","submitted_at":"2026-11-01T10:00:00Z","retention_until":"2027-11-01T10:00:00Z","text":"The synthetic case needs a clearer explanation.","confidence":3}],
      "actions":[{"action_id":"A1","feedback_ids":["F1"],"title":"Revise explanation","next_step":"Draft clearer rationale","owner_label":"faculty-editor","status":"open","created_at":"2026-11-01T11:00:00Z","due_at":"2026-11-08T11:00:00Z"}],
      "artifacts":[{"artifact_id":"CARD-1","version":"1.0.0","artifact_type":"card","title":"Synthetic card","status":"draft","content":{"body":"Synthetic educational text"}}],
      "comments":[{"comment_id":"C1","artifact_id":"CARD-1","version":"1.0.0","text":"Please add an evidence reference."}]}

class V20Tests(unittest.TestCase):
    def test_clean_workflow_is_ready_for_faculty_review(self):
        self.assertEqual(v20.build_report(fixture())["publication_gate"],"ready_for_faculty_review")
    def test_reports_do_not_echo_feedback_or_comment_text(self):
        p=fixture(); r=v20.build_report(p)
        self.assertNotIn(p["feedback"][0]["text"],v20.canonical(r))
        self.assertNotIn(p["comments"][0]["text"],v20.canonical(r))
    def test_possible_email_is_flagged(self):
        p=fixture(); p["feedback"][0]["text"]="Email student@example.org"
        self.assertTrue(any("possible personal data" in x["message"] for x in v20.build_report(p)["issues"]))
    def test_feedback_requires_notice_and_retention(self):
        p=fixture(); p["feedback"][0]["notice_acknowledged"]=False
        self.assertEqual(v20.build_report(p)["publication_gate"],"blocked")
    def test_action_must_link_existing_feedback(self):
        p=fixture(); p["actions"][0]["feedback_ids"]=["missing"]
        self.assertEqual(v20.build_report(p)["publication_gate"],"blocked")
    def test_done_action_requires_followup_evidence(self):
        p=fixture(); p["actions"][0].update(status="done",completion_evidence="",follow_up_outcome="")
        self.assertEqual(v20.build_report(p)["publication_gate"],"blocked")
    def test_version_diff_is_explicit(self):
        p=fixture(); p["artifacts"].append({"artifact_id":"CARD-1","version":"1.1.0","artifact_type":"card","title":"Synthetic card","status":"in_review","content":{"body":"Revised"}})
        self.assertEqual(v20.build_report(p)["version_diffs"][0]["changed_fields"],["body"])
    def test_numeric_version_order_is_not_lexicographic(self):
        p=fixture()
        p["artifacts"].extend([
            {"artifact_id":"CARD-1","version":"1.10.0","artifact_type":"card","title":"Synthetic card","status":"draft","content":{"body":"ten"}},
            {"artifact_id":"CARD-1","version":"1.2.0","artifact_type":"card","title":"Synthetic card","status":"draft","content":{"body":"two"}}
        ])
        diffs=v20.build_report(p)["version_diffs"]
        pair=next(x for x in diffs if x["from_version"]=="1.2.0")
        self.assertEqual(pair["to_version"],"1.10.0")
    def test_comments_must_reference_exact_version(self):
        p=fixture(); p["comments"][0]["version"]="9.9"
        self.assertEqual(v20.build_report(p)["publication_gate"],"blocked")
    def test_approved_artifact_needs_review_metadata(self):
        p=fixture(); p["artifacts"][0]["status"]="approved"
        self.assertEqual(v20.build_report(p)["publication_gate"],"blocked")
    def test_timezone_is_required(self):
        p=fixture(); p["feedback"][0]["submitted_at"]="2026-11-01T10:00:00"
        self.assertEqual(v20.build_report(p)["publication_gate"],"blocked")
    def test_future_schema_rejected(self):
        p=fixture(); p["schema_version"]=2
        with self.assertRaises(v20.V20Error): v20.build_report(p)
    def test_retention_count_requires_explicit_as_of(self):
        p=fixture()
        self.assertIsNone(v20.build_report(p)["summary"]["feedback"]["retention_due_count"])
        p["as_of"]="2027-01-01T00:00:00Z"
        self.assertEqual(v20.build_report(p)["summary"]["feedback"]["retention_due_count"],0)

if __name__=="__main__": unittest.main()
