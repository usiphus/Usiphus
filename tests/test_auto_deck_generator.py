import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usiphus_auto_deck as generator


class AutomatedDeckGeneratorTests(unittest.TestCase):
    def test_curriculum_requires_specialty_and_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({"specialty": "ophthalmology", "topics": [{"name": "Cataract"}]}))
            self.assertEqual(generator.load_curriculum(path)["specialty"], "ophthalmology")

    def test_curriculum_rejects_empty_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({"specialty": "ophthalmology", "topics": []}))
            with self.assertRaises(ValueError):
                generator.load_curriculum(path)

    def test_json_extraction_handles_fences(self):
        value = generator._extract_json("""```json
{"cards": []}
```""")
        self.assertEqual(value, {"cards": []})

    def test_topic_cards_get_stable_ids_and_unreviewed_status(self):
        response = {"message": {"content": json.dumps({"cards": [
            {"prompt": "What is cataract?", "answer": "An opacity of the crystalline lens."}
        ]})}}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": response["message"], "done": True}) + "\n").encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()):
            cards = generator.generate_topic_cards(
                {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                model="test-model", ollama_url="http://localhost:11434",
                count=1, max_output_tokens=1024)
        self.assertEqual(cards[0]["card_id"], "OLLAMA-CATARACT-001")
        self.assertEqual(cards[0]["evidence_status"], "unreviewed")

    def test_cli_exposes_timeout_override_resume_and_card_count_override(self):
        args = generator.parse_args([
            "--generate", "--curriculum", "curriculum.json",
            "--cards-per-topic", "3", "--override-curriculum-card-counts",
            "--ollama-timeout", "900", "--resume",
        ])
        self.assertEqual(args.cards_per_topic, 3)
        self.assertTrue(args.override_curriculum_card_counts)
        self.assertEqual(args.ollama_timeout, 900)
        self.assertTrue(args.resume)

    def test_generation_passes_configured_timeout_to_ollama(self):
        response = {"message": {"content": json.dumps({"cards": [
            {"prompt": "What is cataract?", "answer": "An opacity of the crystalline lens."}
        ]})}}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": response["message"], "done": True}) + "\n").encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()) as request:
            generator.generate_topic_cards(
                {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                model="test-model", ollama_url="http://localhost:11434",
                count=1, max_output_tokens=1024, request_timeout=777)
        self.assertEqual(request.call_args.kwargs["timeout"], 777)

    def test_generation_retries_timeout_and_reports_local_ollama_hint(self):
        with patch.object(generator.urllib.request, "urlopen", side_effect=TimeoutError("timed out")) as request:
            with self.assertRaisesRegex(RuntimeError, "Try a smaller card count"):
                generator.generate_topic_cards(
                    {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                    model="test-model", ollama_url="http://localhost:11434",
                    count=1, max_output_tokens=1024, request_timeout=17)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["timeout"], 17)

    def test_cli_uses_local_defaults_without_credentials(self):
        args = generator.parse_args(["--generate", "--curriculum", "curriculum.json"])
        self.assertIsNone(args.specialty)
        self.assertIsNone(args.level)
        self.assertEqual(args.max_ollama_calls, 500)
        self.assertEqual(args.max_output_tokens, 4096)
        self.assertEqual(args.model, "llama3.1:8b")
        self.assertEqual(args.ollama_url, "http://localhost:11434")

    def test_default_card_mix_is_exam_balanced_and_sums_to_count(self):
        mix = generator.requested_card_mix(6)
        self.assertEqual(sum(mix.values()), 6)
        self.assertEqual(mix, {"basic": 1, "mcq": 2, "clinical_case": 1, "viva": 1, "osce": 1})

    def test_custom_card_mix_must_sum_to_topic_count(self):
        self.assertEqual(
            generator.requested_card_mix(4, {"mcq": 2, "osce": 2}),
            {"mcq": 2, "osce": 2},
        )
        with self.assertRaisesRegex(ValueError, "must sum"):
            generator.requested_card_mix(4, {"mcq": 3})

    def test_card_mix_rejects_unknown_types_and_boolean_counts(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            generator.requested_card_mix(1, {"flashcard": 1})
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            generator.requested_card_mix(1, {"mcq": True})

    def test_generation_rejects_more_cards_than_requested(self):
        response = {"message": {"content": json.dumps({"cards": [
            {"prompt": "Q1?", "answer": "Answer one is sufficiently long."},
            {"prompt": "Q2?", "answer": "Answer two is sufficiently long."}
        ]})}}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": response["message"], "done": True}) + "\n").encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(RuntimeError, "Could not complete"):
                generator.generate_topic_cards(
                    {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                    model="test-model", ollama_url="http://localhost:11434",
                    count=1, max_output_tokens=1024)

    def test_generation_rejects_fewer_cards_than_requested(self):
        response = {"message": {"content": json.dumps({"cards": [
            {"prompt": "Q1?", "answer": "A sufficiently detailed answer."}
        ]})}}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": response["message"], "done": True}) + "\n").encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(RuntimeError, "Could not complete"):
                generator.generate_topic_cards(
                    {"name": "Cataract"}, specialty="ophthalmology", level="medical students",
                    model="test-model", ollama_url="http://localhost:11434",
                    count=2, max_output_tokens=1024)

    def test_ollama_call_limit_blocks_before_network_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({
                "specialty": "ophthalmology",
                "topics": [{"name": "A"}, {"name": "B"}]
            }))
            args = generator.parse_args([
                "--generate", "--curriculum", str(path),
                "--max-ollama-calls", "1", "--output-dir", str(Path(tmp) / "out")
            ])
            with patch.object(generator.urllib.request, "urlopen") as request:
                with self.assertRaisesRegex(ValueError, "max-ollama-calls"):
                    generator.build(args)
                request.assert_not_called()

    def test_full_ophthalmology_blueprint_covers_all_domains(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "ophthalmology" / "curriculum.json"
        curriculum = generator.load_curriculum(path)
        domains = curriculum["curriculum_domains"]
        topics = curriculum["topics"]
        domain_ids = {item["id"] for item in domains}
        self.assertEqual(len(domains), 16)
        self.assertEqual(len(topics), 52)
        self.assertEqual(curriculum["planned_card_count"], 312)
        self.assertEqual(len({topic["id"] for topic in topics}), len(topics))
        self.assertTrue(all(topic["domain_id"] in domain_ids for topic in topics))
        self.assertTrue(all(len(topic["learning_objectives"]) >= 3 for topic in topics))
        self.assertTrue(all(topic["coverage_tags"] for topic in topics))

    def test_generation_payload_includes_curriculum_domain_and_coverage(self):
        response = {"message": {"content": json.dumps({"cards": [
            {"prompt": "Name a cause of retinal detachment?", "answer": "A retinal break."}
        ]})}}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": response["message"], "done": True}) + "\n").encode()
        topic = {"id": "vitreoretinal-02", "domain_id": "vitreoretinal",
                 "domain": "Vitreoretinal disease and surgery", "name": "Retinal detachment",
                 "coverage_tags": ["retinal detachment"], "learning_objectives": ["Recognize detachment"]}
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()) as request:
            generator.generate_topic_cards(
                topic, specialty="ophthalmology", level="residents", model="test-model",
                ollama_url="http://localhost:11434", count=1, max_output_tokens=1024)
        sent = json.loads(request.call_args.args[0].data.decode())
        payload = json.loads(sent["messages"][1]["content"])
        self.assertEqual(payload["curriculum_domain"], "Vitreoretinal disease and surgery")
        self.assertEqual(payload["domain_id"], "vitreoretinal")
        self.assertEqual(payload["coverage_tags"], ["retinal detachment"])\n        self.assertTrue(sent["stream"])\n        self.assertEqual(sent["keep_alive"], "30m")

    def test_curriculum_rejects_topic_with_unknown_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curriculum.json"
            path.write_text(json.dumps({
                "specialty": "ophthalmology",
                "curriculum_domains": [{"id": "retina", "name": "Retina"}],
                "topics": [{"id": "retina-01", "domain_id": "glaucoma",
                            "name": "Retinal disease", "coverage_tags": ["retina"]}]
            }))
            with self.assertRaisesRegex(ValueError, "unknown curriculum domain"):
                generator.load_curriculum(path)


    def test_partial_generation_repairs_only_invalid_slots_and_fills_metadata(self):
        first = {"cards": [
            {"prompt": "Define cataract?", "answer": "Opacity of the crystalline lens causing visual impairment."},
            {"prompt": "Incomplete MCQ", "answer": "Too short"}
        ]}
        repair = {"cards": [
            {"prompt": "Which finding suggests cataract?", "answer": "Lens opacity causes reduced red reflex. A is correct because it directly identifies lens opacity; the other options describe unrelated findings.", "options": ["Lens opacity", "Corneal ulcer", "Retinal tear", "Optic neuritis"], "correct_option": "A"}
        ]}
        events = [first, repair]
        class FakeResponse:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": {"content": json.dumps(self.payload)}, "done": True}) + "\\n").encode()
        with patch.object(generator.urllib.request, "urlopen", side_effect=[FakeResponse(x) for x in events]) as request:
            cards = generator.generate_topic_cards(
                {"id": "lens-01", "name": "Cataract", "coverage_tags": ["lens opacity"],
                 "learning_objectives": ["Recognize cataract"], "reference_sources": ["Faculty handbook"],
                 "card_mix": {"basic": 1, "mcq": 1}},
                specialty="ophthalmology", level="medical students", model="test-model",
                ollama_url="http://localhost:11434", count=2, max_output_tokens=2048)
        self.assertEqual(request.call_count, 2)
        repair_payload = json.loads(request.call_args_list[1].args[0].data.decode())
        repair_user = json.loads(repair_payload["messages"][1]["content"])
        self.assertEqual(repair_user["cards_to_write"], [{"slot": 1, "type": "mcq"}])
        self.assertEqual(cards[0]["card_type"], "basic")
        self.assertEqual(cards[1]["card_type"], "mcq")
        self.assertEqual(cards[0]["card_id"], "OLLAMA-LENS-01-001")
        self.assertEqual(cards[0]["tags"], ["lens opacity", "lens-01"])
        self.assertEqual(cards[0]["references"], ["Faculty handbook"])
        self.assertEqual(cards[0]["evidence_status"], "unreviewed")

    def test_compact_prompt_does_not_delegate_metadata_to_model(self):
        response = {"cards": [
            {"prompt": "Define cataract?", "answer": "Opacity of the crystalline lens causing visual impairment."}
        ]}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": {"content": json.dumps(response)}, "done": True}) + "\\n").encode()
        with patch.object(generator.urllib.request, "urlopen", return_value=FakeResponse()) as request:
            generator.generate_topic_cards(
                {"name": "Cataract", "coverage_tags": ["lens"], "learning_objectives": ["Recognize cataract"]},
                specialty="ophthalmology", level="medical students", model="test-model",
                ollama_url="http://localhost:11434", count=1, max_output_tokens=1024)
        body = json.loads(request.call_args.args[0].data.decode())
        prompt = body["messages"][0]["content"]
        self.assertIn("Do not add metadata", prompt)
        self.assertNotIn("card_id", prompt)


if __name__ == "__main__":
    unittest.main()
