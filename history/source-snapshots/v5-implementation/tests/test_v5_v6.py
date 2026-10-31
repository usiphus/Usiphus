import hashlib
import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import case_database as db
import source_integrity as integrity
import cases2anki as converter
import specialty_harvest as harvester
import email.utils

JATS = '''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
<article-id pub-id-type="pmc">PMC1234567</article-id><article-id pub-id-type="doi">10.1234/demo</article-id>
<title-group><article-title>Nested renal case</article-title></title-group><abstract><p>Renal case abstract.</p></abstract>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"/></permissions>
</article-meta></front><body><sec><title>Case presentation</title><p>Patient presented with flank pain.</p><p>Abdominal examination showed tenderness.</p>
<sec><title>Examination</title><p>Palpation confirmed localized tenderness.</p></sec></sec>
<sec><title>Diagnosis</title><p>The final diagnosis was renal artery aneurysm.</p></sec>
<fig id="f1"><label>Figure 1</label><caption><p>CT angiography image</p></caption><graphic xlink:href="fig1.jpg"/></fig>
</body></article>'''


class SourceIntegrityTests(unittest.TestCase):
    def test_identifier_normalization_and_validation(self):
        ids = integrity.identifier_inputs(pmcid="pmc123", pmid="12345", doi="https://doi.org/10.1234/ABC")
        self.assertEqual(ids, [
            {"src": "PMC", "extId": "PMC123"},
            {"src": "MED", "extId": "12345"},
            {"src": "DOI", "extId": "10.1234/abc"},
        ])
        self.assertEqual(integrity.identifier_inputs(pmcid="bad", pmid="", doi="not-a-doi"), [])

    def test_retraction_expression_and_correction_classification(self):
        ids = [{"src": "PMC", "extId": "PMC123"}]
        for label, expected in (("Retraction", "retracted"), ("Expression of Concern", "expression_of_concern"),
                                ("Correction", "corrected")):
            payload = {"updates": [{"id": "PMC123", "update": {"updateType": label}}]}
            self.assertEqual(integrity.classify_status_response(payload, ids)["status"], expected)

    def test_generic_numeric_record_id_does_not_match_a_pmid(self):
        payload = {"records": [{"id": 12345, "updateType": "Retraction"}]}
        result = integrity.classify_status_response(payload, [{"src": "MED", "extId": "12345"}])
        self.assertEqual(result["status"], "unknown")

    def test_malformed_status_json_is_rejected(self):
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with self.assertRaises(integrity.StatusCheckError):
            integrity.post_status_request([{"src": "PMC", "extId": "PMC123"}],
                                          opener=lambda *a, **k: Response(b"not-json"))

    def test_no_matching_update_is_unknown_not_current(self):
        result = integrity.classify_status_response({"updates": []}, [{"src": "PMC", "extId": "PMC123"}])
        self.assertEqual(result["status"], "unknown")

    def test_conflicting_status_updates_are_conflict(self):
        payload = {"updates": [{"id": "PMC123", "updateType": "Retraction"},
                               {"id": "PMC123", "updateType": "Correction"}]}
        self.assertEqual(integrity.classify_status_response(payload, [{"src": "PMC", "extId": "PMC123"}])["status"], "conflict")

    def test_harvester_http_date_retry_after_and_response_limit(self):
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        future = (integrity.dt.datetime.now(integrity.dt.timezone.utc) + integrity.dt.timedelta(seconds=3))
        retry_date = email.utils.format_datetime(future)
        headers = {"Retry-After": retry_date}
        error = integrity.urllib.error.HTTPError("https://example.test", 429, "busy", headers, None)
        with patch.object(harvester.urllib.request, "urlopen", side_effect=[error, Response(b"ok")]), \
             patch.object(harvester.time, "sleep") as sleep:
            self.assertEqual(harvester.http_get("https://example.test", retries=2), b"ok")
            self.assertEqual(sleep.call_count, 1)
            self.assertGreaterEqual(sleep.call_args.args[0], 0)
            self.assertLessEqual(sleep.call_args.args[0], 60)
        with patch.object(harvester.urllib.request, "urlopen", return_value=Response(b"oversized")):
            with self.assertRaises(harvester.HarvestError):
                harvester.http_get("https://example.test", max_bytes=3)

    def test_crossref_update_relation_and_provider_conflict(self):
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        payload = {"message": {"update-to": [{"type": "retraction", "DOI": "10.1234/notice"}]}}
        result = integrity.fetch_crossref_updates("10.1234/demo", opener=lambda *a, **k: Response(json.dumps(payload).encode()))
        self.assertEqual(result["status"], "retracted")
        combined = integrity.combine_provider_statuses({"status": "corrected"}, result)
        self.assertEqual(combined["status"], "conflict")

    def test_post_rejects_oversized_response(self):
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with self.assertRaises(integrity.StatusCheckError):
            integrity.post_status_request([{"src": "PMC", "extId": "PMC1"}], max_bytes=2,
                                          opener=lambda *a, **k: Response(b'{"x":1}'))

    def test_cache_uses_recent_result_and_force_refresh_bypasses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            calls = []
            def request(ids):
                calls.append(ids)
                return {"updates": [{"id": "PMC123", "updateType": "Correction"}]}
            first = integrity.check_article_status(pmcid="PMC123", cache_path=path, request_fn=request)
            second = integrity.check_article_status(pmcid="PMC123", cache_path=path, request_fn=request)
            third = integrity.check_article_status(pmcid="PMC123", cache_path=path, request_fn=request, force=True)
            self.assertEqual(first["status"], "corrected")
            self.assertEqual(second, first)
            self.assertEqual(third["status"], "corrected")
            self.assertEqual(len(calls), 2)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "urology" / "PMC1234567"
        self.folder.mkdir(parents=True)
        self.xml_path = self.folder / "article.xml"
        self.xml_path.write_text(JATS, encoding="utf-8")
        digest = hashlib.sha256(JATS.encode()).hexdigest()
        meta = {"schema_version": 5, "pmcid": "PMC1234567", "doi": "10.1234/demo", "title": "Nested renal case",
                "year": "2026", "specialty": "urology", "source_url": "https://europepmc.org/articles/PMC1234567",
                "content_sha256": digest, "article_status": {"status": "unknown", "checked_at": None},
                "license": "CC BY 4.0"}
        (self.folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.conn = db.connect(self.root / "cases.sqlite3")
        db.initialize(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_database_migrates_legacy_schema_v1_to_v3(self):
        legacy = db.connect(self.root / "legacy.sqlite3")
        legacy.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL)")
        legacy.execute("INSERT INTO schema_migrations VALUES(1,'2026-01-01T00:00:00+00:00','legacy')")
        legacy.execute("CREATE TABLE card_claims(claim_id TEXT PRIMARY KEY,card_id TEXT NOT NULL,claim_text TEXT NOT NULL,claim_type TEXT NOT NULL DEFAULT 'case_reported',evidence_ids_json TEXT NOT NULL DEFAULT '[]',evidence_covered INTEGER NOT NULL DEFAULT 0)")
        legacy.commit()
        db.initialize(legacy)
        versions=[r[0] for r in legacy.execute("SELECT version FROM schema_migrations ORDER BY version")]
        columns={r[1] for r in legacy.execute("PRAGMA table_info(card_claims)")}
        self.assertEqual(versions,[1,2,3,4])
        self.assertIn("high_risk",columns)
        card_columns={r[1] for r in legacy.execute("PRAGMA table_info(cards)")}
        self.assertIn("extra",card_columns)
        self.assertIn("tags_json",card_columns)
        self.assertIsNotNone(legacy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claim_assessments'").fetchone())
        legacy.close()

    def test_import_is_idempotent_and_nested_sections_are_preserved(self):
        first = db.import_case_folder(self.conn, self.folder)
        second = db.import_case_folder(self.conn, self.folder)
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "unchanged")
        titles = [r[0] for r in self.conn.execute("SELECT title FROM sections ORDER BY ordinal")]
        self.assertIn("Examination", titles)
        self.assertEqual(len(titles), 4)

    def test_same_pmcid_identifier_change_is_flagged_and_not_overwritten(self):
        db.import_case_folder(self.conn, self.folder)
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["doi"] = "10.9999/conflicting-doi"
        meta_path.write_text(json.dumps(meta))
        result = db.import_case_folder(self.conn, self.folder)
        row = self.conn.execute("SELECT doi,article_status,status_json FROM articles WHERE pmcid='PMC1234567'").fetchone()
        self.assertEqual(row["doi"], "10.1234/demo")
        self.assertEqual(row["article_status"], "conflict")
        self.assertIn("identity_conflicts", row["status_json"])
        self.assertEqual(result["status"], "updated")

    def test_full_text_search_and_manifest(self):
        db.import_case_folder(self.conn, self.folder)
        rows = db.search_cases(self.conn, query='"flank pain"', specialty="urology")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pmcid"], "PMC1234567")
        out = self.root / "manifest.jsonl"
        self.assertEqual(db.export_manifest(self.conn, out), 1)
        self.assertIn("PMC1234567", out.read_text())

    def test_evidence_offsets_match_converter_for_multi_paragraph_section(self):
        db.import_case_folder(self.conn, self.folder)
        case = converter.parse_jats(self.xml_path)
        quote = "Abdominal examination showed tenderness."
        spans = converter.evidence_spans(case, [quote])
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["section_index"], 0)
        queue = self.root / "multi-paragraph.jsonl"
        queue.write_text(json.dumps({"card_id": "card-multipara", "case_id": "PMC1234567", "card_type": "teaching",
                                     "front": "Q", "back": "A", "evidence_spans": spans}) + "\n")
        result = db.import_review_queue(self.conn, queue)
        self.assertEqual(result["invalid_evidence_spans"], 0)
        stored = self.conn.execute("SELECT quote FROM evidence_spans WHERE evidence_id=?", (spans[0]["evidence_id"],)).fetchone()
        self.assertEqual(stored["quote"], quote)

    def test_unknown_future_metadata_schema_is_rejected(self):
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["schema_version"] = 999
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "Unsupported metadata schema_version"):
            db.import_case_folder(self.conn, self.folder)

    def test_source_hash_mismatch_fails_closed(self):
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["content_sha256"] = "0" * 64
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            db.import_case_folder(self.conn, self.folder)

    def test_duplicate_doi_is_flagged_not_merged(self):
        db.import_case_folder(self.conn, self.folder)
        folder2 = self.root / "cardiology" / "PMC7654321"
        folder2.mkdir(parents=True)
        xml2 = JATS.replace("PMC1234567", "PMC7654321").replace("Nested renal case", "Second distinct article")
        (folder2 / "article.xml").write_text(xml2)
        meta2 = json.loads((self.folder / "meta.json").read_text())
        meta2.update({"pmcid": "PMC7654321", "title": "Second distinct article", "content_sha256": hashlib.sha256(xml2.encode()).hexdigest()})
        (folder2 / "meta.json").write_text(json.dumps(meta2))
        db.import_case_folder(self.conn, folder2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 2)
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM identity_conflicts").fetchone()[0], 1)

    def test_review_queue_validates_offsets_and_links_claims(self):
        db.import_case_folder(self.conn, self.folder)
        section = self.conn.execute("SELECT * FROM sections WHERE title='Diagnosis'").fetchone()
        quote = "The final diagnosis was renal artery aneurysm."
        start = section["text"].index(quote)
        queue = self.root / "queue.jsonl"
        row = {"card_id": "card-1", "case_id": "PMC1234567", "card_type": "diagnosis", "front": "Q", "back": "A",
               "evidence_spans": [{"evidence_id": "ev-1", "section": "Diagnosis", "start_char": start,
                                   "end_char": start + len(quote), "text": quote}],
               "claims": [{"text": "The reported final diagnosis was renal artery aneurysm.",
                           "claim_type": "case_reported", "evidence_ids": ["ev-1"]}]}
        queue.write_text(json.dumps(row) + "\n")
        result = db.import_review_queue(self.conn, queue)
        self.assertEqual(result["cards_imported"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_claims WHERE evidence_covered=1").fetchone()[0], 0)
        row["evidence_spans"][0]["text"] = "forged evidence"
        queue.write_text(json.dumps(row) + "\n")
        result = db.import_review_queue(self.conn, queue)
        self.assertEqual(result["invalid_evidence_spans"], 1)


    def test_claim_coverage_requires_authenticated_clinician_assessment(self):
        db.import_case_folder(self.conn, self.folder)
        section = self.conn.execute("SELECT * FROM sections WHERE title='Diagnosis'").fetchone()
        quote = "The final diagnosis was renal artery aneurysm."
        start = section["text"].index(quote)
        queue = self.root / "claims.jsonl"
        queue.write_text(json.dumps({"card_id":"claim-card","case_id":"PMC1234567","front":"Q","back":"Reported renal artery aneurysm.",
            "evidence_spans":[{"evidence_id":"claim-span","section":"Diagnosis","start_char":start,"end_char":start+len(quote),"text":quote}],
            "claims":[{"text":"The reported diagnosis was renal artery aneurysm.","evidence_ids":["claim-span"],"high_risk":True}]})+"\n")
        db.import_review_queue(self.conn, queue)
        claim = self.conn.execute("SELECT * FROM card_claims WHERE card_id='claim-card'").fetchone()
        self.assertEqual(claim["evidence_covered"], 0)
        from v5_release import issue_reviewer_token
        secret = "claim-review-secret-with-at-least-32-characters"
        token = issue_reviewer_token(reviewer_id="clinician-claim", role="clinician", secret=secret,
                                     expires_at="2027-10-30T12:00:00Z")
        assessment = {"claim_id":claim["claim_id"],"supported":True,"reviewer_token":token,
            "reviewed_at":"2026-10-30T12:00:00Z","claim_hash":db.sha256(claim["claim_text"]),
            "evidence_hash":db.claim_evidence_hash(self.conn,claim["claim_id"])}
        assessment_path=self.root/"assessments.jsonl"; assessment_path.write_text(json.dumps(assessment)+"\n")
        result=db.import_claim_assessments(self.conn,assessment_path,secret)
        self.assertEqual(result["assessments_imported"],1)
        self.assertEqual(self.conn.execute("SELECT evidence_covered FROM card_claims WHERE claim_id=?",(claim["claim_id"],)).fetchone()[0],0)
        token2 = issue_reviewer_token(reviewer_id="clinician-claim-2", role="clinical_adjudicator", secret=secret,
                                      expires_at="2027-10-30T12:00:00Z")
        assessment["reviewer_token"] = token2
        assessment_path.write_text(json.dumps(assessment)+"\n")
        result=db.import_claim_assessments(self.conn,assessment_path,secret)
        self.assertEqual(result["assessments_imported"],1)
        self.assertEqual(self.conn.execute("SELECT evidence_covered FROM card_claims WHERE claim_id=?",(claim["claim_id"],)).fetchone()[0],1)

    def test_approved_export_requires_exact_card_spans_claim_support_and_signed_decision(self):
        db.import_case_folder(self.conn, self.folder)
        section = self.conn.execute("SELECT * FROM sections WHERE title='Diagnosis'").fetchone()
        quote = "The final diagnosis was renal artery aneurysm."
        start = section["text"].index(quote)
        span = {"evidence_id":"approval-span","section":"Diagnosis","section_index":section["ordinal"],
                "start_char":start,"end_char":start+len(quote),"text":quote}
        queue = self.root / "approval-queue.jsonl"
        row = {"card_id":"approval-gated","case_id":"PMC1234567","card_type":"diagnosis","front":"What was diagnosed?",
               "back":"The reported diagnosis was renal artery aneurysm.","evidence_spans":[span],
               "claims":[{"text":"The reported diagnosis was renal artery aneurysm.","evidence_ids":["approval-span"]}]}
        queue.write_text(json.dumps(row)+"\n")
        db.import_review_queue(self.conn,queue)
        article = self.conn.execute("SELECT source_xml_sha256 FROM articles WHERE pmcid='PMC1234567'").fetchone()
        card = {**row,"source_xml_sha256":article["source_xml_sha256"]}
        self.assertEqual(db.card_approval_status(self.conn,card),"needs_review")
        from v5_release import issue_reviewer_token
        secret="approved-export-test-secret-with-32-chars"
        claim=self.conn.execute("SELECT * FROM card_claims WHERE card_id='approval-gated'").fetchone()
        token=issue_reviewer_token(reviewer_id="clinician-approve",role="clinician",secret=secret,expires_at="2027-10-30T12:00:00Z")
        assessment={"claim_id":claim["claim_id"],"supported":True,"reviewer_token":token,"reviewed_at":"2026-10-30T12:00:00Z",
                    "claim_hash":db.sha256(claim["claim_text"]),"evidence_hash":db.claim_evidence_hash(self.conn,claim["claim_id"])}
        assessment_file=self.root/"claim-assessment.jsonl"; assessment_file.write_text(json.dumps(assessment)+"\n")
        db.import_claim_assessments(self.conn,assessment_file,secret)
        db_card=self.conn.execute("SELECT c.*,a.source_xml_sha256 FROM cards c JOIN articles a ON a.article_pk=c.article_pk WHERE c.card_id='approval-gated'").fetchone()
        decision={"card_id":"approval-gated","decision":"approved","reviewer_token":token,
                 "reviewed_at":"2026-10-30T12:00:00Z","content_hash":db_card["content_hash"],
                 "evidence_hash":db.sha256(db_card["evidence_json"]+"\0"+db_card["source_xml_sha256"])}
        decision_file=self.root/"card-decision.jsonl"; decision_file.write_text(json.dumps(decision)+"\n")
        db.import_authenticated_review_decisions(self.conn,decision_file,secret)
        self.assertEqual(db.card_approval_status(self.conn,card),"approved")
        card["back"]="Changed answer"
        self.assertEqual(db.card_approval_status(self.conn,card),"needs_review")

    def test_signed_claim_and_card_audit_gates_approved_only_export_end_to_end(self):
        import cases2anki as converter
        from v5_release import issue_reviewer_token
        db_path = self.root / "cases.sqlite3"
        db.import_case_folder(self.conn, self.folder)
        draft = self.root / "draft"
        self.assertEqual(converter.main([str(self.root), "--format", "tsv", "--output", str(draft), "--review-jsonl"]), 0)
        review_queue = self.root / "draft_review.jsonl"
        db.import_review_queue(self.conn, review_queue)
        self.conn.commit()
        audit_queue = self.root / "audit_review.jsonl"
        db.export_review_queue(self.conn, audit_queue)
        secret = "end-to-end-review-secret-at-least-32-characters"
        clinician = issue_reviewer_token(reviewer_id="reviewer-a", role="clinician", secret=secret,
                                         expires_at="2027-10-30T00:00:00Z")
        adjudicator = issue_reviewer_token(reviewer_id="reviewer-b", role="clinical_adjudicator", secret=secret,
                                           expires_at="2027-10-30T00:00:00Z")
        assessments, decisions = [], []
        for line in audit_queue.read_text(encoding="utf-8").splitlines():
            card = json.loads(line)
            for claim in card["claims"]:
                tokens = [clinician, adjudicator] if claim["high_risk"] else [clinician]
                for token in tokens:
                    assessments.append({"claim_id":claim["claim_id"],"supported":True,"reviewer_token":token,
                        "reviewed_at":"2026-10-30T12:00:00Z","claim_hash":claim["claim_hash"],"evidence_hash":claim["evidence_hash"]})
            decisions.append({"card_id":card["card_id"],"decision":"approved","reviewer_token":clinician,
                "reviewed_at":"2026-10-30T12:00:00Z","content_hash":card["content_hash"],"evidence_hash":card["evidence_hash"]})
        assessments_path = self.root / "claim_assessments.jsonl"
        decisions_path = self.root / "signed_decisions.jsonl"
        assessments_path.write_text("".join(json.dumps(row)+"\n" for row in assessments))
        decisions_path.write_text("".join(json.dumps(row)+"\n" for row in decisions))
        db.import_claim_assessments(self.conn, assessments_path, secret)
        db.import_authenticated_review_decisions(self.conn, decisions_path, secret)
        self.conn.commit()
        approved = self.root / "approved"
        result = converter.main([str(self.root), "--format", "tsv", "--output", str(approved),
                                 "--review-audit-db", str(db_path), "--approved-only", "--strict-evidence"])
        self.assertEqual(result, 0)
        self.assertGreater(len((self.root / "approved.tsv").read_text(encoding="utf-8-sig").splitlines()), 1)

    def test_hash_bound_review_decision_and_append_only_history(self):
        db.import_case_folder(self.conn, self.folder)
        section = self.conn.execute("SELECT * FROM sections WHERE title='Diagnosis'").fetchone()
        quote = "The final diagnosis was renal artery aneurysm."
        start = section["text"].index(quote)
        queue = self.root / "queue.jsonl"
        queue.write_text(json.dumps({"card_id": "card-approval", "case_id": "PMC1234567", "card_type": "diagnosis",
                                     "front": "Q", "back": "A", "evidence_spans": [{"evidence_id": "ev-approval",
                                     "section": "Diagnosis", "start_char": start, "end_char": start + len(quote), "text": quote}],
                                     "claims": [{"text": "Reported diagnosis: renal artery aneurysm.", "evidence_ids": ["ev-approval"]}]}) + "\n")
        db.import_review_queue(self.conn, queue)
        card = self.conn.execute("SELECT c.*,a.source_xml_sha256 FROM cards c JOIN articles a ON a.article_pk=c.article_pk WHERE card_id='card-approval'").fetchone()
        evidence_hash = db.sha256(card["evidence_json"] + "\0" + card["source_xml_sha256"])
        decision_path = self.root / "decisions.jsonl"
        from v5_release import issue_reviewer_token
        secret = "test-secret-that-is-at-least-thirty-two-characters-long"
        token = issue_reviewer_token(reviewer_id="clinician-1", role="clinician", secret=secret,
                                     expires_at="2027-10-30T12:00:00Z")
        decision = {"card_id": "card-approval", "reviewer_token": token,
                    "decision": "approved", "reviewed_at": "2026-10-30T12:00:00Z",
                    "content_hash": card["content_hash"], "evidence_hash": evidence_hash}
        decision_path.write_text(json.dumps(decision) + "\n")
        self.assertEqual(db.import_authenticated_review_decisions(self.conn, decision_path, secret)["authenticated_decisions_imported"], 1)
        self.assertEqual(db.effective_review_status(self.conn, "card-approval"), "approved")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM review_decisions")
        # A source XML revision changes the evidence binding and invalidates the old approval.
        updated_xml = JATS.replace("Patient presented with flank pain.", "Patient presented with acute flank pain.")
        self.xml_path.write_text(updated_xml, encoding="utf-8")
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["content_sha256"] = hashlib.sha256(updated_xml.encode()).hexdigest()
        meta_path.write_text(json.dumps(meta))
        db.import_case_folder(self.conn, self.folder)
        self.assertEqual(db.effective_review_status(self.conn, "card-approval"), "needs_review")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_claims WHERE card_id='card-approval'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT evidence_json FROM cards WHERE card_id='card-approval'").fetchone()[0], "[]")

    def test_retracted_articles_are_excluded_by_default_and_override_is_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library" / "PMC1234567"
            root.mkdir(parents=True)
            (root / "article.xml").write_text(JATS, encoding="utf-8")
            (root / "meta.json").write_text(json.dumps({"pmcid": "PMC1234567", "specialty": "urology",
                "article_status": {"status": "retracted", "checked_at": "2026-10-30T00:00:00Z"}}))
            output = Path(tmp) / "deck"
            self.assertEqual(converter.main([str(root.parent), "--format", "tsv", "--output", str(output)]), 0)
            self.assertEqual(len((Path(tmp) / "deck.tsv").read_text(encoding="utf-8-sig").splitlines()), 1)
            output2 = Path(tmp) / "override"
            self.assertEqual(converter.main([str(root.parent), "--format", "tsv", "--output", str(output2), "--allow-retracted"]), 0)
            text = (Path(tmp) / "override.tsv").read_text(encoding="utf-8-sig")
            self.assertIn("RETRACTED ARTICLE", text)

    def test_guideline_import_is_separate_and_allowlist_gated(self):
        records = self.root / "guidelines.jsonl"
        row = {"guideline_id":"g-urology-1","publisher":"Urology Society","title":"Urology guideline",
               "stable_url":"https://uroweb.org/guidelines/example","publication_date":"2025-01-01",
               "last_updated":"2025-06-01","recommendation_text":"A dated recommendation.",
               "strength_grade":"Strong","accessed_at":"2026-10-30","version":"2025.1"}
        records.write_text(json.dumps(row)+"\n")
        allowlist = self.root / "allowlist.json"
        allowlist.write_text(json.dumps({"schema_version":1,"hosts":["uroweb.org"],"governance_approved":True,
                                         "approved_by":"clinical-governance-board","reviewed_at":"2026-10-30"}))
        result = db.import_guideline_records(self.conn, records, allowlist)
        self.assertEqual(result["guidelines_imported"],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT review_status FROM guideline_sources WHERE guideline_id='g-urology-1'").fetchone()[0],"needs_review")
        allowlist.write_text(json.dumps({"schema_version":1,"hosts":["example.org"],"governance_approved":True,
                                         "approved_by":"clinical-governance-board","reviewed_at":"2026-10-30"}))
        result = db.import_guideline_records(self.conn, records, allowlist)
        self.assertEqual(result["rejected_records"],1)

    def test_database_integrity_and_foreign_keys(self):
        db.import_case_folder(self.conn, self.folder)
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
