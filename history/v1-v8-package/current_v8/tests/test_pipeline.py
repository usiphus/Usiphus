import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cases2anki as converter
import specialty_harvest as harvester

JATS = '''<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front>
    <journal-meta><journal-title-group><journal-title>Example Clinical Journal</journal-title></journal-title-group></journal-meta>
    <article-meta>
      <article-id pub-id-type="pmc">PMC1234567</article-id>
      <article-id pub-id-type="doi">10.1234/example.2026.01</article-id>
      <title-group><article-title>A rare case of renal artery aneurysm in an adult patient</article-title></title-group>
      <pub-date><year>2026</year></pub-date>
      <abstract><p>A 52-year-old patient presented with flank pain and hypertension. Imaging identified a renal artery aneurysm.</p></abstract>
      <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"><license-p>CC BY 4.0</license-p></license></permissions>
    </article-meta>
  </front>
  <body>
    <sec><title>Case presentation</title>
      <p>A 52-year-old man presented with sudden left flank pain and elevated blood pressure.</p>
      <p>Physical examination revealed left abdominal tenderness without peritonism.</p>
    </sec>
    <sec><title>Investigations</title>
      <p>Computed tomography angiography demonstrated a 2.8 cm renal artery aneurysm with mural thrombus.</p>
      <p>Laboratory testing showed a stable hemoglobin concentration.</p>
    </sec>
    <sec><title>Final diagnosis</title>
      <p>The final diagnosis was a symptomatic renal artery aneurysm.</p>
    </sec>
    <sec><title>Treatment and management</title>
      <p>The patient underwent endovascular stent-graft repair without procedural complication.</p>
    </sec>
    <sec><title>Follow-up</title>
      <p>At 6 months follow-up, the patient remained asymptomatic and imaging showed preserved renal perfusion.</p>
    </sec>
    <sec><title>Conclusion</title>
      <p>This case highlights the importance of considering a renal artery aneurysm in patients with flank pain and hypertension.</p>
    </sec>
  </body>
</article>'''


