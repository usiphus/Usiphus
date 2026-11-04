#!/usr/bin/env python3
"""Independently recompute curriculum coverage from saved Usiphus artifacts.

Example:
  python scripts/coverage_report.py --curriculum examples/ophthalmology/curriculum.json \
      --generated build/ophthalmology/generated_cards.json \
      --output build/ophthalmology/coverage_report.rechecked.json

Exit codes: 0 complete, 1 coverage/traceability gaps, 2 invalid input.
This is not a clinical accuracy or faculty-approval check.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def normalize(value: Any) -> str:
    return re.sub(r"\\s+", " ", str(value or "").casefold()).strip()


def compute_coverage(curriculum: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    topics = curriculum.get("topics")
    cards = generated.get("cards")
    if not isinstance(topics, list) or not topics:
        raise ValueError("Curriculum must contain a non-empty topics array")
    if not isinstance(cards, list):
        raise ValueError("Generated artifact must contain a cards array")

    topic_by_id: dict[str, dict[str, Any]] = {}
    for index, topic in enumerate(topics, 1):
        if not isinstance(topic, dict):
            raise ValueError(f"Curriculum topic {index} must be an object")
        topic_id = str(topic.get("id") or "").strip()
        if not topic_id or topic_id in topic_by_id:
            raise ValueError(f"Curriculum topic {index} has a missing or duplicate id")
        objectives = topic.get("learning_objectives")
        if not isinstance(objectives, list) or not objectives:
            raise ValueError(f"Topic {topic_id} has no learning_objectives array")
        if any(not isinstance(x, str) or not x.strip() for x in objectives):
            raise ValueError(f"Topic {topic_id} contains an invalid learning objective")
        normalized = [normalize(x) for x in objectives]
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"Topic {topic_id} has duplicate learning objectives")
        topic_by_id[topic_id] = topic

    counts: dict[str, Counter[str]] = {
        topic_id: Counter() for topic_id in topic_by_id
    }
    type_counts: dict[str, Counter[str]] = {
        topic_id: Counter() for topic_id in topic_by_id
    }
    errors: list[str] = []
    seen_card_ids: set[str] = set()
    missing_topic: list[str] = []

    for index, card in enumerate(cards, 1):
        if not isinstance(card, dict):
            errors.append(f"Card {index} is not an object")
            continue
        card_id = str(card.get("card_id") or f"index:{index}")
        if card_id in seen_card_ids:
            errors.append(f"Duplicate card_id: {card_id}")
        seen_card_ids.add(card_id)
        topic_id = str(card.get("topic_id") or "").strip()
        if topic_id not in topic_by_id:
            missing_topic.append(card_id)
            errors.append(f"Card {card_id} references unknown/missing topic_id {topic_id!r}")
            continue
        objective = normalize(card.get("learning_objective"))
        allowed = {normalize(x) for x in topic_by_id[topic_id]["learning_objectives"]}
        if objective not in allowed:
            errors.append(f"Card {card_id} has an unknown learning objective for topic {topic_id}")
            continue
        counts[topic_id][objective] += 1
        type_counts[topic_id][str(card.get("card_type") or "unknown")] += 1

    topic_rows = []
    total_objectives = 0
    covered_objectives = 0
    topic_gaps = []
    for topic_id, topic in topic_by_id.items():
        objectives = topic["learning_objectives"]
        total_objectives += len(objectives)
        objective_rows = []
        for objective in objectives:
            count = counts[topic_id][normalize(objective)]
            covered_objectives += int(count > 0)
            objective_rows.append({
                "objective": objective,
                "card_count": count,
                "covered": count > 0,
            })
        missing = [x["objective"] for x in objective_rows if not x["covered"]]
        if missing:
            topic_gaps.append(topic_id)
        topic_rows.append({
            "topic_id": topic_id,
            "topic_name": topic.get("name") or topic.get("topic") or topic_id,
            "card_count": sum(counts[topic_id].values()),
            "card_type_counts": dict(type_counts[topic_id]),
            "objectives": objective_rows,
            "uncovered_objectives": missing,
            "coverage_complete": not missing,
        })

    pct = round(100 * covered_objectives / total_objectives, 2) if total_objectives else 0.0
    return {
        "schema_version": 1,
        "specialty": curriculum.get("specialty") or generated.get("specialty"),
        "summary": {
            "topics_planned": len(topic_by_id),
            "topics_with_gaps": len(topic_gaps),
            "objectives_planned": total_objectives,
            "objectives_covered": covered_objectives,
            "objective_coverage_percent": pct,
            "cards_received": len(cards),
            "cards_with_unknown_topic": len(missing_topic),
            "errors": len(errors),
        },
        "topics": topic_rows,
        "topic_gaps": topic_gaps,
        "errors": errors,
        "clinical_accuracy_verified": False,
        "faculty_review_required": True,
        "notice": "This independent report measures objective linkage only; it does not verify clinical accuracy, guideline currency, source quality, or examination validity.",
    }


def _self_test() -> None:
    curriculum = {"specialty": "test", "topics": [{
        "id": "t1", "name": "Topic", "learning_objectives": ["Objective A", "Objective B"]
    }]}
    generated = {"specialty": "test", "cards": [
        {"card_id": "c1", "topic_id": "t1", "learning_objective": "Objective A", "card_type": "basic"},
    ]}
    report = compute_coverage(curriculum, generated)
    assert report["summary"]["objective_coverage_percent"] == 50.0
    assert report["topic_gaps"] == ["t1"]
    assert report["clinical_accuracy_verified"] is False
    complete = compute_coverage(curriculum, {"cards": [
        *generated["cards"],
        {"card_id": "c2", "topic_id": "t1", "learning_objective": "Objective B", "card_type": "mcq"},
    ]})
    assert complete["summary"]["objective_coverage_percent"] == 100.0
    assert not complete["errors"]
    print("coverage_report self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum", type=Path)
    parser.add_argument("--generated", type=Path)
    parser.add_argument("--output", type=Path, default=Path("coverage_report.rechecked.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if not args.curriculum or not args.generated:
        parser.error("--curriculum and --generated are required unless --self-test is used")
    try:
        curriculum = json.loads(args.curriculum.read_text(encoding="utf-8"))
        generated = json.loads(args.generated.read_text(encoding="utf-8"))
        if not isinstance(curriculum, dict) or not isinstance(generated, dict):
            raise ValueError("Both JSON roots must be objects")
        report = compute_coverage(curriculum, generated)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"coverage_report: {exc}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(
        f"Objective coverage: {summary['objectives_covered']}/"
        f"{summary['objectives_planned']} ({summary['objective_coverage_percent']}%). "
        f"Report: {args.output}"
    )
    if report["errors"] or report["topic_gaps"]:
        print("Coverage/traceability gate: FAIL", file=sys.stderr)
        return 1
    print("Coverage/traceability gate: PASS (not clinical approval)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
