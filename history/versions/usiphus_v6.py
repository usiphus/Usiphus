#!/usr/bin/env python3
"""Usiphus V6 — reliable medical-exam flashcard builder.

Features
--------
* Multi-source JSON ingestion (exam cards, case library, blueprint).
* Canonical data model with stable IDs and source provenance.
* Structural and examination-format validation (MCQ, OSCE, viva, case, basic).
* Deterministic Anki note GUIDs and collision-resistant model/deck IDs.
* Atomic TSV/JSON/Markdown exports.
* Defensive AnkiConnect handling.
* JSON quality report and diagnostic self-tests.

Important clinical limitation
------------------------------
This program validates structure and educational presentation heuristically.
It DOES NOT establish medical factual accuracy, guideline currency, or clinical
safety. Medical claims must be reviewed against current authoritative sources
by a qualified subject-matter reviewer.

Example
-------
python usiphus.py --specialty ophthalmology --blueprint blueprint.json --output ./build
python usiphus.py --exam-cards cards.json --specialty surgery --self-test
python usiphus.py --exam-cards cards.json --specialty ophthalmology --apkg --study-guide
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

VERSION = "6.0.0"
REPORT_SCHEMA_VERSION = 3
TSV_HEADER = [
    "card_id", "specialty", "card_type", "difficulty", "prompt", "answer",
    "options", "correct_option", "learning_objective", "tags", "source",
    "references",
]
PROMPT_MAX_CHARS = 500
ANSWER_MIN_CHARS = 15
ANSWER_MAX_CHARS = 5000
ANKI_CONNECT_TIMEOUT_SECONDS = 10
MAX_INPUT_FILE_BYTES = 50 * 1024 * 1024
VALID_CARD_TYPES = {"basic", "mcq", "osce", "viva", "clinical_case"}
VALID_DIFFICULTIES = {"beginner", "intermediate", "advanced"}
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
        ))
    return cards


# ---------------------------------------------------------------------------
# Structural and educational-format quality checks
# ---------------------------------------------------------------------------

def evaluate_quality(cards: Iterable[Card]) -> QualityReport:
    """Pure heuristic validation; does not mutate cards or verify medical facts."""
    card_list = list(cards)
    prompt_keys = [c.prompt.casefold() for c in card_list if c.prompt]
    prompt_counts = Counter(prompt_keys)
    id_counts = Counter(c.card_id_key for c in card_list if c.card_id_key)
    results: list[CardReport] = []
    total_errors = 0
    total_warnings = 0

    for card in card_list:
        findings: list[Finding] = []
        prompt = card.prompt
        answer = card.answer

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

        if not prompt:
            add(FindingCode.PROMPT_EMPTY, "Prompt is empty")
        else:
            if len(prompt) > PROMPT_MAX_CHARS:
                add(FindingCode.PROMPT_TOO_LONG, f"{len(prompt)} chars > {PROMPT_MAX_CHARS}")
            if prompt_counts[prompt.casefold()] > 1:
                add(FindingCode.PROMPT_DUPLICATE, "Prompt duplicates another card after normalization")
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
                add(FindingCode.MCQ_CORRECT_OPTION_INVALID, "correct_option must match an option or A/B/C...")

        if card.card_type == "osce" and not prompt:
            add(FindingCode.OSCE_TASK_MISSING, "OSCE station requires a task prompt")

        if not card.source:
            add(FindingCode.SOURCE_MISSING, "No source provenance supplied", "warn")
        if not card.references:
            add(FindingCode.REFERENCE_MISSING, "No medical reference supplied; factual accuracy is unverified", "warn")

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
    model_id = anki_numeric_id("usiphus-model-v6", "medical-card")
    deck_id = anki_numeric_id("usiphus-deck-v6", deck_name)

    model = genanki.Model(
        model_id,
        "Usiphus V6 Medical Model",
        fields=[
            {"name": "Prompt"}, {"name": "Answer"}, {"name": "Options"},
            {"name": "CorrectOption"}, {"name": "LearningObjective"},
            {"name": "Tags"}, {"name": "CardID"}, {"name": "References"},
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
        lines.extend([f"*Type: {card.card_type} · Difficulty: {card.difficulty} · Tags: {', '.join(card.tags)}*", ""])
    atomic_write_text("\n".join(lines), destination)
    LOG.info("Study guide written to %s", destination)


# ---------------------------------------------------------------------------
# CLI orchestration
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Usiphus V6 medical exam deck builder")
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
    parser.add_argument("--self-test", action="store_true", help="Run built-in diagnostics")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def run_pipeline(args: argparse.Namespace) -> int:
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    specialty = canonicalize_specialty(args.specialty)
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

    report = evaluate_quality(cards)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "quality_report.json"
    atomic_write_text(
        json.dumps(report_to_dict(report), indent=2, ensure_ascii=False),
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