class HarvesterTests(unittest.TestCase):
    def test_query_contains_required_open_full_text_and_specialty_terms(self):
        query = harvester.build_query("urology", 2020, 2026)
        self.assertIn('PUB_TYPE:"Case Reports"', query)
        self.assertIn("OPEN_ACCESS:Y", query)
        self.assertIn("HAS_FT:Y", query)
        self.assertIn('TITLE_ABS:"bladder"', query)
        self.assertIn("PUB_YEAR:[2020 TO 2026]", query)

    def test_query_rejects_invalid_year_range(self):
        with self.assertRaises(ValueError):
            harvester.build_query("urology", 2025, 2020)

    def test_metadata_and_body_extraction(self):
        meta = harvester.parse_article_metadata(JATS.encode())
        abstract, body, headings = harvester.get_article_text(JATS.encode())
        self.assertEqual(meta["doi"], "10.1234/example.2026.01")
        self.assertEqual(meta["year"], "2026")
        self.assertIn("computed tomography angiography", body.lower())
        self.assertIn("Case presentation", headings)
        self.assertIn("flank pain", abstract)

    def test_specialty_relevance_prefers_title_match(self):
        score = harvester.specialty_relevance("urology", "Renal artery aneurysm", "", "")
        self.assertGreaterEqual(score, 3.0)

    def test_educational_score_has_reasons(self):
        score, reasons = harvester.educational_score(
            "Rare renal artery aneurysm", "Diagnostic challenge", JATS, 18
        )
        self.assertGreater(score, 30)
        self.assertTrue(reasons)

    def test_harvest_one_writes_a_converter_compatible_case_folder(self):
        candidate = harvester.Candidate(
            pmcid="PMC1234567", title="A rare case of renal artery aneurysm",
            doi="10.1234/example.2026.01", year="2026", journal="Example Clinical Journal", cited_by_count=18,
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(harvester, "http_get", return_value=JATS.encode()):
            metadata = harvester.harvest_one(
                candidate, "urology", Path(tmp), limiter=harvester.RateLimiter(1000),
                user_agent="test", min_words=0, min_relevance=1.0, refresh=False,
            )
            article = Path(tmp) / "urology" / "PMC1234567" / "article.xml"
            meta_file = article.parent / "meta.json"
            self.assertTrue(article.exists())
            self.assertTrue(meta_file.exists())
            self.assertEqual(metadata["pmcid"], "PMC1234567")
            loaded = converter.parse_jats(article)
            self.assertEqual(loaded.specialty, "urology")
            self.assertIn("renal artery aneurysm", loaded.category_text("diagnosis").lower())

    def test_search_pagination_deduplicates_pmcids(self):
        pages = [
            {"resultList": {"result": [{"pmcid": "PMC1", "title": "one"}, {"pmcid": "PMC2"}]}, "nextCursorMark": "next"},
            {"resultList": {"result": [{"pmcid": "PMC2"}, {"pmcid": "PMC3"}]}, "nextCursorMark": "next2"},
            {"resultList": {"result": []}, "nextCursorMark": "next2"},
        ]
        with patch.object(harvester, "_search_page", side_effect=pages):
            found = harvester.search_candidates(
                "urology", limit=0, page_size=100, max_pages=5, year_from=None, year_to=None,
                limiter=harvester.RateLimiter(1000), user_agent="test",
            )
        self.assertEqual([x.pmcid for x in found], ["PMC1", "PMC2", "PMC3"])


    def test_core_metadata_mesh_heading_extraction(self):
        payload = {
            "resultList": {"result": [{"meshHeadingList": {"meshHeading": [
                {"descriptorName": {"$": "Kidney Diseases"}},
                {"descriptorName": "Ureteral Obstruction"},
            ]}}]}
        }
        with patch.object(harvester, "http_get", return_value=json.dumps(payload).encode()):
            meta = harvester.fetch_core_metadata("PMC1234567", limiter=harvester.RateLimiter(1000), user_agent="test")
        self.assertEqual(meta["mesh_terms"], ["Kidney Diseases", "Ureteral Obstruction"])
        self.assertGreater(harvester.mesh_relevance("urology", meta["mesh_terms"]), 0)

    def test_cached_article_reapplies_word_count_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "urology" / "PMC1234567"
            folder.mkdir(parents=True)
            (folder / "article.xml").write_text(JATS, encoding="utf-8")
            (folder / "meta.json").write_text(json.dumps({"pmcid": "PMC1234567", "title": "Renal artery aneurysm"}), encoding="utf-8")
            candidate = harvester.Candidate(pmcid="PMC1234567", title="Renal artery aneurysm")
            result = harvester.harvest_one(candidate, "urology", Path(tmp), limiter=harvester.RateLimiter(1000),
                                            user_agent="test", min_words=10000, min_relevance=0, refresh=False)
            self.assertIsNone(result)


class ConverterTests(unittest.TestCase):
    def _case(self):
        temp = tempfile.TemporaryDirectory()
        folder = Path(temp.name) / "PMC1234567"
        folder.mkdir()
        (folder / "article.xml").write_text(JATS, encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({
            "pmcid": "PMC1234567", "specialty": "urology", "source_url": "https://europepmc.org/articles/PMC1234567",
            "educational_score": 88, "cited_by_count": 18,
        }), encoding="utf-8")
        case = converter.parse_jats(folder / "article.xml")
        self.addCleanup(temp.cleanup)
        return case

    def test_jats_parser_preserves_sections_and_metadata(self):
        case = self._case()
        self.assertEqual(case.case_id, "PMC1234567")
        self.assertEqual(case.specialty, "urology")
        self.assertIn("symptomatic renal artery aneurysm", case.category_text("diagnosis"))
        self.assertIn("stent-graft repair", case.category_text("management"))

    def test_diagnosis_card_hides_answer_on_front_and_shows_it_on_back(self):
        case = self._case()
        cards, report = converter.build_cards(case, min_confidence=0.6)
        diagnosis_cards = [c for c in cards if c["card_type"] == "diagnosis"]
        self.assertEqual(len(diagnosis_cards), 1)
        card = diagnosis_cards[0]
        self.assertNotIn("symptomatic renal artery aneurysm", card["front"].lower())
        self.assertNotIn("renal artery aneurysm", card["front"].lower())
        self.assertIn("symptomatic renal artery aneurysm", card["back"].lower())
        self.assertIn("symptomatic renal artery aneurysm", card["extra"].lower())
        self.assertGreater(report["confidence"], 0.8)

    def test_creates_management_outcome_and_teaching_cards(self):
        cards, _ = converter.build_cards(self._case())
        types = {card["card_type"] for card in cards}
        self.assertTrue({"diagnosis", "investigation", "management", "outcome", "teaching"}.issubset(types))
        management = next(card for card in cards if card["card_type"] == "management")
        self.assertIn("left flank pain", management["front"])

    def test_html_content_is_escaped(self):
        escaped = converter.esc('<script>alert("x")</script>')
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)

    def test_tsv_writer_creates_import_file(self):
        cards, _ = converter.build_cards(self._case())
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "cards.tsv"
            converter.write_tsv(output, cards)
            data = output.read_text(encoding="utf-8-sig")
            self.assertIn("Front\tBack\tExtra\tTags", data)
            self.assertIn("card::diagnosis", data)

    def test_cli_preserves_dotted_output_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            case_dir = base / "library" / "PMC1234567"
            case_dir.mkdir(parents=True)
            (case_dir / "article.xml").write_text(JATS, encoding="utf-8")
            (case_dir / "meta.json").write_text(json.dumps({"pmcid": "PMC1234567", "specialty": "urology"}), encoding="utf-8")
            output = base / "deck.v4"
            result = converter.main([str(base / "library"), "--format", "tsv", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertTrue((base / "deck.v4.tsv").exists())

    def test_diagnosis_title_fallback_is_marked_lower_confidence(self):
        case = self._case()
        case.sections = [converter.Section("Case presentation", "A patient presented with a new symptom and underwent evaluation.", "presentation")]
        case.abstract = ""
        dx, source, confidence, _ = converter.find_diagnosis(case)
        self.assertEqual(source, "title")
        self.assertLess(confidence, 0.7)
        self.assertTrue(dx)

    def test_title_inferred_diagnosis_is_not_direct_evidence_locked(self):
        case = self._case()
        case.sections = [converter.Section("Case presentation", "A patient presented with sudden left flank pain and elevated blood pressure. Physical examination revealed left abdominal tenderness without peritonism.", "presentation")]
        case.abstract = ""
        cards, _ = converter.build_cards(case, min_confidence=0.5)
        diagnosis = next(card for card in cards if card["card_type"] == "diagnosis")
        self.assertFalse(diagnosis["evidence_coverage"])

    def test_card_and_evidence_ids_are_stable(self):
        case = self._case()
        first, _ = converter.build_cards(case)
        second, _ = converter.build_cards(case)
        self.assertEqual([c["card_id"] for c in first], [c["card_id"] for c in second])
        self.assertEqual(first[0]["evidence_spans"][0]["evidence_id"], second[0]["evidence_spans"][0]["evidence_id"])


    def test_assertion_attributes_detect_negation_history_and_family(self):
        self.assertEqual(converter.assertion_attributes("There was no evidence of renal carcinoma.")["certainty"], "negated")
        self.assertEqual(converter.assertion_attributes("The patient had a history of renal carcinoma.")["temporality"], "historical")
        self.assertEqual(converter.assertion_attributes("His mother had renal carcinoma.")["experiencer"], "family")

    def test_diagnosis_evidence_and_assertion_are_reported(self):
        case = self._case()
        cards, report = converter.build_cards(case)
        self.assertIn("final diagnosis was a symptomatic renal artery aneurysm", report["diagnosis_evidence_sentence"].lower())
        self.assertEqual(report["assertion_certainty"], "positive")
        self.assertEqual(report["assertion_experiencer"], "patient")
        self.assertTrue(any("Diagnosis evidence in the article" in card["extra"] for card in cards))

    def test_negated_diagnosis_is_not_selected(self):
        case = self._case()
        case.sections = [converter.Section("Diagnosis", "There was no evidence of renal artery aneurysm.", "diagnosis")]
        case.abstract = ""
        detail = converter.find_diagnosis_detail(case)
        self.assertNotEqual(detail["source"], "diagnosis")
        self.assertNotEqual(detail["certainty"], "positive")

    def test_card_limit_keeps_quality_report_in_sync(self):
        cards = [
            {"case_id": "PMC1", "card_type": "diagnosis"},
            {"case_id": "PMC1", "card_type": "investigation"},
            {"case_id": "PMC2", "card_type": "management"},
        ]
        reports = [
            {"case_id": "PMC1", "card_count": 2, "card_types": "diagnosis;investigation", "quality_flags": ""},
            {"case_id": "PMC2", "card_count": 1, "card_types": "management", "quality_flags": ""},
        ]
        limited = converter.apply_card_limit(cards, reports, 1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(reports[0]["card_count"], 1)
        self.assertEqual(reports[0]["card_types"], "diagnosis")
        self.assertIn("export-limit-truncated", reports[0]["quality_flags"])
        self.assertEqual(reports[1]["card_count"], 0)
        self.assertEqual(reports[1]["card_types"], "")

    def test_differential_card_can_be_disabled(self):
        case = self._case()
        case.sections.append(converter.Section("Discussion", "The differential diagnosis included renal cell carcinoma and renal infarction, which can mimic this presentation.", "discussion"))
        cards, _ = converter.build_cards(case, include_differential=True)
        self.assertIn("differential", {card["card_type"] for card in cards})
        cards_without, _ = converter.build_cards(case, include_differential=False)
        self.assertNotIn("differential", {card["card_type"] for card in cards_without})

    def test_cards_include_exact_section_local_evidence_spans(self):
        case = self._case()
        cards, report = converter.build_cards(case)
        self.assertTrue(cards)
        self.assertEqual(report["review_status"], "needs-human-review")
        for card in cards:
            self.assertTrue(card["evidence_spans"], card["card_type"])
            for span in card["evidence_spans"]:
                section = next(s for s in case.sections if s.title == span["section"])
                self.assertEqual(section.text[span["start_char"]:span["end_char"]], span["text"])

    def test_author_reasoning_card_requires_explicit_discussion_sentence(self):
        case = self._case()
        sentence = "The combination of flank pain and hypertension therefore suggests a vascular cause and should prompt targeted imaging."
        case.sections.append(converter.Section("Discussion", sentence, "discussion"))
        cards, _ = converter.build_cards(case)
        reasoning = [c for c in cards if c["card_type"] == "reasoning"]
        self.assertEqual(len(reasoning), 1)
        self.assertEqual(reasoning[0]["evidence_spans"][0]["text"], sentence)
        cards_without, _ = converter.build_cards(case, include_reasoning=False)
        self.assertNotIn("reasoning", {c["card_type"] for c in cards_without})

    def test_strict_evidence_cli_writes_review_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            case_dir = base / "library" / "PMC1234567"
            case_dir.mkdir(parents=True)
            (case_dir / "article.xml").write_text(JATS, encoding="utf-8")
            (case_dir / "meta.json").write_text(json.dumps({"pmcid": "PMC1234567", "specialty": "urology", "source_url": "https://example.org/article"}), encoding="utf-8")
            output = base / "v7"
            result = converter.main([str(base / "library"), "--format", "tsv", "--output", str(output), "--strict-evidence", "--review-jsonl", "--quality-report"])
            self.assertEqual(result, 0)
            rows = [json.loads(line) for line in (base / "v7_review.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(rows)
            self.assertTrue(all(row["evidence_spans"] and row["source_url"] for row in rows))
            quality = (base / "v7_quality.csv").read_text(encoding="utf-8-sig")
            self.assertIn("review_status", quality)

    def test_human_review_decisions_can_gate_approved_only_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            case_dir = base / "library" / "PMC1234567"
            case_dir.mkdir(parents=True)
            (case_dir / "article.xml").write_text(JATS, encoding="utf-8")
            (case_dir / "meta.json").write_text(json.dumps({"pmcid": "PMC1234567", "specialty": "urology", "source_url": "https://example.org/article"}), encoding="utf-8")
            case = converter.parse_jats(case_dir / "article.xml")
            cards, _ = converter.build_cards(case)
            decision_path = base / "decisions.jsonl"
            decision_path.write_text(json.dumps({"card_id": cards[0]["card_id"], "status": "approved", "reviewer": "reviewer-1", "reviewed_at": "2026-10-30T12:00:00Z", "notes": "Evidence checked"}) + "\n", encoding="utf-8")
            output = base / "approved"
            result = converter.main([str(base / "library"), "--format", "tsv", "--output", str(output), "--review-decisions", str(decision_path), "--approved-only", "--review-jsonl"])
            self.assertEqual(result, 0)
            rows = [json.loads(line) for line in (base / "approved_review.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["review_status"], "approved")
            self.assertEqual(rows[0]["reviewer"], "reviewer-1")

    def test_review_decision_rejects_stale_card_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            path.write_text(json.dumps({"card_id": "stale", "status": "approved", "reviewer": "reviewer"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                converter.load_review_decisions(path, [{"card_id": "current"}])

    def test_v8_jats_media_inventory_is_metadata_only_and_blocks_unknown_license(self):
        xml = converter.ET.fromstring("""<article><body><fig id="f1"><label>Figure 1</label><caption><p>CT image showing a renal mass.</p></caption><graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="fig1.jpg"/></fig><table-wrap id="t1"><label>Table 1</label><caption><p>Laboratory results</p></caption></table-wrap></body></article>""")
        records = converter.extract_jats_media(xml, "PMC123", "https://example.org/article", "", "abc123")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["asset_type"], "figure")
        self.assertEqual(records[0]["graphic_refs"], ["fig1.jpg"])
        self.assertEqual(records[0]["reuse_decision"], "blocked_unknown_license")
        self.assertFalse(records[0]["downloaded"])
        self.assertFalse(records[0]["embedded"])
        self.assertEqual(records[1]["asset_type"], "table")
        self.assertEqual(records[0]["asset_id"], converter.extract_jats_media(xml, "PMC123", "https://example.org/article", "", "abc123")[0]["asset_id"])

    def test_v8_version_markers(self):
        self.assertEqual(converter.VERSION, "8.0.0")
        self.assertEqual(harvester.VERSION, "8.0.0")

if __name__ == "__main__":
    unittest.main(verbosity=2)
