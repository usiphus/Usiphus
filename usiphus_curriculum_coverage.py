#!/usr/bin/env python3
"""Curriculum coverage analysis for discovered medical learning resources.

This tool measures mapped-resource coverage and review workflow state. It does
not grade clinical evidence quality, prove curriculum adequacy, or approve content.
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from usiphus_lib.ids import objective_id as make_objective_id

VERSION = "1.0.0"


def load_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _objectives(topic: dict[str, Any]) -> list[str]:
    return [str(x) for x in topic.get("learning_objectives", [])]


def analyze_coverage(curriculum: dict[str, Any], discovery: dict[str, Any],
                     *, relevance_threshold: float = 0.08) -> dict[str, Any]:
    topics = curriculum.get("topics", [])
    resources = discovery.get("resources", [])
    if not isinstance(topics, list) or not isinstance(resources, list):
        raise ValueError("curriculum.topics and discovery.resources must be arrays")
    topic_by_id = {str(t.get("id", "")): t for t in topics}
    objective_ids = {
        tid: {make_objective_id(tid, i) for i, _ in enumerate(_objectives(topic), 1)}
        for tid, topic in topic_by_id.items()
    }
    mapped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        if resource.get("access_status") == "search_link_only":
            continue
        if float(resource.get("relevance_score", 0) or 0) < relevance_threshold:
            continue
        for tid in resource.get("matched_topic_ids", []):
            if tid in topic_by_id:
                mapped[tid].append(resource)

    topic_rows = []
    domain_acc: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "topic_count": 0, "covered_topics": 0, "objective_count": 0,
        "objectives_with_resources": 0, "resource_count": 0
    })
    all_objectives = 0
    all_objectives_covered = 0
    for topic in topics:
        tid = str(topic.get("id", ""))
        found = mapped.get(tid, [])
        covered_objectives = set()
        for resource in found:
            covered_objectives.update(resource.get("matched_objective_ids", []))
        covered_objectives &= objective_ids.get(tid, set())
        objectives = _objectives(topic)
        missing = [
            {"objective_id": make_objective_id(tid, i), "objective": objective}
            for i, objective in enumerate(objectives, 1)
            if make_objective_id(tid, i) not in covered_objectives
        ]
        source_counts = Counter(str(r.get("source_id", "unknown")) for r in found)
        approved = sum(r.get("review_status") == "approved" for r in found)
        in_review = sum(r.get("review_status") == "in_review" for r in found)
        unreviewed = sum(r.get("review_status", "unreviewed") == "unreviewed" for r in found)
        domain_id = str(topic.get("domain_id", "unassigned"))
        d = domain_acc[domain_id]
        d["topic_count"] += 1
        d["covered_topics"] += bool(found)
        d["objective_count"] += len(objectives)
        d["objectives_with_resources"] += len(covered_objectives)
        d["resource_count"] += len(found)
        all_objectives += len(objectives)
        all_objectives_covered += len(covered_objectives)
        topic_rows.append({
            "topic_id": tid, "topic_name": topic.get("name", ""),
            "domain_id": domain_id, "resource_count": len(found),
            "source_family_count": len(source_counts),
            "source_counts": dict(sorted(source_counts.items())),
            "objectives_total": len(objectives),
            "objectives_with_resources": len(covered_objectives),
            "objective_coverage": round(len(covered_objectives) / len(objectives), 4) if objectives else 0.0,
            "missing_objectives": missing,
            "review_counts": {"approved": approved, "in_review": in_review, "unreviewed": unreviewed},
            "coverage_status": "mapped_resources_found" if found else "gap"
        })

    domain_rows = []
    for did, d in sorted(domain_acc.items()):
        domain_rows.append({
            "domain_id": did, **d,
            "topic_coverage": round(d["covered_topics"] / d["topic_count"], 4) if d["topic_count"] else 0,
            "objective_coverage": round(d["objectives_with_resources"] / d["objective_count"], 4) if d["objective_count"] else 0
        })
    covered_topics = sum(row["resource_count"] > 0 for row in topic_rows)
    total_topic_count = len(topic_rows)
    total_metadata = sum(row["resource_count"] for row in topic_rows)
    total_approved = sum(row["review_counts"]["approved"] for row in topic_rows)
    total_unreviewed = sum(row["review_counts"]["unreviewed"] for row in topic_rows)
    gaps = [row for row in topic_rows if row["coverage_status"] == "gap"]
    objective_gaps = sum(len(row["missing_objectives"]) for row in topic_rows)
    return {
        "schema_version": 1,
        "analyzer_version": VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "specialty": curriculum.get("specialty", "unspecified"),
        "method": {
            "relevance_threshold": relevance_threshold,
            "search_link_only_excluded": True,
            "coverage_unit": "resource metadata mapped to topic/objective",
            "warning": "Coverage is a discovery signal, not evidence quality, curriculum adequacy, or learner competence."
        },
        "summary": {
            "topics_total": total_topic_count,
            "topics_with_resources": covered_topics,
            "topics_without_resources": len(gaps),
            "topic_coverage": round(covered_topics / total_topic_count, 4) if total_topic_count else 0,
            "learning_objectives_total": all_objectives,
            "learning_objectives_with_resources": all_objectives_covered,
            "learning_objectives_without_resources": objective_gaps,
            "objective_coverage": round(all_objectives_covered / all_objectives, 4) if all_objectives else 0,
            "topic_resource_mappings": total_metadata,
            "approved_resource_mappings": total_approved,
            "unreviewed_resource_mappings": total_unreviewed
        },
        "domain_coverage": domain_rows,
        "topic_coverage": topic_rows,
        "priority_actions": [
            {"priority": "high", "topic_id": row["topic_id"], "action": "Discover and map at least one relevant resource; validate objectives and source families."}
            for row in gaps
        ] + [
            {"priority": "medium", "topic_id": row["topic_id"], "action": "Find resources addressing the listed unmapped learning objectives.", "missing_objective_ids": [x["objective_id"] for x in row["missing_objectives"]]}
            for row in topic_rows if row["resource_count"] and row["missing_objectives"]
        ] + [
            {"priority": "review", "topic_id": row["topic_id"], "action": "Faculty-review discovered metadata and destination; do not treat relevance as clinical approval."}
            for row in topic_rows if row["review_counts"]["unreviewed"]
        ],
        "governance": {
            "automatic_approval": False,
            "clinical_quality_scoring": False,
            "search_links_counted_as_resources": False,
            "human_review_required": True
        }
    }


class CoverageTests(unittest.TestCase):
    def test_search_links_excluded_and_objectives_mapped(self):
        curriculum = {"specialty": "test", "topics": [{
            "id": "t1", "domain_id": "d1", "name": "Topic",
            "learning_objectives": ["First objective", "Second objective"]
        }, {"id": "t2", "domain_id": "d1", "name": "Empty", "learning_objectives": []}]}
        discovery = {"resources": [
            {"source_id": "pubmed", "access_status": "metadata_only", "relevance_score": .5,
             "matched_topic_ids": ["t1"], "matched_objective_ids": ["t1-LO01"], "review_status": "unreviewed"},
            {"source_id": "anki", "access_status": "search_link_only", "relevance_score": 1,
             "matched_topic_ids": ["t2"], "matched_objective_ids": [], "review_status": "unreviewed"}
        ]}
        report = analyze_coverage(curriculum, discovery)
        self.assertEqual(report["summary"]["topics_with_resources"], 1)
        self.assertEqual(report["summary"]["learning_objectives_with_resources"], 1)
        self.assertEqual(report["summary"]["topics_without_resources"], 1)
        self.assertEqual(report["topic_coverage"][0]["missing_objectives"][0]["objective_id"], "t1-LO02")

    def test_low_relevance_excluded(self):
        c = {"topics": [{"id": "t", "learning_objectives": []}]}
        d = {"resources": [{"relevance_score": .01, "matched_topic_ids": ["t"], "access_status": "metadata_only"}]}
        self.assertEqual(analyze_coverage(c, d)["summary"]["topics_with_resources"], 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum")
    parser.add_argument("--resources", help="resources.json produced by resource discovery")
    parser.add_argument("--output", default="curriculum_coverage_report.json")
    parser.add_argument("--relevance-threshold", type=float, default=0.08)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CoverageTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if not args.curriculum or not args.resources:
        parser.error("--curriculum and --resources are required unless --self-test is used")
    if not 0 <= args.relevance_threshold <= 1:
        parser.error("--relevance-threshold must be between 0 and 1")
    try:
        report = analyze_coverage(load_json(args.curriculum), load_json(args.resources),
                                  relevance_threshold=args.relevance_threshold)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], indent=2))
        print(f"Report written to {target}")
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
