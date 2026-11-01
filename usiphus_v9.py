
#!/usr/bin/env python3
"""Usiphus V9 — clinical evidence governance and semantic exam intelligence engine.

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
* Evidence registry with claim-to-reference mapping and provenance.
* Draft → in_review → approved → published governance workflow.
* Guideline freshness checks, contradiction flags, and audit-trail validation.
* Optional release gate blocking cards with unapproved or stale evidence.
* Semantic near-duplicate candidates, answer-content diagnostics, and human review queue.

Important clinical limitation
------------------------------
This program validates structure and educational presentation heuristically.
It DOES NOT establish medical factual accuracy, guideline currency, or clinical
safety. Medical claims must be reviewed against current authoritative sources
by a qualified subject-matter reviewer.

Example
-------
python usiphus_v8.py --specialty ophthalmology --blueprint blueprint.json --output ./build
python usiphus_v8.py --exam-cards cards.json --specialty surgery --self-test
python usiphus_v8.py --exam-cards cards.json --specialty ophthalmology --apkg --study-guide
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

VERSION = "9.0.0"
REPORT_SCHEMA_VERSION = 6
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
    EVIDENCE_TRANSITIONS,
    EVIDENCE_WORKFLOW,
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
    analyze_evidence_registry,
    build_blueprint_cards,
    build_case_cards,
    build_exam_cards,
    calculate_quality_score,
    coerce_record,
    evaluate_quality,
    export_study_guide,
    load_evidence_registry,
    load_json_cards,
    report_to_dict,
    validate_workflow_audit,
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









def _content_tokens(text: str) -> set[str]:
    """Content-word set used by the lexical semantic fallback."""
    return _tokens(text)


def semantic_similarity_matrix(texts: Sequence[str], backend: str = "auto", model_name: str = "all-MiniLM-L6-v2") -> tuple[list[list[float]], str]:
    """Build similarity matrix. Optional backends are local and never send data remotely.

    backend='sentence-transformers' requires the optional package and a locally
    available/downloadable model. backend='tfidf' requires scikit-learn.
    backend='auto' prefers sentence-transformers, then TF-IDF, then lexical Jaccard.
    """
    normalized = [canonicalize_text(t) for t in texts]
    n = len(normalized)
    matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    if n < 2:
        return matrix, "none"

    if backend in {"auto", "sentence-transformers"}:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            model = SentenceTransformer(model_name)
            vectors = model.encode(normalized, normalize_embeddings=True, show_progress_bar=False)
            for i in range(n):
                for j in range(i + 1, n):
                    score = max(0.0, min(1.0, float(sum(a * b for a, b in zip(vectors[i], vectors[j])))))
                    matrix[i][j] = matrix[j][i] = score
            return matrix, f"sentence-transformers:{model_name}"
        except Exception as exc:
            if backend == "sentence-transformers":
                raise RuntimeError(f"Sentence-transformers backend unavailable: {exc}") from exc
            LOG.debug("Sentence-transformers unavailable; falling back: %s", exc)

    if backend in {"auto", "tfidf"}:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
            from sklearn.metrics.pairwise import cosine_similarity  # type: ignore
            vectors = TfidfVectorizer(ngram_range=(1, 2), stop_words="english").fit_transform(normalized)
            dense = cosine_similarity(vectors)
            for i in range(n):
                for j in range(i + 1, n):
                    matrix[i][j] = matrix[j][i] = float(dense[i, j])
            return matrix, "tfidf"
        except Exception as exc:
            if backend == "tfidf":
                raise RuntimeError(f"TF-IDF backend unavailable: {exc}") from exc
            LOG.debug("TF-IDF unavailable; using lexical fallback: %s", exc)

    if backend not in {"auto", "lexical"}:
        raise ValueError("--semantic-backend must be auto, sentence-transformers, tfidf, or lexical")
    token_sets = [_content_tokens(t) for t in normalized]
    for i in range(n):
        for j in range(i + 1, n):
            union = token_sets[i] | token_sets[j]
            score = len(token_sets[i] & token_sets[j]) / len(union) if union else 0.0
            matrix[i][j] = matrix[j][i] = score
    return matrix, "lexical-jaccard-fallback"


def find_semantic_duplicate_pairs(
    cards: Sequence[Card], threshold: float = 0.82, backend: str = "auto",
    model_name: str = "all-MiniLM-L6-v2",
) -> dict[str, Any]:
    """Return candidate pairs for human review; never merge or delete cards."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("semantic threshold must be between 0 and 1")
    matrix, actual_backend = semantic_similarity_matrix([c.prompt for c in cards], backend, model_name)
    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(cards):
        for j in range(i + 1, len(cards)):
            right = cards[j]
            score = matrix[i][j]
            if score >= threshold:
                pairs.append({
                    "card_id_a": left.card_id, "card_id_b": right.card_id,
                    "specialty_a": left.specialty, "specialty_b": right.specialty,
                    "similarity": round(score, 4), "backend": actual_backend,
                    "review_required": True,
                })
    pairs.sort(key=lambda x: x["similarity"], reverse=True)
    return {"backend": actual_backend, "threshold": threshold, "candidate_count": len(pairs), "pairs": pairs}




