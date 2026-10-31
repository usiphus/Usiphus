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

    def test_search_pagination_deduplicates_by_doi(self):
        pages = [
            {"resultList": {"result": [
                {"pmcid": "PMC10", "doi": "10.1000/same"},
                {"pmcid": "PMC11", "doi": "10.1000/same"},
                {"pmcid": "PMC12", "doi": "10.1000/other"},
            ]}, "nextCursorMark": "next"},
            {"resultList": {"result": []}, "nextCursorMark": "next"},
        ]
        with patch.object(harvester, "_search_page", side_effect=pages):
            found = harvester.search_candidates(
                "urology", limit=0, page_size=100, max_pages=5, year_from=None, year_to=None,
                limiter=harvester.RateLimiter(1000), user_agent="test",
            )
        self.assertEqual([x.pmcid for x in found], ["PMC10", "PMC12"])

    def test_cached_article_is_rechecked_against_current_thresholds(self):
        candidate = harvester.Candidate(pmcid="PMC1234567", title="A rare case of renal artery aneurysm")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "urology" / "PMC1234567"
            folder.mkdir(parents=True)
            (folder / "article.xml").write_text(JATS, encoding="utf-8")
            (folder / "meta.json").write_text(json.dumps({"pmcid": "PMC1234567"}), encoding="utf-8")
            with patch.object(harvester, "http_get", side_effect=AssertionError("cache should be used")):
                result = harvester.harvest_one(
                    candidate, "urology", Path(tmp), limiter=harvester.RateLimiter(1000),
                    user_agent="test", min_words=100000, min_relevance=1.0, refresh=False,
                )
            self.assertIsNone(result)

    def test_score_breakdown_is_exposed(self):
        parts = harvester.educational_score_breakdown("Rare case", "", "CT scan and surgery; follow-up", 10)
        self.assertGreater(parts["investigations"], 0)
        self.assertGreater(parts["management"], 0)
        self.assertGreater(parts["citation_signal"], 0)


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

    def test_diagnosis_extractor_ignores_differential_and_uncertain_candidates(self):
        case = self._case()
        case.sections = [converter.Section(
            "Discussion",
            "The differential diagnosis included renal artery aneurysm, but this was ruled out. "
            "A possible diagnosis of renal artery aneurysm was considered but not confirmed.",
            "discussion",
        )]
        case.abstract = ""
        dx, source, confidence, _ = converter.find_diagnosis(case)
        self.assertEqual(source, "title")
        self.assertLess(confidence, 0.60)

    def test_explicit_diagnosis_later_ruled_out_is_not_accepted(self):
        case = self._case()
        case.sections = [converter.Section(
            "Final diagnosis",
            "The final diagnosis was a symptomatic renal artery aneurysm, which was later ruled out.",
            "diagnosis",
        )]
        case.abstract = ""
        dx, source, confidence, _ = converter.find_diagnosis(case)
        self.assertEqual(source, "title")
        self.assertLess(confidence, 0.60)

    def test_diagnosis_evidence_is_added_to_quality_report(self):
        cards, report = converter.build_cards(self._case())
        self.assertIn("final diagnosis", report["diagnosis_evidence"].lower())

    def test_differential_card_is_generated_and_can_be_disabled(self):
        case = self._case()
        case.sections.append(converter.Section(
            "Discussion", "The differential diagnosis included renal cell carcinoma and other vascular lesions.", "discussion"
        ))
        cards, _ = converter.build_cards(case)
        self.assertIn("differential", {c["card_type"] for c in cards})
        cards_without, _ = converter.build_cards(case, include_differential=False)
        self.assertNotIn("differential", {c["card_type"] for c in cards_without})

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

    def test_diagnosis_title_fallback_is_marked_lower_confidence(self):
        case = self._case()
        case.sections = [converter.Section("Case presentation", "A patient presented with a new symptom and underwent evaluation.", "presentation")]
        case.abstract = ""
        dx, source, confidence, _ = converter.find_diagnosis(case)
        self.assertEqual(source, "title")
        self.assertLess(confidence, 0.7)
        self.assertTrue(dx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
