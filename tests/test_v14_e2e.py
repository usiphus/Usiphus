
"""Synthetic V14 integration tests.

These fixtures are fictional software-test data, not clinical teaching content.
Run with: python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "usiphus_v14.py"
LEARNER_ID = "123e4567-e89b-42d3-a456-426614174000"


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class V14EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="usiphus-v14-e2e-")
        self.root = Path(self.temp.name)
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        self.output = self.root / "build" / "v14-release"
        self.config_path = self.fixtures / "v14.config.json"

        write_json(self.fixtures / "cards.json", {
            "cards": [{
                "card_id": "TEST-001",
                "specialty": "urology",
                "title": "Fictional integration-test card"
            }]
        })
        write_json(self.fixtures / "curriculum.json", {
            "specialty": "urology",
            "competencies": [{
                "competency_id": "U1",
                "title": "Fictional competency",
                "required": True,
                "weight": 1.0
            }]
        })
        write_json(self.fixtures / "mapping.json", {
            "specialty": "urology",
            "candidates": [{
                "card_id": "TEST-001",
                "competency_id": "U1",
                "status": "verified",
                "method": "faculty-approved",
                "reviewer": "synthetic-test-reviewer",
                "reviewed_at": "2026-10-31T12:00:00Z"
            }]
        })
        write_json(self.fixtures / "evidence.json", {
            "claims": [{
                "claim_id": "TEST-E1",
                "card_id": "TEST-001",
                "claim": "Fictional test claim; not a clinical statement.",
                "reference": "Fictional test reference",
                "source_type": "guideline",
                "status": "approved",
                "reviewed_on": "2026-10-31",
                "reviewer": "synthetic-test-reviewer",
                "audit_trail": [
                    {"to": "in_review", "actor": "synthetic-test-reviewer",
                     "date": "2026-10-30"},
                    {"to": "approved", "actor": "synthetic-test-reviewer",
                     "date": "2026-10-31"}
                ]
            }]
        })
        write_json(self.fixtures / "learner_progress.json", {
            "specialty": "urology",
            "accepted_attempts": 1,
            "learners": [{
                "learner_id": LEARNER_ID,
                "competencies": [{
                    "competency_id": "U1",
                    "mastery_estimate": 0.7
                }]
            }]
        })
        write_json(self.fixtures / "cohort_analytics.json", {
            "specialty": "urology",
            "minimum_cohort_size": 5,
            "competencies": [{
                "competency_id": "U1",
                "suppressed": True,
                "suppression_reason": "Synthetic cohort below threshold"
            }]
        })
        (self.fixtures / "review_schedule.csv").write_text(
            "learner_id,competency_id,next_due_at,recommended_interval_days,status\n"
            f"{LEARNER_ID},U1,2026-11-01T12:00:00Z,4,learning\n",
            encoding="utf-8"
        )
        self.input_paths = [
            self.fixtures / name for name in (
                "cards.json", "curriculum.json", "mapping.json", "evidence.json",
                "learner_progress.json", "cohort_analytics.json", "review_schedule.csv"
            )
        ]
        write_json(self.config_path, {
            "schema_version": 1,
            "inputs": {path.stem: path.name for path in self.input_paths},
            "output_dir": "../build/v14-release",
            "as_of": "2026-10-31",
            "max_evidence_age_days": 730,
            "run_regression_tests": False
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(self.config_path)],
            cwd=ROOT, text=True, capture_output=True, check=False
        )

    def test_complete_pipeline_publishes_ready_bundle(self) -> None:
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.input_paths}
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = [
            "v12/platform_report.json",
            "v12/faculty_review_queue.csv",
            "v12/release_checklist.json",
            "v12/v12_manifest.json",
            "v13_validation_report.json",
            "release_summary.json",
            "release_report.md",
            "v14_manifest.json"
        ]
        for relative in expected:
            self.assertTrue((self.output / relative).is_file(), relative)

        summary = json.loads((self.output / "release_summary.json").read_text())
        validation = json.loads(
            (self.output / "v13_validation_report.json").read_text())
        checklist = json.loads(
            (self.output / "v12/release_checklist.json").read_text())
        manifest = json.loads((self.output / "v14_manifest.json").read_text())
        self.assertEqual(summary["release_status"], "ready")
        self.assertEqual(validation["validation_status"], "passed")
        self.assertTrue(checklist["release_ready"])
        self.assertEqual(len(manifest["inputs"]), 7)

        v12_text = "".join(
            (self.output / "v12" / name).read_text()
            for name in ("platform_report.json", "faculty_review_queue.csv",
                         "release_checklist.json", "v12_manifest.json")
        )
        self.assertNotIn(LEARNER_ID, v12_text)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.input_paths}
        self.assertEqual(before, after, "V14 must not modify source artifacts")

    def test_failed_second_run_preserves_previous_release(self) -> None:
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        manifest_path = self.output / "v14_manifest.json"
        previous_manifest = manifest_path.read_bytes()
        previous_summary = (self.output / "release_summary.json").read_bytes()

        mapping_path = self.fixtures / "mapping.json"
        mapping = json.loads(mapping_path.read_text())
        mapping["candidates"][0]["competency_id"] = "UNKNOWN"
        write_json(mapping_path, mapping)

        failed = self.run_cli()
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertEqual(manifest_path.read_bytes(), previous_manifest)
        self.assertEqual(
            (self.output / "release_summary.json").read_bytes(), previous_summary
        )

    def test_rejects_output_directory_that_would_replace_inputs(self) -> None:
        config = json.loads(self.config_path.read_text())
        config["output_dir"] = "."
        write_json(self.config_path, config)
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertTrue((self.fixtures / "cards.json").is_file())
        self.assertFalse(self.output.exists())


    def test_rejects_output_directory_that_is_parent_of_external_input(self) -> None:
        source_dir = self.root / "external-source"
        source_dir.mkdir()
        write_json(source_dir / "cards.json", {
            "cards": [{
                "card_id": "TEST-001",
                "specialty": "urology",
                "title": "Fictional integration-test card"
            }]
        })
        config = json.loads(self.config_path.read_text())
        config["inputs"]["cards"] = "../external-source/cards.json"
        config["output_dir"] = "../external-source"
        write_json(self.config_path, config)

        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertTrue((source_dir / "cards.json").is_file())


if __name__ == "__main__":
    unittest.main()
