import json
import tempfile
import unittest
from pathlib import Path

import usiphus_generate
from usiphus_core import coerce_record, evaluate_quality
from usiphus_v9 import anki_exam_templates


class CardGeneratorTests(unittest.TestCase):
    def test_local_ollama_url_rejects_non_loopback_hosts(self):
        for value in ("https://example.com", "http://192.168.1.20:11434", "file:///tmp/ollama"):
            with self.subTest(value=value), self.assertRaises(usiphus_generate.GenerationError):
                usiphus_generate.local_ollama_url(value)

    def test_generated_card_requires_a_known_source_and_stays_unreviewed(self):
        competency = {"id": "OPH-01", "title": "Ophthalmic care"}
        sources = {"SRC-001": {"title": "Guideline", "reference": "https://example.org/guideline"}}
        response = json.dumps({"cards": [{
            "prompt": "What does this source recommend?",
            "answer": "It recommends a source-supported examination to assess the patient's clinical findings.",
            "learning_objective": "Recall the examination recommendation.",
            "card_type": "basic",
            "difficulty": "intermediate",
            "cognitive_level": "remember",
            "tags": ["diagnosis"],
            "source_ids": ["SRC-001"],
        }]})
        card = usiphus_generate.validate_generated(response, "ophthalmology", competency, sources, 1, 1)[0]
        self.assertEqual(card["evidence_status"], "unreviewed")
        self.assertEqual(card["review_status"], "draft")
        self.assertEqual(card["competency_ids"], ["OPH-01"])
        self.assertEqual(card["references"], ["https://example.org/guideline"])
        quality = evaluate_quality([coerce_record(card)])
        self.assertEqual(quality.error_count, 0)

    def test_model_cannot_cite_unknown_source(self):
        response = json.dumps({"cards": [{
            "prompt": "What does this source recommend?",
            "answer": "A concise source-supported answer should explain the clinical reasoning and implications.",
            "learning_objective": "Recall the recommendation.",
            "source_ids": ["SRC-999"],
        }]})
        with self.assertRaises(usiphus_generate.GenerationError):
            usiphus_generate.validate_generated(
                response, "ophthalmology", {"id": "OPH-01", "title": "Eye care"},
                {}, 1, 1,
            )

    def test_source_backed_generation_requires_a_verbatim_evidence_excerpt(self):
        source_text = "For study purposes, refer urgently for this confirmed finding."
        sources = {"SRC-001": {
            "title": "Guideline",
            "reference": "https://example.org",
            "content": source_text,
        }}
        base_card = {
            "prompt": "What action is recommended for this finding?",
            "answer": "Refer urgently for this confirmed finding because the source explicitly recommends referral.",
            "learning_objective": "Recall the referral recommendation.",
            "source_ids": ["SRC-001"],
            "evidence_quote": "refer urgently for this confirmed finding.",
        }
        cards = usiphus_generate.validate_generated(
            json.dumps({"cards": [base_card]}), "ophthalmology",
            {"id": "OPH-01", "title": "Referral"}, sources, 1, 1,
        )
        self.assertEqual(cards[0]["evidence_excerpt"], base_card["evidence_quote"])
        self.assertEqual(cards[0]["evidence_source_ids"], ["SRC-001"])
        self.assertEqual(coerce_record(cards[0]).evidence_excerpt, base_card["evidence_quote"])
        base_card["evidence_quote"] = "The source recommends a different action."
        with self.assertRaisesRegex(usiphus_generate.GenerationError, "exact excerpt"):
            usiphus_generate.validate_generated(
                json.dumps({"cards": [base_card]}), "ophthalmology",
                {"id": "OPH-01", "title": "Referral"}, sources, 1, 1,
            )

    def test_source_backed_generation_rejects_irrelevant_quotes_and_unstated_numbers(self):
        source_text = (
            "For glaucoma diagnosis, use standard automated perimetry to assess visual fields. "
            "Refer for an intraocular pressure of 24 mmHg or more."
        )
        sources = {"SRC-001": {
            "title": "Guideline",
            "reference": "https://example.org",
            "content": source_text,
        }}
        base_card = {
            "prompt": "Which assessment measures visual fields?",
            "answer": "Standard automated perimetry assesses visual fields in glaucoma diagnosis.",
            "learning_objective": "Recall the recommended visual field assessment.",
            "source_ids": ["SRC-001"],
            "evidence_quote": "Refer for an intraocular pressure of 24 mmHg or more.",
        }
        with self.assertRaisesRegex(usiphus_generate.GenerationError, "does not meaningfully overlap"):
            usiphus_generate.validate_generated(
                json.dumps({"cards": [base_card]}), "ophthalmology",
                {"id": "OPH-01", "title": "Glaucoma diagnosis"}, sources, 1, 1,
            )
        base_card["evidence_quote"] = "For glaucoma diagnosis, use standard automated perimetry to assess visual fields."
        base_card["answer"] = "Standard automated perimetry assesses visual fields in glaucoma diagnosis at 31 mmHg."
        with self.assertRaisesRegex(usiphus_generate.GenerationError, "numeric claims absent"):
            usiphus_generate.validate_generated(
                json.dumps({"cards": [base_card]}), "ophthalmology",
                {"id": "OPH-01", "title": "Glaucoma diagnosis"}, sources, 1, 1,
            )

    def test_mcq_requires_four_unique_options_and_correct_option(self):
        response = json.dumps({"cards": [{
            "prompt": "Which option is supported?",
            "answer": "A source-supported answer should explain the clinical reasoning and implications.",
            "learning_objective": "Select the supported statement.",
            "card_type": "mcq",
            "options": ["A", "B"],
            "correct_option": "A",
            "source_ids": ["SRC-001"],
        }]})
        sources = {"SRC-001": {"title": "Guideline", "reference": "https://example.org"}}
        with self.assertRaises(usiphus_generate.GenerationError):
            usiphus_generate.validate_generated(
                response, "ophthalmology", {"id": "OPH-01", "title": "Eye care"},
                sources, 1, 1,
            )

    def test_mcq_keeps_a_source_grounded_rationale(self):
        response = {"cards": [{
            "prompt": "Which option best matches the cited recommendation?",
            "answer": "Option A is correct because it matches the cited clinical recommendation.",
            "learning_objective": "Apply the recommendation.",
            "card_type": "mcq",
            "options": [
                "Option A states a supported clinical assessment.",
                "Option B proposes an unsupported management step.",
                "Option C omits the key clinical finding.",
                "Option D contradicts the supplied recommendation.",
            ],
            "correct_option": "Option A states a supported clinical assessment.",
            "rationale": "The cited source supports A; the other options are not stated.",
            "source_ids": ["SRC-001"],
        }]}
        sources = {"SRC-001": {"title": "Guideline", "reference": "https://example.org"}}
        card = usiphus_generate.validate_generated(
            json.dumps(response), "ophthalmology",
            {"id": "OPH-01", "title": "Eye care"}, sources, 1, 1,
        )[0]
        self.assertIn("Rationale:", card["answer"])
        self.assertEqual(card["correct_option"], "Option A states a supported clinical assessment.")

    def test_quality_gate_rejects_answer_fragments_and_repeated_words(self):
        source = {"SRC-001": {"title": "Guideline", "reference": "https://example.org"}}
        competency = {"id": "OPH-01", "title": "Eye care"}
        valid_answer = "The source supports this action because it addresses the stated clinical finding."
        for prompt, answer in (
            ("What is the recommended recommended assessment?", valid_answer),
            ("What is the recommended, recommended assessment?", valid_answer),
            ("What is the recommended assessment?", ": Standard automated perimetry"),
            ("What is the recommended assessment?", "Standard automated perimetry"),
        ):
            response = json.dumps({"cards": [{
                "prompt": prompt,
                "answer": answer,
                "learning_objective": "Apply the cited clinical recommendation.",
                "source_ids": ["SRC-001"],
            }]})
            with self.subTest(prompt=prompt, answer=answer), self.assertRaises(
                usiphus_generate.GenerationError
            ):
                usiphus_generate.validate_generated(
                    response, "ophthalmology", competency, source, 1, 1,
                )

    def test_format_mix_targets_are_weighted_and_rotate_across_batches(self):
        types = ["basic", "mcq", "clinical_case", "viva", "osce"]
        weights = {card_type: 20 for card_type in types}
        first = usiphus_generate._format_targets(types, weights, 5, {})
        self.assertEqual(first, {card_type: 1 for card_type in types})
        second = usiphus_generate._format_targets(types, weights, 5, first)
        self.assertEqual(second, first)

    def test_ollama_schema_constrains_enabled_types_and_configured_sources(self):
        schema = usiphus_generate.card_response_schema(
            ["SRC-001", "SRC-002"], ["basic", "mcq"], ["Source-supported excerpt with enough detail."]
        )
        properties = schema["properties"]["cards"]["items"]["properties"]
        self.assertEqual(properties["source_ids"]["items"]["enum"], ["SRC-001", "SRC-002"])
        self.assertEqual(properties["card_type"]["enum"], ["basic", "mcq"])
        self.assertIn("evidence_quote", schema["properties"]["cards"]["items"]["required"])
        self.assertEqual(
            properties["evidence_quote"]["enum"], ["Source-supported excerpt with enough detail."]
        )
        self.assertEqual(properties["options"]["items"]["minLength"], 10)
        self.assertEqual(properties["rubric"]["minItems"], 3)

    def test_evidence_quotes_are_extracted_as_verbatim_source_sentences(self):
        source_text = "# Guidance\n- Refer urgently when the confirmed finding is present."
        sources = {"SRC-001": {"content": source_text}}
        quotes = usiphus_generate.evidence_quote_candidates(sources)
        self.assertIn("Refer urgently when the confirmed finding is present.", quotes)

    def test_anki_export_has_a_distinct_template_for_each_exam_layout(self):
        templates, style = anki_exam_templates()
        self.assertEqual(
            [template["name"] for template in templates],
            ["Rapid recall", "Single best answer", "Clinical case", "Viva", "OSCE station"],
        )
        self.assertTrue(all(template["qfmt"] and template["afmt"] for template in templates))
        self.assertIn(".osce", style)
        self.assertIn("{{EvidenceExcerpt}}", templates[0]["afmt"])
        self.assertIn(".source-evidence", style)
        self.assertIn("@media(max-width:600px)", style)
        self.assertIn("prefers-color-scheme:dark", style)

    def test_run_writes_draft_cards_and_preserves_base_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curriculum = root / "curriculum.json"
            source = root / "source.md"
            base_cards = root / "base.json"
            output = root / "generated.json"
            curriculum.write_text(json.dumps({
                "specialty": "ophthalmology",
                "competencies": [
                    {"id": "OPH-01", "title": "Eye assessment"},
                    {"id": "OPH-02", "title": "Eye health"},
                ],
            }), encoding="utf-8")
            source.write_text("A source-grounded study fact.", encoding="utf-8")
            base_cards.write_text(json.dumps({"cards": [{
                "card_id": "OPH-BASE-1", "prompt": "Existing question?",
                "answer": "Existing answer with sufficient detail for the learner to understand the clinical reasoning.",
            }]}), encoding="utf-8")
            config = {
                "schema_version": 1,
                "specialty": "ophthalmology",
                "curriculum": str(curriculum),
                "source_material": [{"title": "Source", "reference": "https://example.org", "path": str(source)}],
                "base_cards": str(base_cards),
                "output": str(output),
                "batch_size": 1,
                "max_no_progress_batches": 2,
                "max_batches": None,
                "generator": {"provider": "ollama", "base_url": "http://localhost:11434", "model": "test"},
            }
            config_path = root / "generator.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            config, _ = usiphus_generate.load_config(config_path)

            def fake_generator(*_args):
                prompt = _args[2]
                competency = "OPH-01" if "OPH-01" in prompt else "OPH-02"
                index = fake_generator.calls.get(competency, 0)
                fake_generator.calls[competency] = index + 1
                if "QUALITY-CONTROL RETRY" in prompt:
                    fake_generator.retries.append(competency)
                if index == 0:
                    return json.dumps({"cards": [{
                        "prompt": f"Source-grounded source-grounded study fact for {competency}?",
                        "answer": "A source-grounded answer with enough detail to explain the clinical reasoning.",
                        "learning_objective": "Apply the competency.",
                        "source_ids": ["SRC-001"],
                        "evidence_quote": "A source-grounded study fact.",
                    }]})
                if index > 1:
                    return json.dumps({"cards": []})
                return json.dumps({"cards": [{
                    "prompt": f"What does the source-grounded study fact indicate for {competency}?",
                    "answer": "A source-grounded answer with enough detail to explain the clinical reasoning.",
                    "learning_objective": "Apply the competency.",
                    "source_ids": ["SRC-001"],
                    "evidence_quote": "A source-grounded study fact.",
                }]})

            fake_generator.calls = {}
            fake_generator.retries = []
            self.assertEqual(usiphus_generate.run(config, fake_generator), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result["cards"]), 3)
            self.assertEqual(result["cards"][0]["card_id"], "OPH-BASE-1")
            self.assertTrue(all(card.get("evidence_status") == "unreviewed" for card in result["cards"][1:]))
            self.assertEqual(fake_generator.retries, ["OPH-01", "OPH-02"])

    def test_batch_generation_has_no_500_card_total_cap_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curriculum = root / "curriculum.json"
            source = root / "source.md"
            output = root / "generated.json"
            curriculum.write_text(json.dumps({
                "specialty": "ophthalmology",
                "competencies": [{"id": "OPH-01", "title": "Eye assessment"}],
            }), encoding="utf-8")
            source.write_text("A source-grounded study fact.", encoding="utf-8")
            config = {
                "schema_version": 1,
                "specialty": "ophthalmology",
                "curriculum": str(curriculum),
                "source_material": [{"title": "Source", "reference": "https://example.org", "path": str(source)}],
                "base_cards": None,
                "output": str(output),
                "batch_size": 501,
                "max_no_progress_batches": 1,
                "max_batches": 1,
                "generator": {"provider": "ollama", "base_url": "http://localhost:11434", "model": "test"},
            }
            config_path = root / "generator.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            config, _ = usiphus_generate.load_config(config_path)

            def fake_generator(*_args):
                return json.dumps({"cards": [{
                    "prompt": f"What does the source-grounded study fact indicate in question {index}?",
                    "answer": "This source-grounded study fact explains the finding and its clinical implications.",
                    "learning_objective": "Apply the competency.",
                    "source_ids": ["SRC-001"],
                    "evidence_quote": "A source-grounded study fact.",
                } for index in range(501)]})

            self.assertEqual(usiphus_generate.run(config, fake_generator), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result["cards"]), 501)
            self.assertEqual(result["generator"]["cards_generated"], 501)
            self.assertEqual(result["cards"][-1]["card_id"], "GEN-OPH-01-0501")

    def test_rerunning_resumes_from_checkpoint_and_keeps_ids_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curriculum = root / "curriculum.json"
            source = root / "source.md"
            output = root / "generated.json"
            curriculum.write_text(json.dumps({
                "specialty": "ophthalmology",
                "competencies": [{"id": "OPH-01", "title": "Eye assessment"}],
            }), encoding="utf-8")
            source.write_text("A source-grounded study fact.", encoding="utf-8")
            config = {
                "specialty": "ophthalmology",
                "curriculum": str(curriculum),
                "source_material": [{"title": "Source", "reference": "https://example.org", "path": str(source)}],
                "base_cards": None,
                "output": str(output),
                "batch_size": 1,
                "max_no_progress_batches": 1,
                "max_batches": 1,
                "generator": {"provider": "ollama", "base_url": "http://localhost:11434", "model": "test"},
            }

            def response(question):
                return json.dumps({"cards": [{
                    "prompt": f"What does the source-grounded study fact indicate in {question}",
                    "answer": "A source-grounded answer with enough detail to explain the clinical reasoning.",
                    "learning_objective": "Apply the competency.",
                    "source_ids": ["SRC-001"],
                    "evidence_quote": "A source-grounded study fact.",
                }]})

            self.assertEqual(usiphus_generate.run(config, lambda *_args: response("First source-grounded question?")), 0)
            self.assertEqual(usiphus_generate.run(config, lambda *_args: response("Second source-grounded question?")), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual([card["card_id"] for card in result["cards"]], [
                "GEN-OPH-01-0001", "GEN-OPH-01-0002",
            ])
            result["cards"][0].pop("evidence_excerpt")
            result["cards"][0].pop("evidence_source_ids")
            output.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(usiphus_generate.GenerationError, "lacks a source-verified evidence excerpt"):
                usiphus_generate.run(config, lambda *_args: response("Third source-grounded question?"))

    def test_supported_exam_formats_are_v9_card_types(self):
        source = {"SRC-001": {"title": "Guideline", "reference": "https://example.org"}}
        competency = {"id": "OPH-01", "title": "Eye care"}
        osce = {"cards": [{
            "prompt": "Examine this fictional patient and explain your next steps:",
            "answer": "A structured source-supported station answer should assess the candidate's clinical reasoning.",
            "learning_objective": "Demonstrate the relevant examination.",
            "card_type": "osce",
            "rubric": [
                "Explains the source-supported assessment.",
                "Identifies the key clinical finding.",
                "Communicates an appropriate evidence-based next step.",
            ],
            "source_ids": ["SRC-001"],
        }]}
        result = usiphus_generate.validate_generated(
            json.dumps(osce), "ophthalmology", competency, source, 1, 1
        )[0]
        self.assertEqual(result["card_type"], "osce")
        self.assertEqual(len(result["rubric"]), 3)


if __name__ == "__main__":
    unittest.main()
