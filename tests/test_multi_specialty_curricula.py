import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "specialties" / "manifest.json"


class MultiSpecialtyCurriculumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_manifest_lists_existing_eye_and_new_specialties(self):
        entries = self.manifest["specialties"]
        ids = [entry["id"] for entry in entries]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("ophthalmology", ids)
        self.assertEqual(len([x for x in entries if x["id"] != "ophthalmology"]), 19)

    def test_every_new_curriculum_is_structurally_complete(self):
        for entry in self.manifest["specialties"]:
            if entry["id"] == "ophthalmology":
                continue
            path = ROOT / entry["path"]
            curriculum = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(curriculum["specialty"], entry["id"])
            self.assertEqual(curriculum["planned_topic_count"], len(curriculum["topics"]))
            self.assertEqual(
                curriculum["planned_card_count"],
                sum(topic["card_count"] for topic in curriculum["topics"]),
            )
            domain_ids = {domain["id"] for domain in curriculum["curriculum_domains"]}
            topic_ids = [topic["id"] for topic in curriculum["topics"]]
            self.assertEqual(len(topic_ids), len(set(topic_ids)))
            self.assertEqual(len(domain_ids), len(curriculum["curriculum_domains"]))
            for topic in curriculum["topics"]:
                self.assertIn(topic["domain_id"], domain_ids)
                self.assertTrue(topic["name"].strip())
                self.assertGreaterEqual(len(topic["learning_objectives"]), 3)
                self.assertTrue(all(obj.strip() for obj in topic["learning_objectives"]))
                self.assertEqual(len(topic["source_types_to_discover"]), len(set(topic["source_types_to_discover"])))
                self.assertTrue(topic["source_search_terms"])
                self.assertEqual(topic["reference_sources"], [])
            self.assertEqual(curriculum["card_blueprint"]["cards_per_topic"], 6)

    def test_shared_discovery_families_include_requested_sources(self):
        expected = {
            "anki_shared_decks", "doctorde_challenges", "cureus_open_access",
            "pubmed_central", "pubmed", "doaj", "public_exam_prep",
            "professional_guidelines", "open_textbooks_resources",
        }
        for entry in self.manifest["specialties"]:
            if entry["id"] == "ophthalmology":
                continue
            curriculum = json.loads((ROOT / entry["path"]).read_text(encoding="utf-8"))
            for topic in curriculum["topics"]:
                self.assertTrue(expected.issubset(set(topic["source_types_to_discover"])))


if __name__ == "__main__":
    unittest.main()
