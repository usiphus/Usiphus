import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import usiphus_v18 as v18


class V18Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources = [{
            "source_id": "SRC-1", "title": "Synthetic guideline",
            "source_type": "guideline", "publisher": "Synthetic College",
            "jurisdiction": "GB", "version": "1", "status": "active",
            "last_reviewed_at": "2026-01-01T00:00:00Z", "review_due_days": 365,
            "url": "https://example.org/guideline#section",
        }]
        self.claims = [{
            "claim_id": "CL-1", "card_id": "CARD-1", "claim": "Synthetic claim",
            "references": [{"source_id": "SRC-1", "jurisdiction": "GB"}],
        }]
        self.as_of = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def run_analysis(self, **kwargs):
        return v18.analyze(self.sources, self.claims, source_root=self.root,
                           as_of=self.as_of, **kwargs)

    def test_canonical_url_normalizes_host_port_and_fragment(self):
        self.assertEqual(v18.canonical_url("HTTPS://Example.org:443/a/#x"),
                         "https://example.org/a")

    def test_withdrawn_source_is_top_priority(self):
        self.sources[0]["status"] = "withdrawn"
        report = self.run_analysis()
        self.assertEqual(report["review_queue"][0]["event_type"], "withdrawn_source")

    def test_content_change_is_review_candidate(self):
        old = {"schema_version": 1, "sources": {"SRC-1": {
            "source_id": "SRC-1", "title": "Synthetic guideline",
            "publisher": "Synthetic College", "jurisdiction": "GB",
            "source_type": "guideline", "version": "1", "status": "active",
            "canonical_url": "https://example.org/guideline",
            "content_sha256": "a" * 64, "published_on": "",
        }}}
        self.sources[0]["content_sha256"] = "b" * 64
        report = self.run_analysis(previous_state=old)
        self.assertTrue(any(x["change_type"] == "source_content_changed"
                            for x in report["source_changes"]))

    def test_impact_reaches_claim_card_competency_assessment_osce(self):
        report = self.run_analysis(
            cards=[{"card_id": "CARD-1", "competency_ids": ["U1"]}],
            assessments=[{"item_id": "Q1", "competency_ids": ["U1"]}],
            osce=[{"station_id": "S1", "competency_ids": ["U1"]}],
        )
        impact = report["source_impact"]["SRC-1"]
        self.assertEqual(impact["claims"], ["CL-1"])
        self.assertEqual(impact["assessment_items"], ["Q1"])
        self.assertEqual(impact["osce_stations"], ["S1"])

    def test_jurisdiction_mismatch_is_queued(self):
        self.claims[0]["references"][0]["jurisdiction"] = "US"
        report = self.run_analysis()
        self.assertTrue(any(x["event_type"] == "jurisdiction_mismatch"
                            for x in report["review_queue"]))

    def test_duplicate_source_candidates_are_not_merged(self):
        self.sources.append(dict(self.sources[0], source_id="SRC-2"))
        report = self.run_analysis()
        self.assertTrue(report["duplicate_source_candidates"])
        self.assertEqual(len(report["next_state"]["sources"]), 2)

    def test_source_path_traversal_is_rejected(self):
        self.sources[0]["content_path"] = "../outside.txt"
        with self.assertRaises(v18.V18Error):
            self.run_analysis()

    def test_decision_requires_rationale_and_timezone(self):
        decision = {"target_type": "claim", "target_id": "CL-1",
                    "decision": "accepted", "reviewer_label": "faculty-label",
                    "rationale": "Reviewed"}
        with self.assertRaises(v18.V18Error):
            self.run_analysis(decisions=[decision])

    def test_decision_audit_chain_is_linked(self):
        decision = {"target_type": "claim", "target_id": "CL-1",
                    "decision": "deferred", "reviewer_label": "faculty-label",
                    "rationale": "Awaiting review", "decided_at": "2026-10-31T10:00:00Z"}
        report = self.run_analysis(decisions=[decision])
        self.assertEqual(report["decision_audit_events"][0]["previous_hash"], "0" * 64)
        self.assertEqual(len(report["decision_audit_events"][0]["event_hash"]), 64)


if __name__ == "__main__":
    unittest.main()
