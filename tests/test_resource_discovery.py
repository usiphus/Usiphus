import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import usiphus_resource_discovery as discovery


class FakeResponse:
    def __init__(self, value):
        self.raw = json.dumps(value).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        return self.raw[:size]


def fixture():
    return {
        "specialty": "ophthalmology",
        "curriculum_version": "example",
        "topics": [{
            "id": "retina-01", "domain_id": "retina", "name": "Diabetic retinopathy",
            "learning_objectives": ["Classify diabetic retinopathy and maculopathy"],
            "exam_emphasis": ["screening and management"],
            "coverage_tags": ["retina", "diabetes"],
            "source_types_to_discover": ["pubmed", "pubmed_central", "cureus_open_access",
                                         "crossref", "anki_shared_decks", "doctorde_challenges",
                                         "public_exam_prep", "professional_guidelines",
                                         "open_textbooks_resources", "doaj"],
        }]
    }


class DiscoveryTests(unittest.TestCase):
    def test_search_links_are_encoded_and_not_fetched(self):
        links = discovery._search_links("anki_shared_decks", "diabetic retinopathy")
        self.assertIn("diabetic+retinopathy", links[0]["canonical_url"])
        self.assertEqual(links[0]["access_status"], "search_link_only")

    def test_relevance_matches_topic_terms(self):
        topic = fixture()["topics"][0]
        resource = discovery._record("pubmed", "Diabetic retinopathy screening",
                                     "https://example.org/article", abstract="retina diabetes maculopathy")
        matched = discovery.match_resource(resource, topic)
        self.assertGreater(matched["relevance_score"], 0)
        self.assertEqual(matched["matched_topic_ids"], ["retina-01"])

    def test_pubmed_search_maps_metadata(self):
        responses = [
            {"esearchresult": {"idlist": ["123"]}},
            {"result": {"123": {"title": "Diabetic retinopathy", "fulljournalname": "Test Journal",
                                "pubdate": "2025", "authors": [{"name": "A Author"}],
                                "articleids": [{"idtype": "doi", "value": "10.1234/test"}],
                                "pubtype": [{"name": "Review"}]}}},
        ]
        def opener(request, timeout=20):
            return FakeResponse(responses.pop(0))
        results = discovery.search_pubmed("diabetic retinopathy", 5, opener=opener)
        self.assertEqual(results[0]["pmid"], "123")
        self.assertEqual(results[0]["doi"], "10.1234/test")
        self.assertEqual(results[0]["canonical_url"], "https://pubmed.ncbi.nlm.nih.gov/123")  # canonical form has no trailing slash

    def test_europe_pmc_records_oa_and_cureus_separately(self):
        payload = {"resultList": {"result": [{
            "title": "Diabetic retinopathy", "pmcid": "PMC123", "doi": "10.1234/x",
            "journalTitle": "Cureus", "firstPublicationDate": "2025-01-01",
            "license": "CC BY", "abstractText": "retinal diabetes",
            "authorList": {"author": [{"fullName": "A Author"}]},
        }]}}
        def opener(request, timeout=20):
            return FakeResponse(payload)
        oa = discovery.search_europe_pmc("diabetic retinopathy", 3, opener=opener)
        cureus = discovery.search_europe_pmc("diabetic retinopathy", 3,
                                             source_id="cureus_open_access", opener=opener)
        self.assertEqual(oa[0]["source_id"], "pubmed_central")
        self.assertEqual(cureus[0]["source_id"], "cureus_open_access")
        self.assertEqual(cureus[0]["licence_or_reuse_status"], "CC BY")

    def test_crossref_maps_doi_and_published_date(self):
        payload = {"message": {"items": [{
            "title": ["Synthetic ophthalmology review"], "DOI": "10.1234/review",
            "URL": "https://doi.org/10.1234/review",
            "published": {"date-parts": [[2024, 6, 1]]},
            "author": [{"given": "A", "family": "Author"}],
            "container-title": ["Journal"], "type": "journal-article",
        }]}}
        def opener(request, timeout=20):
            return FakeResponse(payload)
        result = discovery.search_crossref("ophthalmology review", 5, opener=opener)[0]
        self.assertEqual(result["doi"], "10.1234/review")
        self.assertEqual(result["published_on"], "2024-6-1")

    def test_discovery_produces_topic_coverage_and_provider_errors(self):
        topic = fixture()["topics"][0]
        with patch.object(discovery, "search_pubmed", return_value=[
            discovery._record("pubmed", "Diabetic retinopathy screening", "https://example.org/a")
        ]), patch.object(discovery, "search_europe_pmc", return_value=[]), patch.object(
            discovery, "search_crossref", return_value=[]):
            report = discovery.discover_curriculum(fixture(), limit=2, delay=0)
        self.assertEqual(report["coverage_summary"]["topics_with_resources"], 1)
        self.assertEqual(report["topic_coverage"][0]["topic_id"], "retina-01")
        self.assertEqual(report["topic_coverage"][0]["resources_found"], 1)
        self.assertGreater(report["topic_coverage"][0]["search_links_generated"], 0)
        self.assertIn("anki_shared_decks", report["topic_coverage"][0]["source_families_without_metadata_results"])
        self.assertFalse(report["governance"]["content_downloaded"])

    def test_invalid_provider_scheme_rejected(self):
        with self.assertRaises(discovery.DiscoveryError):
            discovery._request_json("http://example.org/api")

    def test_curriculum_requires_specialty_and_topics(self):
        with self.assertRaises(discovery.DiscoveryError):
            discovery.discover_curriculum({"specialty": "x", "topics": []})

    def test_resource_records_are_deduplicated_across_topics(self):
        curr = fixture()
        curr["topics"].append({**curr["topics"][0], "id": "retina-02", "name": "Macular disease"})
        shared = discovery._record("pubmed", "Retinal disease", "https://example.org/shared")
        with patch.object(discovery, "search_pubmed", return_value=[shared]), patch.object(
            discovery, "search_europe_pmc", return_value=[]), patch.object(
            discovery, "search_crossref", return_value=[]):
            report = discovery.discover_curriculum(curr, providers=("pubmed",), delay=0)
        self.assertEqual(len(report["resources"]), 1)
        self.assertEqual(set(report["resources"][0]["matched_topic_ids"]), {"retina-01", "retina-02"})


if __name__ == "__main__":
    unittest.main()
