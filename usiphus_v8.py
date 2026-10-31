#!/usr/bin/env python3
"""Usiphus V8 — clinical evidence and knowledge governance engine.

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

VERSION = "8.0.0"
REPORT_SCHEMA_VERSION = 5
TSV_HEADER = [
    "card_id", "specialty", "card_type", "difficulty", "prompt", "answer",
    "options", "correct_option", "learning_objective", "tags", "source",
    "references", "cognitive_level", "exam_domain", "rubric",
    "evidence_status", "reviewed_on", "reviewer",
]
PROMPT_MAX_CHARS = 500
ANSWER_MIN_CHARS = 15
ANSWER_MAX_CHARS = 5000
ANKI_CONNECT_TIMEOUT_SECONDS = 10
MAX_INPUT_FILE_BYTES = 50 * 1024 * 1024
VALID_CARD_TYPES = {"basic", "mcq", "osce", "viva", "clinical_case"}
VALID_DIFFICULTIES = {"beginner", "intermediate", "advanced"}
VALID_COGNITIVE_LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}
VALID_EVIDENCE_STATUSES = {"unreviewed", "reviewed", "needs_update", "not_applicable"}
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "what", "is", "are", "the", "a", "an", "of", "in", "to", "for", "and",
    "or", "with", "on", "by", "from", "which", "how", "does", "do", "describe",
    "explain", "define", "discuss", "list", "name", "identify", "most", "common",
}
LOG = logging.getLogger("usiphus")


class FindingCode(str, Enum):
    INPUT_RECORD_INVALID = "input-record-invalid"
    CARD_ID_MISSING = "card-id-missing"
    CARD_ID_DUPLICATE = "card-id-duplicate"
    SPECIALTY_MISSING = "specialty-missing"
    CARD_TYPE_INVALID = "card-type-invalid"
    DIFFICULTY_INVALID = "difficulty-invalid"
    PROMPT_EMPTY = "prompt-empty"
    PROMPT_TOO_LONG = "prompt-too-long"
    PROMPT_NOT_QUESTION = "prompt-not-question"
    PROMPT_DUPLICATE = "prompt-duplicate"
    ANSWER_EMPTY = "answer-empty"
    ANSWER_TOO_SHORT = "answer-too-short"
    ANSWER_TOO_LONG = "answer-too-long"
    MCQ_OPTIONS_MISSING = "mcq-options-missing"
    MCQ_OPTIONS_TOO_FEW = "mcq-options-too-few"
    MCQ_OPTIONS_DUPLICATE = "mcq-options-duplicate"
    MCQ_CORRECT_OPTION_MISSING = "mcq-correct-option-missing"
    MCQ_CORRECT_OPTION_INVALID = "mcq-correct-option-invalid"
    OSCE_TASK_MISSING = "osce-task-missing"
    SOURCE_MISSING = "source-missing"
    REFERENCE_MISSING = "reference-missing"
    PROMPT_WHITESPACE = "prompt-whitespace"
    COGNITIVE_LEVEL_INVALID = "cognitive-level-invalid"
    EXAM_DOMAIN_MISSING = "exam-domain-missing"
    OBJECTIVE_MISSING = "objective-missing"
    RUBRIC_MISSING = "rubric-missing"
    EVIDENCE_STATUS_INVALID = "evidence-status-invalid"
    EVIDENCE_NOT_REVIEWED = "evidence-not-reviewed"
    REVIEW_DATE_INVALID = "review-date-invalid"
    REVIEW_STALE = "review-stale"
    SEMANTIC_DUPLICATE_CANDIDATE = "semantic-duplicate-candidate"
    ANSWER_NO_STRUCTURE = "answer-no-structure"
    CLINICAL_CASE_NO_CONTEXT = "clinical-case-no-context"
    MCQ_EXPLANATION_MISSING = "mcq-explanation-missing"


@dataclass(frozen=True)
class Finding:
    code: FindingCode
    detail: str
    severity: Literal["error", "warn"] = "error"


@dataclass(frozen=True)
class Card:
    card_id: str
    specialty: str
    prompt_raw: str
    answer_raw: str
    tags: tuple[str, ...] = ()
    card_type: str = "basic"
    difficulty: str = "intermediate"
    options: tuple[str, ...] = ()
    correct_option: str = ""
    learning_objective: str = ""
    source: str = ""
    references: tuple[str, ...] = ()
    cognitive_level: str = "understand"
    exam_domain: str = ""
    rubric: tuple[str, ...] = ()
    evidence_status: str = "unreviewed"
    reviewed_on: str = ""
    reviewer: str = ""

    @property
    def prompt(self) -> str:
        return canonicalize_prompt(self.prompt_raw)

    @property
    def answer(self) -> str:
        return canonicalize_text(self.answer_raw)

    @property
    def card_id_key(self) -> str:
        return canonicalize_card_id(self.card_id).casefold()

    def to_tsv_row(self) -> list[str]:
        return [
            canonicalize_card_id(self.card_id),
            canonicalize_specialty(self.specialty),
            self.card_type,
            self.difficulty,
            self.prompt,
            self.answer,
            json.dumps(list(self.options), ensure_ascii=False),
            self.correct_option,
            canonicalize_text(self.learning_objective),
            ";".join(self.tags),
            canonicalize_text(self.source),
            json.dumps(list(self.references), ensure_ascii=False),
            self.cognitive_level,
            self.exam_domain,
            json.dumps(list(self.rubric), ensure_ascii=False),
            self.evidence_status,
            self.reviewed_on,
            self.reviewer,
        ]


@dataclass(frozen=True)
class CardReport:
    card_id: str
    normalized_prompt: str
    passed: bool
    findings: list[Finding]


@dataclass
class QualityReport:
    schema_version: int = REPORT_SCHEMA_VERSION
    cards_evaluated: int = 0
    cards_passed: int = 0
    cards_flagged: int = 0
    error_count: int = 0
    warning_count: int = 0
    results: list[CardReport] = field(default_factory=list)

    @property
    def error_examples(self) -> list[dict[str, str]]:
        examples: list[dict[str, str]] = []
        for result in self.results:
            for finding in result.findings:
                if finding.severity == "error":
                    examples.append({
                        "card_id": result.card_id,
                        "code": finding.code.value,
                        "detail": finding.detail,
                    })
        return examples


# ---------------------------------------------------------------------------
# Normalization and stable identifiers
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_Q_RE = re.compile(r"\?+$")
_NON_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def canonicalize_text(value: Any) -> str:
    if value is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(value).strip())


def canonicalize_card_id(raw: Any) -> str:
    return canonicalize_text(raw)


def canonicalize_specialty(value: Any) -> str:
    return canonicalize_text(value).casefold()


def canonicalize_tags(tags: Any) -> tuple[str, ...]:
    if tags is None:
        return ()
    if isinstance(tags, str):
        items: Sequence[Any] = [tags]
    elif isinstance(tags, (list, tuple, set)):
        items = list(tags)
    else:
        raise ValueError("tags must be a string or a list of strings")
    output: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if raw is None:
            continue
        tag = canonicalize_text(raw)
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            output.append(tag)
    return tuple(output)


def canonicalize_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError(f"{field_name} must be a string or list of strings")
    result = tuple(canonicalize_text(x) for x in values if canonicalize_text(x))
    return result


def canonicalize_prompt(prompt: Any) -> str:
    """Collapse whitespace and normalize blueprint 'what is it' prompts.

    Does not force arbitrary prompts to become questions. Validation reports
    non-question prompts so headings and station instructions remain possible
    when appropriate.
    """
    text = canonicalize_text(prompt)
    if not text:
        return ""
    text = re.sub(r"\s+--\s+", " — ", text)
    text = re.sub(r"\s+-\s+", " — ", text)
    if re.search(r"\bwhat is it\??$", text, flags=re.IGNORECASE):
        text = re.sub(r"\bwhat is it\??$", "what is it?", text, flags=re.IGNORECASE)
        text = _TRAILING_Q_RE.sub("?", text)
    return text


def stable_digest(*parts: str, length: int = 20) -> str:
    payload = "\x1f".join(canonicalize_text(p).casefold() for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def stable_blueprint_id(topic: Any, name: Any) -> str:
    return f"bp-{stable_digest(str(topic), str(name), length=20)}"


def anki_numeric_id(namespace: str, name: str) -> int:
    """Generate a deterministic positive 31-bit Anki model/deck ID."""
    raw = int(stable_digest(namespace, name, length=8), 16)
    # Keep in a positive, non-zero 31-bit range.
    return max(1, raw & 0x7FFFFFFF)


def anki_guid(card_id: str) -> str:
    """Anki GUIDs are stable across repeat exports for the same card ID."""
    return hashlib.sha256(canonicalize_card_id(card_id).casefold().encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Input parsing and schema validation
# ---------------------------------------------------------------------------

def load_json_cards(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Input path is not a file: {path}")
    if path.stat().st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"Input file exceeds {MAX_INPUT_FILE_BYTES} bytes: {path}")
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and isinstance(data.get("cards"), list):
        records = data["cards"]
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError(
            f"Invalid JSON in {path}: expected a list or {{'cards': [...]}}"
        )
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"{path}: record {index} must be a JSON object")
    return records


def coerce_record(
    record: Mapping[str, Any],
    *,
    default_specialty: str = "",
    default_tags: tuple[str, ...] = (),
    source_label: str = "",
    index: int = 0,
) -> Card:
    raw_id = record.get("card_id") or record.get("id") or ""
    specialty = record.get("specialty") or default_specialty
    prompt = record.get("prompt") or record.get("question") or record.get("task") or ""
    answer = record.get("answer") or record.get("explanation") or record.get("checklist") or ""

    card_type = canonicalize_text(record.get("card_type") or record.get("type") or "basic").casefold()
    difficulty = canonicalize_text(record.get("difficulty") or "intermediate").casefold()

    options = canonicalize_string_list(record.get("options"), "options")
    correct = canonicalize_text(record.get("correct_option") or record.get("correct_answer"))
    if correct and options:
        # Normalize a correct-option letter or exact option text.
        match = re.fullmatch(r"[A-Za-z]", correct)
        if match:
            position = ord(correct.upper()) - ord("A")
            if 0 <= position < len(options):
                correct = options[position]

    tags = canonicalize_tags(record.get("tags") if record.get("tags") is not None else default_tags)
    references = canonicalize_string_list(record.get("references") or record.get("sources"), "references")

    source = canonicalize_text(record.get("source") or source_label)
    if not source and source_label:
        source = source_label

    return Card(
        card_id=canonicalize_card_id(raw_id),
        specialty=canonicalize_specialty(specialty),
        prompt_raw=str(prompt or ""),
        answer_raw=str(answer or ""),
        tags=tags,
        card_type=card_type,
        difficulty=difficulty,
        options=options,
        correct_option=correct,
        learning_objective=canonicalize_text(record.get("learning_objective") or record.get("objective")),
        source=source,
        references=references,
        cognitive_level=canonicalize_text(record.get("cognitive_level") or "understand").casefold(),
        exam_domain=canonicalize_text(record.get("exam_domain") or record.get("exam_format") or ""),
        rubric=canonicalize_string_list(record.get("rubric") or record.get("checklist_items"), "rubric"),
        evidence_status=canonicalize_text(record.get("evidence_status") or "unreviewed").casefold(),
        reviewed_on=canonicalize_text(record.get("reviewed_on") or ""),
        reviewer=canonicalize_text(record.get("reviewer") or ""),
    )


def build_exam_cards(path: Path, specialty: str) -> list[Card]:
    return [
        coerce_record(r, default_specialty=specialty, source_label=f"exam_cards:{path.name}", index=i)
        for i, r in enumerate(load_json_cards(path))
    ]


def build_case_cards(path: Path, default_specialty: str = "") -> list[Card]:
    return [
        coerce_record(
            r, default_specialty=default_specialty, default_tags=("case",),
            source_label=f"case_library:{path.name}", index=i,
        )
        for i, r in enumerate(load_json_cards(path))
    ]


def build_blueprint_cards(path: Path, specialty: str) -> list[Card]:
    cards: list[Card] = []
    for index, record in enumerate(load_json_cards(path)):
        topic = canonicalize_text(record.get("topic") or record.get("category") or "General")
        name = canonicalize_text(record.get("name") or record.get("term") or record.get("prompt"))
        if not name:
            raise ValueError(f"{path}: blueprint record {index} has no name/term/prompt")
        prompt = f"[{topic}] {name} — what is it?"
        card_id = canonicalize_card_id(record.get("card_id")) or stable_blueprint_id(topic, name)
        answer = record.get("definition") or record.get("answer") or record.get("explanation") or ""
        cards.append(Card(
            card_id=card_id,
            specialty=canonicalize_specialty(specialty),
            prompt_raw=prompt,
            answer_raw=str(answer),
            tags=canonicalize_tags(record.get("tags") or ("blueprint", topic)),
            card_type="basic",
            difficulty=canonicalize_text(record.get("difficulty") or "intermediate").casefold(),
            learning_objective=canonicalize_text(record.get("learning_objective")),
            source=canonicalize_text(record.get("source") or f"blueprint:{path.name}"),
            references=canonicalize_string_list(record.get("references"), "references"),
            cognitive_level=canonicalize_text(record.get("cognitive_level") or "understand").casefold(),
            exam_domain=canonicalize_text(record.get("exam_domain") or "knowledge").casefold(),
            rubric=canonicalize_string_list(record.get("rubric"), "rubric"),
            evidence_status=canonicalize_text(record.get("evidence_status") or "unreviewed").casefold(),
            reviewed_on=canonicalize_text(record.get("reviewed_on") or ""),
            reviewer=canonicalize_text(record.get("reviewer") or ""),
        ))
    return cards


# ---------------------------------------------------------------------------
# Structural and educational-format quality checks
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    """Simple lexical tokens; deliberately not represented as true semantics."""
    return {
        token.casefold() for token in TOKEN_RE.findall(text)
        if token.casefold() not in STOP_WORDS and len(token) > 1
    }


def lexical_similarity(left: str, right: str) -> float:
    """Jaccard similarity of content words (heuristic duplicate screening only)."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_near_duplicate_pairs(cards: Sequence[Card], threshold: float = 0.78) -> list[dict[str, Any]]:
    """Return possible lexical duplicates for human review; never auto-delete."""
    candidates: list[dict[str, Any]] = []
    for i, left in enumerate(cards):
        for right in cards[i + 1:]:
            if left.specialty != right.specialty:
                continue
            score = lexical_similarity(left.prompt, right.prompt)
            if score >= threshold and left.prompt.casefold() != right.prompt.casefold():
                candidates.append({
                    "card_id_a": left.card_id,
                    "card_id_b": right.card_id,
                    "lexical_similarity": round(score, 3),
                    "review_required": True,
                })
    return sorted(candidates, key=lambda item: item["lexical_similarity"], reverse=True)


