"""Regression tests for defects found while reviewing the lint/quality-gate/evidence changes.

Each test pins one concrete problem so it cannot silently return:

* invalid JSON committed to the repo (``schemas/v17_contracts.schema.json`` lacked its closing brace)
* ``global`` statements in ``usiphus_signing`` that turned private-key PEM and signature bytes into module state
* evidence risk regexes with a stray space / missing number handling, and a substring-based rights gate
* auto-deck request budget, prompt determinism, Ollama URL handling and loopback enforcement
* faculty reference strings being ignored by the card quality gate
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import usiphus_auto_deck as deck  # noqa: E402
import usiphus_evidence as evidence  # noqa: E402
import usiphus_generation_quality as gq  # noqa: E402
import usiphus_signing as sg  # noqa: E402

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".checkpoints"}


def _walk(suffix: str):
    for path in sorted(ROOT.rglob(f"*{suffix}")):
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            yield path


class RepoHygiene(unittest.TestCase):
    def test_every_json_file_in_the_repository_parses(self) -> None:
        broken = []
        for path in _walk(".json"):
            try:
                json.loads(path.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeDecodeError) as exc:
                broken.append(f"{path.relative_to(ROOT)}: {exc}")
        self.assertEqual(broken, [])

    def test_schema_refs_resolve_inside_the_v17_schema(self) -> None:
        schema = json.loads((ROOT / "schemas" / "v17_contracts.schema.json").read_text(encoding="utf-8"))
        defs = set(schema.get("$defs", {}))
        refs = set()

        def collect(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                        refs.add(value.rsplit("/", 1)[1])
                    else:
                        collect(value)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect(schema)
        self.assertEqual(sorted(refs - defs), [], "schema $ref points at a missing definition")

    def test_engine_modules_do_not_use_global_statements(self) -> None:
        offenders = []
        for path in sorted(ROOT.glob("usiphus*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Global):
                    offenders.append(f"{path.name}:{node.lineno} global {', '.join(node.names)}")
        self.assertEqual(offenders, [])


class SigningDoesNotLeakKeyMaterial(unittest.TestCase):
    def test_keygen_and_sign_leave_no_module_level_secrets(self) -> None:
        for name in ("private_pem", "public_pem", "raw"):
            self.assertFalse(hasattr(sg, name), f"usiphus_signing.{name} exists before use")
        original = sg._FORCE_OPENSSL
        try:
            for force_openssl in (False, True):
                sg._FORCE_OPENSSL = force_openssl
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    tmp_path = Path(tmp)
                    try:
                        sg.generate_keypair(tmp_path / "k.pem", tmp_path / "p.pem")
                        signature = sg.Ed25519Signer(tmp_path / "k.pem").sign("message")
                    except (sg.SigningError, OSError):
                        continue            # this backend is not available here
                    self.assertTrue(signature.startswith("ed25519:"))
                    for name in ("private_pem", "public_pem", "raw"):
                        self.assertFalse(hasattr(sg, name), f"usiphus_signing.{name} leaked after signing")
        finally:
            sg._FORCE_OPENSSL = original


class EvidenceRiskPatterns(unittest.TestCase):
    def flags(self, text: str) -> list[str]:
        return evidence.detect_high_risk_claims(text)

    def test_doses_are_flagged_with_or_without_a_space(self) -> None:
        for text in ("Give 500mg orally", "Give 5 mg/kg", "Use 0.5 ml twice", "Give 2 units", "Apply 10 µg daily",
                     "Use a loading dose"):
            with self.subTest(text=text):
                self.assertIn("drug_dose", self.flags(text))

    def test_plain_words_that_look_like_units_are_not_doses(self) -> None:
        for text in ("The units of the visual pathway", "The g in the diagram", "A mg of caution"):
            with self.subTest(text=text):
                self.assertNotIn("drug_dose", self.flags(text))

    def test_thresholds_are_flagged_at_end_of_sentence_too(self) -> None:
        for text in ("Treat if IOP exceeds 21 mmHg.", "Target below 18 mmHg", "Glucose 7 mmol", "Reduce by 30%.",
                     "Intervene when pressure is > 25", "A cut-off applies"):
            with self.subTest(text=text):
                self.assertIn("threshold", self.flags(text))

    def test_non_numeric_prose_has_no_threshold_flag(self) -> None:
        self.assertEqual(self.flags("Describe the anatomy of the anterior segment."), [])


class EvidenceRightsGate(unittest.TestCase):
    @staticmethod
    def source(rights: str) -> dict:
        return {
            "source_id": "s1", "canonical_url": "https://example.org/g", "title": "Guideline",
            "source_type": "professional_guideline", "published_on": "2025-01-01", "guideline_version": "1",
            "licence_or_reuse_status": rights, "review_status": "faculty_reviewed",
            "access_status": "full_text_reviewed",
        }

    def test_only_exact_confirmed_markers_pass(self) -> None:
        for rights in ("permission_confirmed", "Permission_Confirmed", "  written_permission_confirmed  ",
                       "cc_by_confirmed", "public_domain_confirmed"):
            with self.subTest(rights=rights):
                self.assertEqual(evidence.validate_evidence_source(self.source(rights)), [])

    def test_markers_embedded_in_negations_or_extra_text_do_not_pass(self) -> None:
        for rights in ("no_permission_confirmed", "permission_confirmed_pending", "permission_confirmed_expired",
                       "not permission_confirmed", "cc_by_confirmed? no", "rights_unknown; permission_confirmed later"):
            with self.subTest(rights=rights):
                errors = evidence.validate_evidence_source(self.source(rights))
                self.assertTrue(any("rights" in e for e in errors))

    def test_corrupt_packet_error_names_the_file(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            Path(tmp, "t1.json").write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "t1.json"):
                evidence.load_topic_evidence_packets({"specialty": "x", "topics": [{"id": "t1"}]}, tmp)


class AutoDeckSafetyAndDeterminism(unittest.TestCase):
    def test_default_card_mix_order_does_not_depend_on_hash_seed(self) -> None:
        code = ("import sys, json; sys.path.insert(0, %r); import usiphus_auto_deck as d; "
                "print(json.dumps(list(d.requested_card_mix(11).items())))" % str(ROOT))
        outputs = set()
        for seed in ("1", "2", "3", "4"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
            outputs.add(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                       text=True, check=True).stdout)
        self.assertEqual(len(outputs), 1, f"prompt blueprint order varies by hash seed: {outputs}")

    def test_ollama_host_without_scheme_is_accepted(self) -> None:
        self.assertEqual(deck.normalize_ollama_url("127.0.0.1:11434"), "http://127.0.0.1:11434")
        self.assertEqual(deck.normalize_ollama_url("localhost:11434/"), "http://localhost:11434")
        self.assertEqual(deck.normalize_ollama_url("http://localhost:11434"), "http://localhost:11434")

    def test_remote_ollama_is_refused_unless_explicitly_allowed(self) -> None:
        for value in ("http://example.com:11434", "https://api.example.org", "192.168.1.20:11434"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    deck.normalize_ollama_url(value)
                self.assertTrue(deck.normalize_ollama_url(value, allow_remote=True).startswith("http"))

    def test_ollama_request_budget_counts_real_requests_and_cannot_be_retried_away(self) -> None:
        class Bad:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self):
                yield (json.dumps({"message": {"content": json.dumps({"cards": []})}, "done": True}) + "\n").encode()

        topic = {"id": "t1", "name": "Topic", "learning_objectives": ["a", "b", "c"], "card_count": 3}
        with patch.object(deck.urllib.request, "urlopen", return_value=Bad()) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "budget"):
                deck.generate_validated_topic_cards(
                    topic, specialty="x", level="students", model="m", ollama_url="http://localhost:11434",
                    count=3, max_output_tokens=1024, budget=deck.CallBudget(1))
        self.assertEqual(urlopen.call_count, 1, "a request was sent beyond the budget")


class SpecialtyInference(unittest.TestCase):
    def test_single_specialty_in_cards_is_inferred(self) -> None:
        self.assertEqual(deck.infer_specialty([{"specialty": "Urology"}, {"specialty": " urology "}]), "Urology")

    def test_mixed_or_missing_specialty_is_not_guessed(self) -> None:
        self.assertIsNone(deck.infer_specialty([{"specialty": "urology"}, {"specialty": "neurology"}]))
        self.assertIsNone(deck.infer_specialty([{"prompt": "x"}, "not a dict"]))


class FacultyReferenceStrings(unittest.TestCase):
    TOPIC = {"id": "t1", "name": "Topic", "domain_id": "d", "learning_objectives": ["Objective one is here"],
             "coverage_tags": ["tag"], "exam_emphasis": ["viva"], "reference_sources": ["Faculty reference A"]}

    def card(self, references):
        return [{"prompt": "What is the key teaching point here?",
                 "answer": "A structured answer that covers the definition, assessment and safe first steps clearly.",
                 "learning_objective": "Objective one is here", "card_type": "basic", "difficulty": "beginner",
                 "cognitive_level": "remember", "exam_domain": "x", "tags": ["tag"], "references": references}]

    def test_reference_supplied_as_plain_string_may_be_cited(self) -> None:
        report = gq.validate_topic_cards(dict(self.TOPIC), self.card(["Faculty reference A"]),
                                         specialty="x", requested_mix={"basic": 1})
        self.assertEqual(report["source_reference_count"], 1)

    def test_unsupplied_reference_is_still_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supplied by faculty"):
            gq.validate_topic_cards(dict(self.TOPIC), self.card(["Invented reference"]),
                                    specialty="x", requested_mix={"basic": 1})


if __name__ == "__main__":
    unittest.main()
