"""V16 governed-release integration tests using synthetic fixtures only."""
from __future__ import annotations
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from test_v14_e2e import V14EndToEndTests, write_json

class V16GovernedReleaseTests(V14EndToEndTests):
    def configure_v15(self, *, mode="strict", sources=None, claims=None, minimum_reviewers=1):
        source_rows = sources if sources is not None else [{
            "source_id":"S1","title":"Synthetic source","source_type":"guideline",
            "authority":"synthetic-publisher","jurisdiction":"TEST","version":"1","status":"active"}]
        claim_rows = claims if claims is not None else [{
            "claim_id":"C1","card_id":"TEST-001","claim":"Synthetic test claim",
            "references":[{"source_id":"S1","version":"1","jurisdiction":"TEST"}],"status":"draft"}]
        write_json(self.fixtures/"v15_sources.json",{"sources":source_rows})
        write_json(self.fixtures/"v15_claims.json",{"claims":claim_rows})
        config=json.loads(self.config_path.read_text(encoding="utf-8"))
        config.update({"v15_sources":"v15_sources.json","v15_claims":"v15_claims.json",
            "v15_evidence_mode":mode,"v15_block_on_failure":True,
            "v15_minimum_reviewers":minimum_reviewers,
            "v15_require_signed_audit":False,"v15_require_audit_anchor":False})
        write_json(self.config_path,config)

    def test_legacy_migration_is_explicit_and_manifested(self):
        self.configure_v15(mode="legacy",sources=[],claims=[{
            "claim_id":"C1","card_id":"TEST-001","claim":"Synthetic",
            "references":[{"legacy_reference":"Old free-text reference"}],"status":"draft"}])
        result=self.run_cli()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        summary=json.loads((self.output/"release_summary.json").read_text())
        manifest=json.loads((self.output/"v14_manifest.json").read_text())
        governance=json.loads((self.output/"v15/v15_governance_report.json").read_text())
        self.assertEqual(summary["v15_evidence_mode"],"legacy")
        self.assertEqual(governance["legacy_reference_claims"],1)
        self.assertEqual(manifest["release_policy"]["v15_evidence_mode"],"legacy")
        self.assertIn("v15_governance_report.json",manifest["governance_report_hashes"])

    def test_strict_mode_rejects_legacy_reference(self):
        self.configure_v15(mode="strict",sources=[],claims=[{
            "claim_id":"C1","card_id":"TEST-001","claim":"Synthetic",
            "references":[{"legacy_reference":"Old free-text reference"}],"status":"draft"}])
        result=self.run_cli()
        self.assertEqual(result.returncode,2)
        self.assertFalse(self.output.exists())

    def test_withdrawn_and_superseded_sources_block(self):
        for status in ("withdrawn","superseded"):
            with self.subTest(status=status):
                self.tearDown(); self.setUp()
                rows=[{"source_id":"S1","title":"Old","source_type":"guideline","authority":"synthetic",
                       "jurisdiction":"TEST","status":status,
                       **({"superseded_by":"S2"} if status=="superseded" else {})},
                      {"source_id":"S2","title":"New","source_type":"guideline","authority":"synthetic",
                       "jurisdiction":"TEST","status":"active"}]
                self.configure_v15(sources=rows)
                result=self.run_cli()
                self.assertEqual(result.returncode,2)
                self.assertFalse(self.output.exists())

    def test_unknown_source_and_supersession_cycle_block(self):
        self.configure_v15(claims=[{"claim_id":"C1","card_id":"TEST-001","claim":"Synthetic",
                                    "references":[{"source_id":"MISSING"}],"status":"draft"}])
        result=self.run_cli()
        self.assertEqual(result.returncode,2)
        self.tearDown(); self.setUp()
        rows=[{"source_id":"A","title":"A","source_type":"guideline","authority":"x","jurisdiction":"TEST",
               "status":"superseded","superseded_by":"B"},
              {"source_id":"B","title":"B","source_type":"guideline","authority":"x","jurisdiction":"TEST",
               "status":"superseded","superseded_by":"A"}]
        self.configure_v15(sources=rows,claims=[])
        self.assertEqual(self.run_cli().returncode,2)

    def test_insufficient_quorum_blocks(self):
        spec=importlib.util.spec_from_file_location("v15_test_module",Path(__file__).resolve().parents[1]/"usiphus_v15.py")
        module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
        first=module.build_event("0"*64,"r1","submit","2026-10-30T10:00:00Z",{"to":"in_review"})
        second=module.build_event(first["event_hash"],"r1","approve","2026-10-31T10:00:00Z",{"to":"approved"})
        claim={"claim_id":"C1","card_id":"TEST-001","claim":"Synthetic approved",
               "references":[{"source_id":"S1"}],"status":"approved","reviewed_on":"2026-10-31",
               "audit_events":[first,second],"review_decisions":[{"reviewer_id":"r1","decision":"approve"}]}
        self.configure_v15(claims=[claim],minimum_reviewers=2)
        result=self.run_cli()
        self.assertEqual(result.returncode,2)
        self.assertFalse(self.output.exists())

    def test_late_failure_preserves_previous_bundle_and_source_files(self):
        self.configure_v15()
        source_path=self.fixtures/"v15_sources.json"
        claims_path=self.fixtures/"v15_claims.json"
        source_before=source_path.read_bytes(); claims_before=claims_path.read_bytes()
        first=self.run_cli()
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        manifest_before=(self.output/"v14_manifest.json").read_bytes()
        payload=json.loads(source_path.read_text()); payload["sources"][0]["status"]="withdrawn"
        write_json(source_path,payload)
        failed=self.run_cli()
        self.assertEqual(failed.returncode,2)
        self.assertEqual((self.output/"v14_manifest.json").read_bytes(),manifest_before)
        self.assertEqual(claims_path.read_bytes(),claims_before)
        self.assertEqual(source_before,json.dumps({"sources":[{"source_id":"S1","title":"Synthetic source","source_type":"guideline","authority":"synthetic-publisher","jurisdiction":"TEST","version":"1","status":"active"}]},indent=2).encode()+b"\n")

if __name__=="__main__":
    unittest.main()