def _valid_iso_date(value: str) -> bool:
    if not value:
        return False
    try:
        from datetime import date
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def evaluate_quality(
    cards: Iterable[Card],
    *,
    require_reviewed: bool = False,
    max_review_age_days: int = 730,
    near_duplicate_threshold: float = 0.78,
) -> QualityReport:
    """Structural + educational metadata checks; does not verify medical truth."""
    from datetime import date, timedelta

    card_list = list(cards)
    prompt_counts = Counter(c.prompt.casefold() for c in card_list if c.prompt)
    id_counts = Counter(c.card_id_key for c in card_list if c.card_id_key)
    near_pairs = find_near_duplicate_pairs(card_list, near_duplicate_threshold)
    near_by_id: dict[str, list[dict[str, Any]]] = {}
    for pair in near_pairs:
        near_by_id.setdefault(pair["card_id_a"], []).append(pair)
        near_by_id.setdefault(pair["card_id_b"], []).append(pair)

    results: list[CardReport] = []
    total_errors = total_warnings = 0
    today = date.today()

    for card in card_list:
        findings: list[Finding] = []
        prompt, answer = card.prompt, card.answer

        def add(code: FindingCode, detail: str, severity: Literal["error", "warn"] = "error") -> None:
            findings.append(Finding(code, detail, severity))

        if not card.card_id:
            add(FindingCode.CARD_ID_MISSING, "card_id is empty")
        elif id_counts[card.card_id_key] > 1:
            add(FindingCode.CARD_ID_DUPLICATE, f"card_id '{card.card_id}' is duplicated")
        if not card.specialty:
            add(FindingCode.SPECIALTY_MISSING, "specialty is empty")
        if card.card_type not in VALID_CARD_TYPES:
            add(FindingCode.CARD_TYPE_INVALID, f"Unsupported card_type: {card.card_type!r}")
        if card.difficulty not in VALID_DIFFICULTIES:
            add(FindingCode.DIFFICULTY_INVALID, f"Unsupported difficulty: {card.difficulty!r}", "warn")
        if card.cognitive_level not in VALID_COGNITIVE_LEVELS:
            add(FindingCode.COGNITIVE_LEVEL_INVALID, f"Unsupported cognitive_level: {card.cognitive_level!r}", "warn")
        if not card.exam_domain:
            add(FindingCode.EXAM_DOMAIN_MISSING, "No exam domain supplied (e.g. anatomy, diagnosis, management)", "warn")
        if not card.learning_objective:
            add(FindingCode.OBJECTIVE_MISSING, "No explicit learning objective supplied", "warn")

        if not prompt:
            add(FindingCode.PROMPT_EMPTY, "Prompt is empty")
        else:
            if len(prompt) > PROMPT_MAX_CHARS:
                add(FindingCode.PROMPT_TOO_LONG, f"{len(prompt)} chars > {PROMPT_MAX_CHARS}")
            if prompt_counts[prompt.casefold()] > 1:
                add(FindingCode.PROMPT_DUPLICATE, "Exact prompt duplicate after normalization")
            if prompt != canonicalize_text(card.prompt_raw):
                add(FindingCode.PROMPT_WHITESPACE, "Prompt contains non-canonical whitespace", "warn")
            if not prompt.endswith(("?", ":")) and card.card_type in {"basic", "mcq", "viva"}:
                add(FindingCode.PROMPT_NOT_QUESTION, "Prompt does not end with '?' or ':'", "warn")

        if not answer:
            add(FindingCode.ANSWER_EMPTY, "Answer is empty")
        else:
            if len(answer) < ANSWER_MIN_CHARS:
                add(FindingCode.ANSWER_TOO_SHORT, f"{len(answer)} chars < {ANSWER_MIN_CHARS}")
            if len(answer) > ANSWER_MAX_CHARS:
                add(FindingCode.ANSWER_TOO_LONG, f"{len(answer)} chars > {ANSWER_MAX_CHARS}")
            if card.card_type in {"osce", "clinical_case", "viva"} and len(answer.split()) < 5:
                add(FindingCode.ANSWER_NO_STRUCTURE, "This exam format usually needs a structured answer", "warn")

        if card.card_type == "mcq":
            if not card.options:
                add(FindingCode.MCQ_OPTIONS_MISSING, "MCQ has no options")
            elif len(card.options) < 2:
                add(FindingCode.MCQ_OPTIONS_TOO_FEW, "MCQ requires at least two options")
            option_keys = [x.casefold() for x in card.options]
            if len(option_keys) != len(set(option_keys)):
                add(FindingCode.MCQ_OPTIONS_DUPLICATE, "MCQ contains duplicate options")
            if not card.correct_option:
                add(FindingCode.MCQ_CORRECT_OPTION_MISSING, "MCQ correct_option is missing")
            elif card.correct_option.casefold() not in set(option_keys):
                add(FindingCode.MCQ_CORRECT_OPTION_INVALID, "correct_option must match an option")
            if len(answer.split()) < 8:
                add(FindingCode.MCQ_EXPLANATION_MISSING, "Provide a rationale explaining why the correct answer is best", "warn")

        if card.card_type == "osce" and not card.rubric:
            add(FindingCode.RUBRIC_MISSING, "OSCE card should include a marking checklist/rubric", "warn")
        if card.card_type == "clinical_case" and len(prompt.split()) < 8:
            add(FindingCode.CLINICAL_CASE_NO_CONTEXT, "Clinical case prompt appears too brief; check history, examination and task context", "warn")

        if not card.source:
            add(FindingCode.SOURCE_MISSING, "No source provenance supplied", "warn")
        if not card.references:
            add(FindingCode.REFERENCE_MISSING, "No medical reference supplied; factual accuracy is unverified", "warn")

        if card.evidence_status not in VALID_EVIDENCE_STATUSES:
            add(FindingCode.EVIDENCE_STATUS_INVALID, f"Unsupported evidence_status: {card.evidence_status!r}")
        elif card.evidence_status != "reviewed":
            severity: Literal["error", "warn"] = "error" if require_reviewed else "warn"
            add(FindingCode.EVIDENCE_NOT_REVIEWED, f"Evidence status is '{card.evidence_status}'", severity)
        if card.reviewed_on:
            if not _valid_iso_date(card.reviewed_on):
                add(FindingCode.REVIEW_DATE_INVALID, "reviewed_on must use YYYY-MM-DD")
            elif card.evidence_status == "reviewed":
                reviewed_date = date.fromisoformat(card.reviewed_on)
                if reviewed_date < today - timedelta(days=max_review_age_days):
                    add(FindingCode.REVIEW_STALE, f"Evidence review is older than {max_review_age_days} days", "warn")
        elif card.evidence_status == "reviewed":
            add(FindingCode.REVIEW_DATE_INVALID, "Reviewed cards must include reviewed_on (YYYY-MM-DD)", "warn")

        if card.evidence_status == "reviewed" and not card.reviewer:
            add(FindingCode.EVIDENCE_NOT_REVIEWED, "Reviewed status requires a reviewer identifier", "warn")

        if near_by_id.get(card.card_id):
            best = max(p["lexical_similarity"] for p in near_by_id[card.card_id])
            add(FindingCode.SEMANTIC_DUPLICATE_CANDIDATE,
                f"Possible near-duplicate prompt (lexical similarity {best:.2f}); human review required", "warn")

        passed = not any(f.severity == "error" for f in findings)
        total_errors += sum(f.severity == "error" for f in findings)
        total_warnings += sum(f.severity == "warn" for f in findings)
        results.append(CardReport(card.card_id or "<no-id>", prompt, passed, findings))

    return QualityReport(
        cards_evaluated=len(results),
        cards_passed=sum(r.passed for r in results),
        cards_flagged=sum(not r.passed for r in results),
        error_count=total_errors,
        warning_count=total_warnings,
        results=results,
    )


