"""Deterministic quality and curriculum-coverage gates for generated specialty decks.

These checks establish structure, traceability and blueprint coverage only. They
cannot establish clinical truth, guideline currency or educational validity.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

VALID_CARD_TYPES = {"basic", "mcq", "clinical_case", "viva", "osce"}
VALID_DIFFICULTIES = {"beginner", "intermediate", "advanced"}
VALID_COGNITIVE_LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _unique_strings(value: Any, field: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    result = [item.strip() for item in value]
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    if len({_norm(item) for item in result}) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result


def _reference_key(ref: Any) -> str:
    """Normalised identity of a faculty-supplied reference given as a plain string or an object."""
    if isinstance(ref, str):
        return _norm(ref)
    if isinstance(ref, dict):
        return _norm(ref.get("url") or ref.get("reference") or ref.get("doi") or ref.get("pmid"))
    return ""


def validate_curriculum_blueprint(curriculum: dict[str, Any]) -> dict[str, Any]:
    """Fail before inference when a specialty blueprint cannot support coverage."""
    topics = curriculum.get("topics")
    domains = curriculum.get("curriculum_domains")
    if not isinstance(topics, list) or not topics:
        raise ValueError("Curriculum requires a non-empty topics array")
    if not isinstance(domains, list) or not domains:
        raise ValueError("Specialty generation requires curriculum_domains")
    domain_ids = {str(item.get("id")) for item in domains if isinstance(item, dict) and item.get("id")}
    if len(domain_ids) != len(domains):
        raise ValueError("Curriculum domain IDs must be present and unique")
    topic_ids: set[str] = set()
    objective_total = 0
    for index, topic in enumerate(topics, 1):
        if not isinstance(topic, dict):
            raise ValueError(f"Topic {index} must be an object")
        topic_id = str(topic.get("id") or "").strip()
        if not topic_id or topic_id in topic_ids:
            raise ValueError(f"Topic {index} needs a unique stable id")
        topic_ids.add(topic_id)
        if topic.get("domain_id") not in domain_ids:
            raise ValueError(f"Topic {topic_id} references an unknown domain_id")
        if not str(topic.get("name") or topic.get("topic") or "").strip():
            raise ValueError(f"Topic {topic_id} needs a name")
        objectives = _unique_strings(topic.get("learning_objectives"), f"{topic_id}.learning_objectives", required=True)
        if len(objectives) < 3:
            raise ValueError(f"Topic {topic_id} needs at least three learning objectives")
        _unique_strings(topic.get("coverage_tags"), f"{topic_id}.coverage_tags", required=True)
        _unique_strings(topic.get("exam_emphasis"), f"{topic_id}.exam_emphasis", required=True)
        count = topic.get("card_count", 6)
        if isinstance(count, bool) or not isinstance(count, int) or count < len(objectives):
            raise ValueError(f"Topic {topic_id} card_count must be an integer covering every objective at least once")
        mix = topic.get("card_mix")
        if mix is not None:
            if not isinstance(mix, dict) or any(
                kind not in VALID_CARD_TYPES or isinstance(amount, bool)
                or not isinstance(amount, int) or amount < 0
                for kind, amount in mix.items()
            ) or sum(mix.values()) != count:
                raise ValueError(f"Topic {topic_id} card_mix must contain valid non-negative counts summing to card_count")
        objective_total += len(objectives)
    return {
        "topic_count": len(topics),
        "domain_count": len(domains),
        "objective_count": objective_total,
        "specialty": curriculum.get("specialty"),
    }


def validate_topic_cards(
    topic: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    specialty: str,
    requested_mix: dict[str, int],
) -> dict[str, Any]:
    """Reject weak or off-blueprint topic batches before they are checkpointed."""
    topic_id = str(topic.get("id") or topic.get("name") or topic.get("topic") or "topic")
    objectives = _unique_strings(topic.get("learning_objectives", []), f"{topic_id}.learning_objectives")
    objective_lookup = {_norm(item): item for item in objectives}
    expected_count = sum(requested_mix.values())
    if len(cards) != expected_count:
        raise ValueError(f"{topic_id}: expected {expected_count} cards, received {len(cards)}")

    prompts: set[str] = set()
    objective_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    faculty_refs = topic.get("reference_sources", [])
    if not isinstance(faculty_refs, list):
        raise ValueError(f"{topic_id}: reference_sources must be a list")
    allowed_refs = {key for key in (_reference_key(ref) for ref in faculty_refs) if key}
    for index, card in enumerate(cards, 1):
        if not isinstance(card, dict):
            raise ValueError(f"{topic_id}: card {index} must be an object")
        for field in ("prompt", "answer", "learning_objective", "card_type", "difficulty", "cognitive_level", "exam_domain"):
            if not isinstance(card.get(field), str) or not card[field].strip():
                raise ValueError(f"{topic_id}: card {index} requires non-empty {field}")
        prompt, answer = card["prompt"].strip(), card["answer"].strip()
        if len(prompt) < 12 or len(prompt) > 500 or len(answer.split()) < 12:
            raise ValueError(f"{topic_id}: card {index} prompt/answer is too short or prompt exceeds 500 characters")
        if not prompt.endswith(("?", ":")):
            raise ValueError(f"{topic_id}: card {index} prompt must end with '?' or ':'")
        key = _norm(prompt)
        if key in prompts:
            raise ValueError(f"{topic_id}: duplicate prompt detected")
        prompts.add(key)

        card_type = card["card_type"].casefold()
        if card_type not in VALID_CARD_TYPES or requested_mix.get(card_type, 0) == 0:
            raise ValueError(f"{topic_id}: card {index} uses unrequested/invalid card_type {card_type!r}")
        type_counts[card_type] += 1
        if card["difficulty"].casefold() not in VALID_DIFFICULTIES:
            raise ValueError(f"{topic_id}: card {index} has invalid difficulty")
        if card["cognitive_level"].casefold() not in VALID_COGNITIVE_LEVELS:
            raise ValueError(f"{topic_id}: card {index} has invalid cognitive_level")
        if objectives:
            objective_key = _norm(card["learning_objective"])
            if objective_key not in objective_lookup:
                raise ValueError(f"{topic_id}: card {index} is not mapped to an exact curriculum learning objective")
            objective_counts[objective_lookup[objective_key]] += 1

        tags = _unique_strings(card.get("tags"), f"{topic_id}.card[{index}].tags", required=True)
        coverage = {_norm(tag) for tag in topic.get("coverage_tags", [])}
        if coverage and not any(_norm(tag) in coverage for tag in tags):
            raise ValueError(f"{topic_id}: card {index} has no tag matching the topic coverage tags")

        references = _unique_strings(card.get("references", []), f"{topic_id}.card[{index}].references")
        if not allowed_refs and references:
            raise ValueError(f"{topic_id}: card {index} invented references; no faculty references were supplied")
        if allowed_refs and any(_norm(ref) not in allowed_refs for ref in references):
            raise ValueError(f"{topic_id}: card {index} cites a reference not supplied by faculty")

        if card_type == "mcq":
            options = _unique_strings(card.get("options"), f"{topic_id}.card[{index}].options", required=True)
            if len(options) not in {4, 5}:
                raise ValueError(f"{topic_id}: MCQ {index} must have four or five options")
            if not isinstance(card.get("correct_option"), str) or _norm(card["correct_option"]) not in {_norm(x) for x in options}:
                raise ValueError(f"{topic_id}: MCQ {index} must key exactly one listed option")
            if len(answer.split()) < 20:
                raise ValueError(f"{topic_id}: MCQ {index} needs a substantive answer rationale")
        if card_type == "clinical_case" and len(prompt.split()) < 20:
            raise ValueError(f"{topic_id}: clinical case {index} needs a self-contained clinical vignette")
        if card_type == "viva" and len(answer.split()) < 20:
            raise ValueError(f"{topic_id}: viva {index} needs a structured oral-exam answer")
        if card_type == "osce":
            task_words = {"candidate", "station", "perform", "counsel", "demonstrate", "obtain", "assess", "examine", "communicate", "interpret", "explain"}
            if len(prompt.split()) < 12 or not (set(re.findall(r"[a-z]+", prompt.casefold())) & task_words):
                raise ValueError(f"{topic_id}: OSCE {index} needs a clear candidate instruction or station task")
            rubric = _unique_strings(card.get("rubric"), f"{topic_id}.card[{index}].rubric", required=True)
            if len(rubric) < 3:
                raise ValueError(f"{topic_id}: OSCE {index} needs at least three observable checklist items")

    mismatches = {
        kind: {"expected": amount, "actual": type_counts.get(kind, 0)}
        for kind, amount in requested_mix.items()
        if type_counts.get(kind, 0) != amount
    }
    if mismatches:
        raise ValueError(f"{topic_id}: requested exam-format mix not met: {mismatches}")
    missing_objectives = [objective for objective in objectives if objective_counts[objective] == 0]
    if missing_objectives:
        raise ValueError(f"{topic_id}: no generated card covers objectives: {missing_objectives}")

    # Metadata is stamped by the application, never trusted from model output.
    for index, card in enumerate(cards, 1):
        card["specialty"] = specialty
        card["topic_id"] = topic.get("id")
        card["domain_id"] = topic.get("domain_id")
        card["curriculum_domain"] = topic.get("domain")
        card["evidence_status"] = "unreviewed"
        card["references"] = card.get("references", [])
    return {
        "topic_id": topic.get("id"),
        "topic_name": topic.get("name") or topic.get("topic"),
        "cards_generated": len(cards),
        "objective_count": len(objectives),
        "objectives_covered": len(objectives) - len(missing_objectives),
        "objective_card_counts": dict(objective_counts),
        "card_type_counts": dict(type_counts),
        "source_reference_count": len(allowed_refs),
        "evidence_status": "unreviewed",
        "coverage_complete": not missing_objectives and not mismatches,
    }


def build_generation_coverage_report(
    curriculum: dict[str, Any],
    topic_reports: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize actual topic/objective/format coverage, not just planned totals."""
    by_topic = {str(item.get("topic_id")): item for item in topic_reports}
    topics = curriculum.get("topics", [])
    missing = []
    for topic in topics:
        topic_id = str(topic.get("id"))
        report = by_topic.get(topic_id)
        if not report or not report.get("coverage_complete"):
            missing.append(topic_id)
    expected_objectives = sum(len(item.get("learning_objectives", [])) for item in topics)
    covered_objectives = sum(item.get("objectives_covered", 0) for item in topic_reports)
    return {
        "schema_version": 1,
        "specialty": curriculum.get("specialty"),
        "topics_planned": len(topics),
        "topics_generated": len(topic_reports),
        "topics_with_complete_coverage": len(topics) - len(missing),
        "objectives_planned": expected_objectives,
        "objectives_covered": covered_objectives,
        "cards_generated": len(cards),
        "topic_gaps": missing,
        "topic_reports": topic_reports,
        "all_topics_covered": not missing and len(topic_reports) == len(topics),
        "clinical_accuracy_verified": False,
        "faculty_review_required": True,
        "source_gap_count": sum(1 for item in topic_reports if item.get("source_reference_count", 0) == 0),
        "notice": "Coverage and structural gates do not verify clinical accuracy, guideline currency, source quality or exam validity.",
    }
