
#!/usr/bin/env python3
"""Usiphus core: card model, quality checks, exports and evidence-registry analysis.

Single implementation shared by V7, V8 and V9 (previously copied three times).
Structural and metadata checks only; nothing here establishes clinical truth.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import (  # noqa: E402
    canonicalize_text, canonicalize_multiline, canonicalize_card_id, canonicalize_specialty,
    canonicalize_tags, canonicalize_string_list, canonicalize_prompt, stable_digest,
    stable_blueprint_id, anki_numeric_id, anki_guid, lexical_similarity,
    find_near_duplicate_pairs, atomic_write_text, push_to_ankiconnect, parse_as_of,
    tokens as _tokens, valid_iso_date as _valid_iso_date,
)

LOG = logging.getLogger("usiphus")
REPORT_SCHEMA_VERSION = 6  # default; V7/V8 pass their own historical value explicitly


TSV_HEADER = [
    "card_id", "specialty", "card_type", "difficulty", "prompt", "answer",
    "options", "correct_option", "learning_objective", "tags", "source",
    "references", "cognitive_level", "exam_domain", "rubric",
    "evidence_status", "reviewed_on", "reviewer", "evidence_excerpt",
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
    PROMPT_ANSWER_LOW_OVERLAP = "prompt-answer-low-overlap"
    ANSWER_REVIEW_SUGGESTION = "answer-review-suggestion"
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
    evidence_excerpt: str = ""

    @property
    def prompt(self) -> str:
        return canonicalize_prompt(self.prompt_raw)

    @property
    def answer(self) -> str:
        return canonicalize_multiline(self.answer_raw)

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
            self.evidence_excerpt,
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


_WHITESPACE_RE = re.compile(r"\s+")


_TRAILING_Q_RE = re.compile(r"\?+$")


_NON_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


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
        evidence_excerpt=canonicalize_text(record.get("evidence_excerpt") or record.get("evidence_quote") or ""),
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
    as_of: "date | None" = None,
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
    today = as_of or date.today()
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


def evaluate_quality(
    cards: Iterable[Card],
    *,
    require_reviewed: bool = False,
    max_review_age_days: int = 730,
    near_duplicate_threshold: float = 0.78,
    as_of: "date | None" = None,
    schema_version: int | None = None,
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
    today = as_of or date.today()

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
        schema_version=schema_version or REPORT_SCHEMA_VERSION,
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


def _load_genanki():
    try:
        import genanki  # type: ignore
        return genanki
    except ImportError as exc:
        raise RuntimeError("genanki is required for --apkg. Install: pip install genanki") from exc


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
        if card.evidence_excerpt:
            lines.extend(["**Source evidence excerpt (unverified)**", f"> {card.evidence_excerpt}", ""])
        if card.rubric:
            lines.extend(["**Marking checklist**", *[f"- [ ] {item}" for item in card.rubric], ""])
        lines.extend([
            f"*Type: {card.card_type} · Difficulty: {card.difficulty} · Cognitive level: {card.cognitive_level} · Evidence: {card.evidence_status} · Tags: {', '.join(card.tags)}*",
            "",
        ])
    atomic_write_text("\n".join(lines), destination)
    LOG.info("Study guide written to %s", destination)