def calculate_quality_score(cards: Sequence[Card], report: QualityReport) -> dict[str, Any]:
    """Transparent completeness score, not a medical-accuracy score."""
    if not cards:
        return {"score": 0, "label": "no-cards", "components": {}}
    total = len(cards)
    objective = sum(bool(c.learning_objective) for c in cards) / total
    refs = sum(bool(c.references) for c in cards) / total
    source = sum(bool(c.source) for c in cards) / total
    reviewed = sum(c.evidence_status == "reviewed" for c in cards) / total
    rubric_applicable = [c for c in cards if c.card_type == "osce"]
    rubric = (
        sum(bool(c.rubric) for c in rubric_applicable) / len(rubric_applicable)
        if rubric_applicable else 1.0
    )
    no_errors = max(0.0, 1.0 - report.error_count / max(1, total * 3))
    components = {
        "learning_objective_coverage": round(objective, 3),
        "reference_coverage": round(refs, 3),
        "source_provenance_coverage": round(source, 3),
        "reviewed_evidence_coverage": round(reviewed, 3),
        "osce_rubric_coverage": round(rubric, 3),
        "structural_integrity": round(no_errors, 3),
    }
    weights = {
        "learning_objective_coverage": 0.15,
        "reference_coverage": 0.20,
        "source_provenance_coverage": 0.10,
        "reviewed_evidence_coverage": 0.25,
        "osce_rubric_coverage": 0.10,
        "structural_integrity": 0.20,
    }
    score = round(100 * sum(components[k] * weights[k] for k in weights))
    return {
        "score": score,
        "label": "metadata-completeness-and-structure-only",
        "components": components,
        "warning": "This score is not a clinical accuracy or educational effectiveness score.",
    }


