import json
import tempfile
import unittest
from pathlib import Path

import v5_release as v5

class V5ReleaseTests(unittest.TestCase):
    def test_retry_after_numeric_and_http_date(self):
        self.assertEqual(v5.retry_delay("12", 0), 12)
        import datetime as dt, email.utils
        stamp=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=2)
        self.assertGreaterEqual(v5.retry_delay(email.utils.format_datetime(stamp),0),0)

    def test_persistent_harvest_checkpoint_and_resume_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn=v5.init_harvest_state(Path(tmp)/"state.sqlite")
            v5.checkpoint(conn,"PMC1","urology","discovered")
            v5.checkpoint(conn,"PMC1","urology","failed",retryable=True,error="timeout")
            v5.checkpoint(conn,"PMC2","cardiology","failed",retryable=False,error="bad XML")
            rows=v5.resumable_candidates(conn)
            self.assertEqual([r["candidate_id"] for r in rows],["PMC1"])
            self.assertEqual(rows[0]["attempts"],1)
            conn.close()

    def test_cache_verifier_detects_hash_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/"urology"/"PMC1"; folder.mkdir(parents=True)
            xml=b"<article/>"; (folder/"article.xml").write_bytes(xml)
            (folder/"meta.json").write_text(json.dumps({"schema_version":5,"pmcid":"PMC1","content_sha256":v5.digest(xml)}))
            (folder/"article.txt").write_text("test article")
            (root/"manifest.jsonl").write_text(json.dumps({"specialty":"urology","pmcid":"PMC1","content_sha256":v5.digest(xml)})+"\n")
            self.assertTrue(v5.verify_cache(root)["ok"])
            (folder/"article.xml").write_bytes(b"<article>changed</article>")
            report=v5.verify_cache(root)
            self.assertFalse(report["ok"])
            self.assertIn("xml_hash_mismatch",[x["kind"] for x in report["issues"]])

    def test_gold_metrics_require_adjudicated_real_labels_and_report_by_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"gold.jsonl"
            rows=[{"specialty":"urology","card_type":"diagnosis","case_id":"a","reviewer_count":2,"reviewer_ids":["r1","r2"],"adjudicated":True,
                   "high_risk":True,"gold_supported":True,"predicted_supported":True,"evidence_exact":True,
                   "gold_diagnosis":"renal artery aneurysm","predicted_diagnosis":"renal artery aneurysm","predicted_specialty":"urology",
                   "assertion_gold":{"negation":False,"certainty":"certain","temporality":"current","experiencer":"patient","family_history":False},
                   "assertion_predicted":{"negation":False,"certainty":"certain","temporality":"current","experiencer":"patient","family_history":False},
                   "answer_claim_count":2,"supported_claim_count":2},
                  {"specialty":"urology","card_type":"diagnosis","case_id":"b","reviewer_count":1,"reviewer_ids":["r3"],"adjudicated":True,
                   "gold_supported":False,"predicted_supported":True,"evidence_exact":False,
                   "gold_diagnosis":"renal artery stenosis","predicted_diagnosis":"renal artery aneurysm","predicted_specialty":"cardiology",
                   "assertion_gold":{"negation":True,"certainty":"uncertain","temporality":"historical","experiencer":"family","family_history":True},
                   "assertion_predicted":{"negation":False,"certainty":"certain","temporality":"current","experiencer":"patient","family_history":False},
                   "answer_claim_count":1,"supported_claim_count":0}]
            path.write_text("".join(json.dumps(r)+"\n" for r in rows))
            report=v5.evaluate_gold_set(path,dataset_version="1.0",protocol="two-reviewer adjudication protocol")
            group=report["groups"]["urology/diagnosis"]
            self.assertEqual(group["tp"],1); self.assertEqual(group["fp"],1)
            self.assertAlmostEqual(group["unsupported_claim_rate"],1/3)
            self.assertEqual(group["assertion_attribute_accuracy"]["negation"],0.5)
            self.assertEqual(group["diagnosis_exact_match"],0.5)
            rows[0]["reviewer_count"]=1
            rows[0]["reviewer_ids"]=["r1"]
            path.write_text("".join(json.dumps(r)+"\n" for r in rows))
            with self.assertRaises(ValueError): v5.evaluate_gold_set(path,dataset_version="1.0",protocol="protocol")

    def test_reviewer_token_signature_expiry_and_roles(self):
        secret="a-secret-with-at-least-32-characters-long"
        token=v5.issue_reviewer_token(reviewer_id="reviewer-7",role="clinician",secret=secret,expires_at="2027-01-01T00:00:00Z")
        self.assertEqual(v5.verify_reviewer_token(token,secret=secret)["reviewer_id"],"reviewer-7")
        token["signature"]="0"*64
        with self.assertRaises(PermissionError): v5.verify_reviewer_token(token,secret=secret)

    def test_guideline_allowlist_is_fail_closed(self):
        record={"guideline_id":"g1","publisher":"Society","title":"Guideline","stable_url":"https://uroweb.org/guideline/x",
                "publication_date":"2025-01-01","last_updated":"2025-02-01","recommendation_text":"Recommendation",
                "strength_grade":"Strong","accessed_at":"2026-10-30","version":"1"}
        self.assertTrue(v5.validate_guideline_record(record,{"uroweb.org"})["allowlisted"])
        with self.assertRaises(PermissionError): v5.validate_guideline_record(record,{"example.org"})

if __name__=="__main__": unittest.main()
