import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import usiphus_v15 as v15

ROOT = Path(__file__).resolve().parents[1]


class V15GovernanceTests(unittest.TestCase):
    def test_cli_rejects_malformed_inputs_without_writing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [
                ('{"sources":', '{"claims": []}'),
                (json.dumps({"sources": "not-a-list"}), '{"claims": []}'),
            ]

            for index, (sources_data, claims_data) in enumerate(cases):
                with self.subTest(case=index):
                    sources = root / f"sources-{index}.json"
                    claims = root / f"claims-{index}.json"
                    output = root / f"output-{index}"
                    sources.write_text(sources_data, encoding="utf-8")
                    claims.write_text(claims_data, encoding="utf-8")
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "usiphus_v15.py"),
                            "--sources", str(sources),
                            "--claims", str(claims),
                            "--output-dir", str(output),
                        ],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("ERROR |", result.stderr)
                    self.assertFalse(output.exists())

    def test_hash_chain_detects_tampering(self):
        event = v15.build_event("0" * 64, "reviewer", "approve",
                                "2026-10-31T12:00:00Z", {"claim_id": "C1"})
        self.assertEqual(v15.verify_hash_chain([event]), [])
        self.assertTrue(v15.verify_hash_chain([dict(event, action="reject")]))

    def test_legacy_reference_requires_manual_mapping_in_strict_mode(self):
        result = v15.analyze([], [{
            "claim_id": "C1", "card_id": "CARD1", "claim": "synthetic",
            "reference": "legacy citation", "status": "draft"
        }], as_of=date(2026, 10, 31), max_age_days=730,
           minimum_reviewers=1, require_registered_sources=True)
        self.assertEqual(result["legacy_reference_claims"], 1)
        self.assertTrue(any("legacy reference" in " ".join(x["issues"])
                            for x in result["findings"]))

    def test_source_to_card_impact_index(self):
        source = {"source_id":"S1","title":"Example","source_type":"guideline",
                  "authority":"publisher","jurisdiction":"XX","version":"1","status":"active"}
        claim = {"claim_id":"C1","card_id":"CARD1","claim":"synthetic",
                 "references":[{"source_id":"S1","version":"1","jurisdiction":"XX"}],
                 "status":"draft"}
        result = v15.analyze([source], [claim], as_of=date(2026,10,31),
            max_age_days=730, minimum_reviewers=1, require_registered_sources=True)
        self.assertEqual(result["impact_index"], {"S1":["CARD1"]})
        self.assertEqual(result["governance_status"], "checks_passed")

    def test_reviewer_quorum_is_distinct(self):
        source = {"source_id":"S1","title":"Example","source_type":"guideline",
                  "authority":"publisher","jurisdiction":"XX","version":"1"}
        claim = {"claim_id":"C1","card_id":"CARD1","claim":"synthetic",
                 "references":[{"source_id":"S1"}],"status":"approved",
                 "reviewed_on":"2026-10-31",
                 "review_decisions":[{"reviewer_id":"R1","decision":"approve"}]}
        result = v15.analyze([source], [claim], as_of=date(2026,10,31),
            max_age_days=730, minimum_reviewers=2, require_registered_sources=True)
        self.assertTrue(any("distinct approving reviewer" in " ".join(x["issues"])
                            for x in result["findings"]))

    def test_supersession_cycle_is_flagged(self):
        common = {"title":"Example","source_type":"guideline",
                  "authority":"publisher","jurisdiction":"XX"}
        sources = [dict(common, source_id="A", status="superseded", superseded_by="B"),
                   dict(common, source_id="B", status="superseded", superseded_by="A")]
        result = v15.analyze(sources, [], as_of=date(2026,10,31),
            max_age_days=730, minimum_reviewers=1, require_registered_sources=True)
        self.assertTrue(any("cycle" in " ".join(x["issues"]) for x in result["findings"]))


if __name__ == "__main__":
    unittest.main()