# ---------------------------------------------------------------------------
# Atomic output helpers
# ---------------------------------------------------------------------------

def atomic_write_text(text: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def write_tsv(cards: Sequence[Card], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            writer = csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(TSV_HEADER)
            writer.writerows(card.to_tsv_row() for card in cards)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    LOG.info("Wrote %d cards to %s", len(cards), destination)


def report_to_dict(report: QualityReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "summary": {
            "cards_evaluated": report.cards_evaluated,
            "cards_passed": report.cards_passed,
            "cards_flagged": report.cards_flagged,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
        },
        "error_examples": report.error_examples,
        "results": [
            {
                "card_id": r.card_id,
                "normalized_prompt": r.normalized_prompt,
                "passed": r.passed,
                "findings": [
                    {"code": f.code.value, "detail": f.detail, "severity": f.severity}
                    for f in r.findings
                ],
            }
            for r in report.results
        ],
    }


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def _load_genanki():
    try:
        import genanki  # type: ignore
        return genanki
    except ImportError as exc:
        raise RuntimeError("genanki is required for --apkg. Install: pip install genanki") from exc


def export_apkg(cards: Sequence[Card], destination: Path, deck_name: str) -> None:
    genanki = _load_genanki()
    model_id = anki_numeric_id("usiphus-model-v8", "medical-card")
    deck_id = anki_numeric_id("usiphus-deck-v8", deck_name)

    model = genanki.Model(
        model_id,
        "Usiphus V8 Medical Model",
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


def push_to_ankiconnect(cards: Sequence[Card], deck_name: str, url: str) -> None:
    notes = []
    for card in cards:
        notes.append({
            "deckName": deck_name,
            "modelName": "Basic",
            "fields": {"Front": card.prompt, "Back": card.answer},
            "tags": list(card.tags) + [f"usiphus_id_{stable_digest(card.card_id, length=16)}"],
            "options": {"allowDuplicate": False},
        })
    payload = json.dumps({
        "action": "addNotes", "version": 6, "params": {"notes": notes},
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=ANKI_CONNECT_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"AnkiConnect request failed: {exc}") from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AnkiConnect returned invalid JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise RuntimeError("AnkiConnect returned a non-object response")
    if body.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error: {body['error']}")
    results = body.get("result")
    if not isinstance(results, list):
        raise RuntimeError("AnkiConnect response is missing a result list")
    if len(results) != len(cards):
        raise RuntimeError(
            f"AnkiConnect returned {len(results)} results for {len(cards)} notes"
        )
    failures = [{"index": i, "error": item} for i, item in enumerate(results) if item is None]
    if failures:
        raise RuntimeError(f"AnkiConnect failed to add notes: {failures}")
    LOG.info("AnkiConnect accepted %d notes", len(cards))


def export_study_guide(cards: Sequence[Card], destination: Path, specialty: str, deck_name: str) -> None:
    lines = [f"# {deck_name} — {specialty.title() if specialty else 'General'} Study Guide", "", f"{len(cards)} cards", ""]
    for card in cards:
        lines.extend([
            f"## {card.prompt}",
            "",
            card.answer,
            "",
        ])
        if card.options:
            lines.append("**Options**")
            lines.extend(f"- {option}" for option in card.options)
            lines.append("")
        if card.correct_option:
            lines.extend([f"**Correct option:** {card.correct_option}", ""])
        if card.learning_objective:
            lines.extend([f"**Learning objective:** {card.learning_objective}", ""])
        if card.references:
            lines.extend(["**References**", *[f"- {ref}" for ref in card.references], ""])
        if card.rubric:
            lines.extend(["**Marking checklist**", *[f"- [ ] {item}" for item in card.rubric], ""])
        lines.extend([
            f"*Type: {card.card_type} · Difficulty: {card.difficulty} · Cognitive level: {card.cognitive_level} · Evidence: {card.evidence_status} · Tags: {', '.join(card.tags)}*",
            "",
        ])
    atomic_write_text("\n".join(lines), destination)
    LOG.info("Study guide written to %s", destination)


# ---------------------------------------------------------------------------
# Clinical evidence governance (V8)
# ---------------------------------------------------------------------------

EVIDENCE_WORKFLOW = ("draft", "in_review", "approved", "published")
EVIDENCE_TRANSITIONS = {
    "draft": {"in_review"},
    "in_review": {"draft", "approved"},
    "approved": {"in_review", "published"},
    "published": {"in_review"},
}


def load_evidence_registry(path: Path) -> list[dict[str, Any]]:
    """Load evidence claims. Accept either a list or {'claims': [...]} JSON."""
    if not path.exists():
        raise FileNotFoundError(f"Evidence registry not found: {path}")
    if path.stat().st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"Evidence registry exceeds {MAX_INPUT_FILE_BYTES} bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid evidence registry JSON: {exc}") from exc
    records = payload.get("claims", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
        raise ValueError("Evidence registry must be a list of claim objects or {'claims': [...]}.")
    return records


def validate_workflow_audit(record: Mapping[str, Any]) -> list[str]:
    """Check status and append-only transition history for one evidence claim."""
    problems: list[str] = []
    status = canonicalize_text(record.get("status") or "draft").casefold()
    if status not in EVIDENCE_WORKFLOW:
        problems.append(f"invalid workflow status: {status}")
    audit = record.get("audit_trail") or []
    if not isinstance(audit, list):
        return problems + ["audit_trail must be a list"]
    previous = "draft"
    for index, event in enumerate(audit):
        if not isinstance(event, dict):
            problems.append(f"audit event {index} must be an object")
            continue
        target = canonicalize_text(event.get("to") or "").casefold()
        actor = canonicalize_text(event.get("actor") or "")
        if target not in EVIDENCE_WORKFLOW or target not in EVIDENCE_TRANSITIONS.get(previous, set()):
            problems.append(f"invalid workflow transition at audit event {index}: {previous} -> {target}")
        if not actor:
            problems.append(f"audit event {index} is missing actor")
        if not _valid_iso_date(str(event.get("date") or "")):
            problems.append(f"audit event {index} has invalid/missing date")
        if target in EVIDENCE_WORKFLOW:
            previous = target
    if status in {"approved", "published"} and not audit:
        problems.append("approved/published evidence requires an audit_trail")
    if audit and previous != status:
        problems.append(f"audit trail ends at {previous}, but record status is {status}")
    return problems


def analyze_evidence_registry(
    cards: Sequence[Card], records: Sequence[Mapping[str, Any]], *,
    max_age_days: int = 730, require_approved: bool = False,
) -> dict[str, Any]:
    """Validate claim mapping, provenance, workflow, freshness and simple contradictions.

    This is governance metadata validation, not automated appraisal of source quality
    or clinical truth. Contradiction flags are review prompts, never adjudications.
    """
    from datetime import date, timedelta
    known_ids = {c.card_id for c in cards}
    seen_claim_ids: set[str] = set()
    by_card: dict[str, list[dict[str, Any]]] = {}
    findings: list[dict[str, Any]] = []
    claim_groups: dict[str, list[dict[str, Any]]] = {}
    today = date.today()
    for index, raw in enumerate(records):
        rec = dict(raw)
        cid = canonicalize_card_id(rec.get("claim_id"))
        card_id = canonicalize_card_id(rec.get("card_id"))
        claim = canonicalize_text(rec.get("claim"))
        reference = canonicalize_text(rec.get("reference"))
        status = canonicalize_text(rec.get("status") or "draft").casefold()
        issues: list[str] = []
        if not cid: issues.append("claim_id is required")
        elif cid in seen_claim_ids: issues.append("duplicate claim_id")
        seen_claim_ids.add(cid)
        if not card_id or card_id not in known_ids: issues.append("claim maps to an unknown card_id")
        if not claim: issues.append("claim text is required")
        if not reference: issues.append("reference is required")
        if not canonicalize_text(rec.get("source_type")): issues.append("source_type is required")
        if status not in EVIDENCE_WORKFLOW: issues.append(f"invalid status: {status}")
        for issue in validate_workflow_audit(rec): issues.append(issue)
        reviewed_on = str(rec.get("reviewed_on") or "")
        if status in {"approved", "published"}:
            if not reviewed_on or not _valid_iso_date(reviewed_on):
                issues.append("approved/published evidence requires reviewed_on YYYY-MM-DD")
            elif date.fromisoformat(reviewed_on) < today - timedelta(days=max_age_days):
                issues.append(f"evidence review is stale (>{max_age_days} days)")
            if not canonicalize_text(rec.get("reviewer")): issues.append("approved/published evidence requires reviewer")
        if status not in {"approved", "published"} and require_approved:
            issues.append(f"release blocked: status is {status}, not approved/published")
        item = {"claim_id": cid, "card_id": card_id, "claim": claim, "status": status,
                "reference": reference, "issues": issues, "registry_index": index}
        if card_id: by_card.setdefault(card_id, []).append(item)
        if claim:
            polarity_key = re.sub(r"\b(no|not|never|without|decrease|decreases|decreased|absence|does|do|did)\b", " ", claim.casefold())
            polarity_key = re.sub(r"[^a-z0-9 ]", "", polarity_key)
            polarity_key = re.sub(r"\s+", " ", polarity_key).strip()
            claim_groups.setdefault(polarity_key, []).append(item)
        if issues:
            findings.append({"claim_id": cid or f"row-{index+1}", "card_id": card_id,
                             "severity": "error" if require_approved else "warn", "issues": issues})
    # Conservative polarity-only contradiction heuristic. It does not infer clinical meaning.
    contradictions = []
    for normalized, items in claim_groups.items():
        if len(items) < 2: continue
        positive = [x for x in items if not re.search(r"\b(no|not|never|without|decrease|decreases|decreased|absence)\b", x["claim"].casefold())]
        negative = [x for x in items if re.search(r"\b(no|not|never|without|decrease|decreases|decreased|absence)\b", x["claim"].casefold())]
        if positive and negative:
            contradictions.append({"normalized_claim": normalized,
                "claim_ids": sorted({x["claim_id"] for x in positive + negative}),
                "card_ids": sorted({x["card_id"] for x in positive + negative}),
                "note": "Possible polarity conflict; human clinical adjudication required."})
    cards_without_evidence = sorted(known_ids - set(by_card))
    if require_approved:
        for card_id in cards_without_evidence:
            findings.append({"claim_id": "", "card_id": card_id, "severity": "error",
                             "issues": ["release blocked: no mapped evidence claims"]})
    error_count = sum(x["severity"] == "error" for x in findings)
    return {"schema_version": 1, "claims_count": len(records),
            "cards_with_evidence": len(by_card), "cards_without_evidence": cards_without_evidence,
            "findings": findings, "contradiction_candidates": contradictions,
            "release_ready": error_count == 0 and not contradictions,
            "error_count": error_count,
            "notice": "Automated governance checks validate metadata and workflow only; they do not establish source quality or clinical truth."}


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
    parser.add_argument("--evidence-registry", help="JSON evidence registry mapping claims to references and workflow status")
    parser.add_argument("--require-approved-evidence", action="store_true", help="Block release unless every card has current approved/published claim evidence")
    parser.add_argument("--self-test", action="store_true", help="Run built-in diagnostics")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def run_pipeline(args: argparse.Namespace) -> int:
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    specialty = canonicalize_specialty(args.specialty)
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

    evidence_report = None
    if getattr(args, "evidence_registry", None):
        evidence_records = load_evidence_registry(Path(args.evidence_registry))
        evidence_report = analyze_evidence_registry(
            cards, evidence_records,
            max_age_days=getattr(args, "max_review_age_days", 730),
            require_approved=getattr(args, "require_approved_evidence", False),
        )
    elif getattr(args, "require_approved_evidence", False):
        evidence_report = analyze_evidence_registry(
            cards, [], max_age_days=getattr(args, "max_review_age_days", 730), require_approved=True,
        )

    report = evaluate_quality(
        cards,
        require_reviewed=getattr(args, "require_reviewed", False),
        max_review_age_days=getattr(args, "max_review_age_days", 730),
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
            "clinical_review_notice": (
                "Automated checks assess structure and educational metadata only; "
                "they do not verify clinical truth, guideline currency, or patient safety."
            ),
        }, indent=2, ensure_ascii=False),
        report_path,
    )
    if evidence_report is not None:
        atomic_write_text(json.dumps(evidence_report, indent=2, ensure_ascii=False), output_dir / "evidence_governance_report.json")

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
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "sample.txt"
                atomic_write_text("hello", path)
                self.assertEqual(path.read_text(encoding="utf-8"), "hello")

        def test_json_loading_and_blueprint(self):
            with tempfile.TemporaryDirectory() as directory:
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

    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(UsiphusTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceGovernanceTests),
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