# ---------------------------------------------------------------------------
# Clinical evidence governance (V8)
# ---------------------------------------------------------------------------









def analyze_card_semantics(card: Card) -> list[dict[str, Any]]:
    """Explainable, non-clinical diagnostics for a human editor."""
    findings: list[dict[str, Any]] = []
    prompt_terms = _content_tokens(card.prompt)
    answer_terms = _content_tokens(card.answer)
    if len(prompt_terms) >= 3:
        overlap = len(prompt_terms & answer_terms) / len(prompt_terms)
        if overlap < 0.15:
            findings.append({
                "code": FindingCode.PROMPT_ANSWER_LOW_OVERLAP.value,
                "severity": "warn", "detail": f"Only {overlap:.0%} of prompt content terms appear in the answer; verify relevance manually.",
            })
    if len(card.answer.split()) < 8:
        findings.append({
            "code": FindingCode.ANSWER_REVIEW_SUGGESTION.value,
            "severity": "warn", "detail": "Answer is very short; consider whether explanation, rationale, or key qualifiers are needed.",
        })
    if card.card_type in {"clinical_case", "osce", "viva"} and not card.learning_objective:
        findings.append({
            "code": FindingCode.ANSWER_REVIEW_SUGGESTION.value,
            "severity": "warn", "detail": "Add an explicit learning objective to improve curriculum traceability.",
        })
    return findings


