
#!/usr/bin/env python3
"""Usiphus V7 — reliable medical-exam flashcard builder.

Features
--------
* Multi-source JSON ingestion (exam cards, case library, blueprint).
* Canonical data model with stable IDs and source provenance.
* Structural and examination-format validation (MCQ, OSCE, viva, case, basic).
* Deterministic Anki note GUIDs and collision-resistant model/deck IDs.
* Atomic TSV/JSON/Markdown exports.
* Defensive AnkiConnect handling.
* JSON quality report, transparent metadata-completeness score, and diagnostic self-tests.
* Exam-intelligence metadata: cognitive level, exam domain, learning objective,
  OSCE rubric, evidence-review status/date/reviewer.
* Lexical near-duplicate screening for human review (not semantic AI matching).

Important clinical limitation
------------------------------
This program validates structure and educational presentation heuristically.
It DOES NOT establish medical factual accuracy, guideline currency, or clinical
safety. Medical claims must be reviewed against current authoritative sources
by a qualified subject-matter reviewer.

Example
-------
python usiphus_v7.py --specialty ophthalmology --blueprint blueprint.json --output ./build
python usiphus_v7.py --exam-cards cards.json --specialty surgery --self-test
python usiphus_v7.py --exam-cards cards.json --specialty ophthalmology --apkg --study-guide
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

VERSION = "7.0.0"
REPORT_SCHEMA_VERSION = 4
LOG = logging.getLogger("usiphus")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import (  # noqa: E402  (shared, single-source implementations)
    canonicalize_text, canonicalize_multiline, canonicalize_card_id, canonicalize_specialty,
    canonicalize_tags, canonicalize_string_list, canonicalize_prompt, stable_digest,
    stable_blueprint_id, anki_numeric_id, anki_guid, lexical_similarity,
    find_near_duplicate_pairs, atomic_write_text, push_to_ankiconnect, parse_as_of,
    tokens as _tokens, valid_iso_date as _valid_iso_date,
)
from usiphus_core import (  # noqa: E402  (shared implementation)
    ANKI_CONNECT_TIMEOUT_SECONDS,
    ANSWER_MAX_CHARS,
    ANSWER_MIN_CHARS,
    Card,
    CardReport,
    Finding,
    FindingCode,
    MAX_INPUT_FILE_BYTES,
    PROMPT_MAX_CHARS,
    QualityReport,
    STOP_WORDS,
    TOKEN_RE,
    TSV_HEADER,
    VALID_CARD_TYPES,
    VALID_COGNITIVE_LEVELS,
    VALID_DIFFICULTIES,
    VALID_EVIDENCE_STATUSES,
    _NON_ID_RE,
    _TRAILING_Q_RE,
    _WHITESPACE_RE,
    _load_genanki,
    build_blueprint_cards,
    build_case_cards,
    build_exam_cards,
    calculate_quality_score,
    coerce_record,
    evaluate_quality,
    export_study_guide,
    load_json_cards,
    report_to_dict,
    write_tsv,
)












# ---------------------------------------------------------------------------
# Normalization and stable identifiers
# ---------------------------------------------------------------------------























# ---------------------------------------------------------------------------
# Input parsing and schema validation
# ---------------------------------------------------------------------------











# ---------------------------------------------------------------------------
# Structural and educational-format quality checks
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# Atomic output helpers
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------



def export_apkg(cards: Sequence[Card], destination: Path, deck_name: str) -> None:
    genanki = _load_genanki()
    model_id = anki_numeric_id("usiphus-model-v7", "medical-card")
    deck_id = anki_numeric_id("usiphus-deck-v7", deck_name)

    model = genanki.Model(
        model_id,
        "Usiphus V7 Medical Model",
        fields=[
            {"name": "Prompt"}, {"name": "Answer"}, {"name": "Options"},
            {"name": "CorrectOption"}, {"name": "LearningObjective"},
            {"name": "Tags"}, {"name": "CardID"}, {"name": "References"},
            {"name": "Rubric"}, {"name": "EvidenceStatus"},
        ],
        templates=[{
            "name": "Usiphus Card",
            "qfmt": (
                "{{Prompt}}"
                "{{#Options}}<hr><ol>{{Options}}</ol>{{/Options}}"
            ),
            "afmt": (
                '{{FrontSide}}<hr id="answer"><b>Answer</b><br>{{Answer}}'
                '{{#CorrectOption}}<br><b>Correct option:</b> {{CorrectOption}}{{/CorrectOption}}'
                '{{#LearningObjective}}<br><b>Learning objective:</b> {{LearningObjective}}{{/LearningObjective}}'
                '{{#References}}<br><small>{{References}}</small>{{/References}}'
                '{{#Rubric}}<br><b>Checklist</b><br>{{Rubric}}{{/Rubric}}'
                '<br><small>Evidence status: {{EvidenceStatus}}</small>'
            ),
        }],
    )
    deck = genanki.Deck(deck_id, deck_name)

    for card in cards:
        options_html = "".join(f"<li>{html.escape(option)}</li>" for option in card.options)
        fields = [
            html.escape(card.prompt),
            html.escape(card.answer).replace("\n", "<br>"),
            options_html,
            html.escape(card.correct_option),
            html.escape(card.learning_objective),
            html.escape("; ".join(card.tags)),
            html.escape(card.card_id),
            html.escape("; ".join(card.references)),
            "".join(f"<li>{html.escape(item)}</li>" for item in card.rubric),
            html.escape(card.evidence_status),
        ]
        note = genanki.Note(model=model, fields=fields, guid=anki_guid(card.card_id), tags=list(card.tags))
        deck.add_note(note)

    destination.parent.mkdir(parents=True, exist_ok=True)
    # genanki writes to a path; write to a unique temporary path then replace.
    fd, temp_path = tempfile.mkstemp(prefix=".usiphus-", suffix=".apkg", dir=destination.parent)
    os.close(fd)
    try:
        genanki.Package(deck).write_to_file(temp_path)
        os.replace(temp_path, destination)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    LOG.info("APKG written to %s", destination)






# ---------------------------------------------------------------------------
# CLI orchestration
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Usiphus V7 medical exam deck builder")
    parser.add_argument("--specialty", help="Default specialty for imported cards")
    parser.add_argument("--exam-cards", help="JSON exam-card file")
    parser.add_argument("--case-library", help="JSON case-library file")
    parser.add_argument("--blueprint", help="JSON blueprint file")
    parser.add_argument("--output", default="./build", help="Output directory")
    parser.add_argument("--deck-name", help="Anki deck name")
    parser.add_argument("--apkg", action="store_true", help="Export an Anki package")
    parser.add_argument("--study-guide", action="store_true", help="Export Markdown study guide")
    parser.add_argument("--anki-connect", action="store_true", help="Push to AnkiConnect")
    parser.add_argument("--anki-url", default="http://localhost:8765", help="AnkiConnect endpoint")
    parser.add_argument("--force", action="store_true", help="Export despite quality errors")
    parser.add_argument("--fail-on-warn", action="store_true", help="Abort if any warning exists")
    parser.add_argument("--require-reviewed", action="store_true", help="Require evidence_status=reviewed for every card")
    parser.add_argument("--max-review-age-days", type=int, default=730, help="Warn when evidence review is older than this many days")
    parser.add_argument("--as-of", help="Reproducible reference date YYYY-MM-DD (default: today)")
    parser.add_argument("--self-test", action="store_true", help="Run built-in diagnostics")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def run_pipeline(args: argparse.Namespace) -> int:
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    specialty = canonicalize_specialty(args.specialty)
    as_of_date = parse_as_of(getattr(args, "as_of", None))
    if getattr(args, "max_review_age_days", 730) < 1:
        raise ValueError("--max-review-age-days must be >= 1")
    cards: list[Card] = []

    if args.exam_cards:
        if not specialty:
            raise ValueError("--specialty is required with --exam-cards")
        cards.extend(build_exam_cards(Path(args.exam_cards), specialty))
    if args.case_library:
        cards.extend(build_case_cards(Path(args.case_library), specialty))
    if args.blueprint:
        if not specialty:
            raise ValueError("--specialty is required with --blueprint")
        cards.extend(build_blueprint_cards(Path(args.blueprint), specialty))

    if not cards:
        raise ValueError("No cards loaded. Supply --exam-cards, --case-library, or --blueprint.")

    missing_specialties = [c.card_id or "<no-id>" for c in cards if not c.specialty]
    if missing_specialties:
        raise ValueError(
            "Cards missing specialty: " + ", ".join(missing_specialties[:10])
            + ". Supply --specialty or specialty on each record."
        )

    report = evaluate_quality(
        cards,
        require_reviewed=getattr(args, "require_reviewed", False),
        max_review_age_days=getattr(args, "max_review_age_days", 730),
        as_of=as_of_date,
        schema_version=REPORT_SCHEMA_VERSION,
    )
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "quality_report.json"
    atomic_write_text(
        json.dumps({
            **report_to_dict(report),
            "near_duplicate_candidates": find_near_duplicate_pairs(cards),
            "quality_score": calculate_quality_score(cards, report),
            "clinical_review_notice": (
                "Automated checks assess structure and educational metadata only; "
                "they do not verify clinical truth, guideline currency, or patient safety."
            ),
        }, indent=2, ensure_ascii=False),
        report_path,
    )
    LOG.info(
        "Quality: %d/%d passed; %d errors; %d warnings",
        report.cards_passed, report.cards_evaluated,
        report.error_count, report.warning_count,
    )

    if report.error_count and not args.force:
        LOG.error("Export aborted due to quality errors. Use --force to override.")
        for item in report.error_examples[:20]:
            LOG.error("%s [%s] %s", item["card_id"], item["code"], item["detail"])
        return 1

    if args.fail_on_warn and report.warning_count and not args.force:
        LOG.error("Export aborted because warnings exist (--fail-on-warn).")
        return 1

    write_tsv(cards, output_dir / "deck.tsv")
    deck_name = args.deck_name or (f"Usiphus::{specialty.title()}" if specialty else "Usiphus Deck")

    if args.apkg:
        export_apkg(cards, output_dir / "deck.apkg", deck_name)
    if args.anki_connect:
        push_to_ankiconnect(cards, deck_name, args.anki_url)
    if args.study_guide:
        export_study_guide(cards, output_dir / "study_guide.md", specialty, deck_name)

    atomic_write_text(
        json.dumps({
            "usiphus_version": VERSION,
            "created_unix": int(time.time()),
            "deck_name": deck_name,
            "specialty": specialty,
            "card_count": len(cards),
            "card_ids": [c.card_id for c in cards],
        }, indent=2, ensure_ascii=False),
        output_dir / "build_manifest.json",
    )
    return 0


# ---------------------------------------------------------------------------
# Self-tests
# ---------------------------------------------------------------------------

def run_self_test() -> int:
    import unittest

    class UsiphusTests(unittest.TestCase):
        def test_normalizer_idempotent(self):
            value = "[Cornea] Keratoconus — what is it"
            once = canonicalize_prompt(value)
            self.assertEqual(once, "[Cornea] Keratoconus — what is it?")
            self.assertEqual(canonicalize_prompt(once), once)

        def test_hyphen_preserved(self):
            self.assertEqual(canonicalize_prompt("A-B test"), "A-B test")

        def test_tag_deduplication(self):
            self.assertEqual(canonicalize_tags(["Case", "case", " OSCE "]), ("Case", "OSCE"))

        def test_duplicate_prompt_and_ids(self):
            cards = [
                Card("A", "ophthalmology", "What is cataract?", "Lens opacity causing visual impairment."),
                Card("a", "ophthalmology", " what is cataract? ", "Another valid answer."),
            ]
            report = evaluate_quality(cards)
            codes = {f.code for r in report.results for f in r.findings}
            self.assertIn(FindingCode.CARD_ID_DUPLICATE, codes)
            self.assertIn(FindingCode.PROMPT_DUPLICATE, codes)

        def test_mcq_validation(self):
            card = Card(
                "mcq1", "surgery", "Which is correct?", "The femoral artery is commonly affected.",
                card_type="mcq", options=("Femoral", "Popliteal"), correct_option="Femoral",
            )
            self.assertTrue(evaluate_quality([card]).results[0].passed)

        def test_mcq_missing_correct_answer(self):
            card = Card(
                "mcq1", "surgery", "Which is correct?", "A sufficiently long answer here.",
                card_type="mcq", options=("A", "B"),
            )
            codes = {f.code for f in evaluate_quality([card]).results[0].findings}
            self.assertIn(FindingCode.MCQ_CORRECT_OPTION_MISSING, codes)

        def test_stable_ids(self):
            self.assertEqual(stable_blueprint_id("Cornea", "Keratoconus"),
                             stable_blueprint_id("Cornea", "Keratoconus"))
            self.assertEqual(anki_guid("card-1"), anki_guid("CARD-1"))

        def test_warning_does_not_count_as_error(self):
            card = Card("x", "medicine", "What is X?", "A sufficiently long answer for this card.")
            report = evaluate_quality([card])
            self.assertGreater(report.warning_count, 0)
            self.assertEqual(report.error_count, 0)
            self.assertTrue(report.results[0].passed)

        def test_atomic_writes(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                path = Path(directory) / "sample.txt"
                atomic_write_text("hello", path)
                self.assertEqual(path.read_text(encoding="utf-8"), "hello")

        def test_json_loading_and_blueprint(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                path = Path(directory) / "blueprint.json"
                path.write_text(json.dumps([{
                    "topic": "Cornea", "name": "Keratoconus",
                    "definition": "Progressive corneal thinning and ectasia.",
                }]), encoding="utf-8")
                cards = build_blueprint_cards(path, "ophthalmology")
                self.assertEqual(len(cards), 1)
                self.assertTrue(cards[0].card_id.startswith("bp-"))
                self.assertTrue(cards[0].prompt.endswith("?"))

        def test_lexical_near_duplicate_candidates(self):
            cards = [
                Card("a", "ophthalmology", "What is the common cause of cataract?", "A sufficiently detailed answer."),
                Card("b", "ophthalmology", "What is a common cause of cataract?", "Another sufficiently detailed answer."),
            ]
            self.assertTrue(find_near_duplicate_pairs(cards, threshold=0.5))

        def test_quality_score_is_not_accuracy_claim(self):
            card = Card("x", "medicine", "What is X?", "A sufficiently long answer for this card.")
            report = evaluate_quality([card])
            score = calculate_quality_score([card], report)
            self.assertIn("metadata-completeness", score["label"])
            self.assertIn("not a clinical accuracy", score["warning"])

        def test_reviewed_metadata(self):
            card = Card(
                "x", "medicine", "What is X?", "A sufficiently long answer for this card.",
                references=("Guideline reference",), source="faculty-reviewed source",
                evidence_status="reviewed", reviewed_on="2026-01-01", reviewer="reviewer-1",
            )
            report = evaluate_quality([card], max_review_age_days=3650)
            self.assertFalse(any(f.code == FindingCode.EVIDENCE_NOT_REVIEWED
                                 for f in report.results[0].findings))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UsiphusTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        return run_pipeline(args)
    except (ValueError, FileNotFoundError, NotADirectoryError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    raise SystemExit(main())