def build_semantic_review_queue(cards: Sequence[Card], duplicate_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for pair in duplicate_report.get("pairs", []):
        queue.append({"type": "possible_duplicate", "priority": "medium", **dict(pair)})
    for card in cards:
        for finding in analyze_card_semantics(card):
            queue.append({"type": "content_review", "priority": "low", "card_id": card.card_id, **finding})
    return queue








# ---------------------------------------------------------------------------
# Atomic output helpers
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------



def anki_exam_templates() -> tuple[list[dict[str, str]], str]:
    shared_back = (
        '<hr id="answer"><section class="answer"><b>Answer</b><br>{{Answer}}</section>'
        '{{#CorrectOption}}<section><b>Correct option:</b> {{CorrectOption}}</section>{{/CorrectOption}}'
        '{{#LearningObjective}}<section><b>Learning objective:</b> {{LearningObjective}}</section>{{/LearningObjective}}'
        '{{#Rubric}}<section><b>Marking checklist</b><ul>{{Rubric}}</ul></section>{{/Rubric}}'
        '{{#EvidenceExcerpt}}<blockquote class="source-evidence"><b>Source excerpt (unverified)</b><br>{{EvidenceExcerpt}}</blockquote>{{/EvidenceExcerpt}}'
        '{{#References}}<small class="references">{{References}}</small>{{/References}}'
        '<small class="evidence">Evidence status: {{EvidenceStatus}}</small>'
    )
    layouts = [
        ("BasicLayout", "Rapid recall", "basic", "{{Prompt}}"),
        ("MCQLayout", "Single best answer", "mcq", "{{Prompt}}{{#Options}}<hr><ol>{{Options}}</ol>{{/Options}}"),
        ("ClinicalCaseLayout", "Clinical case", "case", "<b>Clinical case</b><div>{{Prompt}}</div>"),
        ("VivaLayout", "Viva", "viva", "<b>Oral examination</b><div>{{Prompt}}</div>"),
        ("OSCELayout", "OSCE station", "osce", "<b>OSCE station</b><div>{{Prompt}}</div>"),
    ]
    templates = []
    for field_name, template_name, css_class, question in layouts:
        templates.append({
            "name": template_name,
            "qfmt": (
                f'{{{{#{field_name}}}}}<main class="usiphus {css_class}">'
                f'<header>{template_name}</header>{question}</main>{{{{/{field_name}}}}}'
            ),
            "afmt": (
                f'{{{{#{field_name}}}}}{{{{FrontSide}}}}{shared_back}'
                f'<div class="metadata">{{{{Tags}}}} · {{{{CardID}}}}</div>{{{{/{field_name}}}}}'
            ),
        })
    style = (
        ".card{font-family:Arial,Helvetica,sans-serif;font-size:20px;text-align:left;color:#17212b;"
        "background:#f1f5f9;padding:1rem;line-height:1.6}"
        ".usiphus{box-sizing:border-box;max-width:48rem;margin:0 auto;padding:clamp(1rem,4vw,2rem);"
        "border:1px solid #cbd5e1;border-radius:16px;background:#fff;line-height:1.6;"
        "box-shadow:0 8px 24px rgba(15,23,42,.08);overflow-wrap:anywhere}"
        ".usiphus header{font-size:.72em;font-weight:700;text-transform:uppercase;"
        "letter-spacing:.1em;color:#075985;margin-bottom:1rem;padding-bottom:.65rem;"
        "border-bottom:1px solid #e2e8f0}"
        ".mcq ol,.osce ul{padding-left:1.5rem}.case{border-left:5px solid #b45309}"
        ".viva{border-left:5px solid #6d28d9}.osce{border-left:5px solid #047857}"
        ".answer{margin-top:1rem}.references,.evidence,.metadata{display:block;color:#475569;"
        "font-size:.68em;margin-top:1rem;overflow-wrap:anywhere}.source-evidence{margin:1rem 0;"
        "padding:.85rem 1rem;border-left:4px solid #0891b2;border-radius:0 8px 8px 0;"
        "background:#ecfeff;color:#164e63;font-size:.82em}"
        "@media(max-width:600px){.card{font-size:18px;padding:.5rem}.usiphus{border-radius:10px}}"
        "@media(prefers-color-scheme:dark){.card{color:#e2e8f0;background:#0f172a}"
        ".usiphus{color:#e2e8f0;background:#1e293b;border-color:#475569;box-shadow:none}"
        ".usiphus header{color:#7dd3fc;border-color:#475569}.references,.evidence,.metadata{color:#cbd5e1}"
        ".source-evidence{color:#cffafe;background:#164e63;border-color:#22d3ee}}"
    )
    return templates, style


def export_apkg(cards: Sequence[Card], destination: Path, deck_name: str) -> None:
    genanki = _load_genanki()
    model_id = anki_numeric_id("usiphus-model-v9", "medical-card")
    deck_id = anki_numeric_id("usiphus-deck-v9", deck_name)
    templates, style = anki_exam_templates()

    model = genanki.Model(
        model_id,
        "Usiphus V9 Medical Model",
        fields=[
            {"name": "Prompt"}, {"name": "Answer"}, {"name": "Options"},
            {"name": "CorrectOption"}, {"name": "LearningObjective"},
            {"name": "Tags"}, {"name": "CardID"}, {"name": "References"},
            {"name": "Rubric"}, {"name": "EvidenceStatus"},
            {"name": "BasicLayout"}, {"name": "MCQLayout"},
            {"name": "ClinicalCaseLayout"}, {"name": "VivaLayout"},
            {"name": "OSCELayout"}, {"name": "EvidenceExcerpt"},
        ],
        templates=templates,
        css=style,
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
            "1" if card.card_type == "basic" else "",
            "1" if card.card_type == "mcq" else "",
            "1" if card.card_type == "clinical_case" else "",
            "1" if card.card_type == "viva" else "",
            "1" if card.card_type == "osce" else "",
            html.escape(card.evidence_excerpt),
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
    parser = argparse.ArgumentParser(description="Usiphus V8 clinical evidence governance engine")
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
    parser.add_argument("--evidence-registry", help="JSON evidence registry mapping claims to references and workflow status")
    parser.add_argument("--require-approved-evidence", action="store_true", help="Block release unless every card has current approved/published claim evidence")
    parser.add_argument("--semantic-backend", choices=("auto", "sentence-transformers", "tfidf", "lexical"), default="auto", help="Local semantic similarity backend")
    parser.add_argument("--semantic-model", default="all-MiniLM-L6-v2", help="Sentence-transformers model name")
    parser.add_argument("--semantic-threshold", type=float, default=0.82, help="Semantic duplicate candidate threshold")
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

    semantic_duplicates = find_semantic_duplicate_pairs(
        cards, threshold=getattr(args, "semantic_threshold", 0.82),
        backend=getattr(args, "semantic_backend", "auto"),
        model_name=getattr(args, "semantic_model", "all-MiniLM-L6-v2"),
    )
    semantic_review_queue = build_semantic_review_queue(cards, semantic_duplicates)
    evidence_report = None
    if getattr(args, "evidence_registry", None):
        evidence_records = load_evidence_registry(Path(args.evidence_registry))
        evidence_report = analyze_evidence_registry(
            cards, evidence_records,
            max_age_days=getattr(args, "max_review_age_days", 730),
            require_approved=getattr(args, "require_approved_evidence", False),
            as_of=as_of_date,
        )
    elif getattr(args, "require_approved_evidence", False):
        evidence_report = analyze_evidence_registry(
            cards, [], max_age_days=getattr(args, "max_review_age_days", 730), require_approved=True,
            as_of=as_of_date,
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
            "evidence_governance": evidence_report,
            "semantic_analysis": semantic_duplicates,
            "semantic_review_queue": semantic_review_queue,
            "clinical_review_notice": (
                "Automated checks assess structure and educational metadata only; "
                "they do not verify clinical truth, guideline currency, or patient safety."
            ),
        }, indent=2, ensure_ascii=False),
        report_path,
    )
    if evidence_report is not None:
        atomic_write_text(json.dumps(evidence_report, indent=2, ensure_ascii=False), output_dir / "evidence_governance_report.json")
    atomic_write_text(json.dumps(semantic_review_queue, indent=2, ensure_ascii=False), output_dir / "semantic_review_queue.json")

    LOG.info(
        "Quality: %d/%d passed; %d errors; %d warnings",
        report.cards_passed, report.cards_evaluated,
        report.error_count, report.warning_count,
    )

    if evidence_report and not evidence_report["release_ready"] and getattr(args, "require_approved_evidence", False) and not args.force:
        LOG.error("Release blocked by evidence governance: %d evidence errors and %d contradiction candidates.",
                  evidence_report["error_count"], len(evidence_report["contradiction_candidates"]))
        return 1

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

    class EvidenceGovernanceTests(unittest.TestCase):
        def test_approved_claim_requires_reference_and_reviewer(self):
            card = Card("c1", "medicine", "What is X?", "A sufficiently long answer.")
            result = analyze_evidence_registry([card], [{"claim_id":"cl1", "card_id":"c1", "claim":"X is present", "reference":"Guideline", "source_type":"guideline", "status":"approved", "reviewed_on":"2026-01-01", "reviewer":"reviewer"}], max_age_days=5000)
            self.assertEqual(result["error_count"], 0)

        def test_release_gate_blocks_unapproved_claim(self):
            card = Card("c1", "medicine", "What is X?", "A sufficiently long answer.")
            result = analyze_evidence_registry([card], [{"claim_id":"cl1", "card_id":"c1", "claim":"X is present", "reference":"Guideline", "source_type":"guideline", "status":"draft"}], require_approved=True)
            self.assertFalse(result["release_ready"])
            self.assertGreater(result["error_count"], 0)

        def test_invalid_audit_transition_is_flagged(self):
            issues = validate_workflow_audit({"status":"published", "audit_trail":[{"to":"published", "actor":"x", "date":"2026-01-01"}]})
            self.assertTrue(any("transition" in x for x in issues))

        def test_polarity_conflict_is_review_candidate(self):
            card = Card("c1", "medicine", "What is X?", "A sufficiently long answer.")
            records = [
                {"claim_id":"a", "card_id":"c1", "claim":"Drug increases risk", "reference":"R1", "source_type":"study"},
                {"claim_id":"b", "card_id":"c1", "claim":"Drug does not increases risk", "reference":"R2", "source_type":"study"},
            ]
            result = analyze_evidence_registry([card], records)
            self.assertTrue(result["contradiction_candidates"])

    class SemanticTests(unittest.TestCase):
        def test_semantic_matrix_is_symmetric(self):
            matrix, backend = semantic_similarity_matrix(["cataract surgery indications", "indications for cataract surgery"], backend="lexical")
            self.assertEqual(len(matrix), 2)
            self.assertAlmostEqual(matrix[0][1], matrix[1][0])

        def test_semantic_duplicate_review_only(self):
            cards = [Card("a", "ophthalmology", "Cataract surgery indications?", "A sufficiently detailed answer."), Card("b", "ophthalmology", "Indications for cataract surgery?", "Another sufficiently detailed answer.")]
            result = find_semantic_duplicate_pairs(cards, threshold=0.3, backend="lexical")
            self.assertGreaterEqual(result["candidate_count"], 1)

        def test_semantic_analysis_never_rewrites_answer(self):
            card = Card("x", "medicine", "What is X?", "A detailed answer that must remain unchanged.")
            before = card.answer
            analyze_card_semantics(card)
            self.assertEqual(card.answer, before)

    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(UsiphusTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceGovernanceTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(SemanticTests),
    ])
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
