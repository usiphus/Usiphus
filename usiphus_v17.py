#!/usr/bin/env python3
"""Usiphus V17 — stable contracts, curriculum intelligence and workflow validation.

V17 is an additive local-first analysis layer. It does not rewrite source
artifacts, infer clinical truth, authenticate reviewers, or approve mappings.
Only explicitly faculty-approved mapping records count as verified coverage.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

VERSION = "17.0.0"
CONTRACT_VERSION = 1
MAX_BYTES = 50 * 1024 * 1024
LOG = logging.getLogger("usiphus.v17")

try:
    from usiphus_common import atomic_write_text
except ImportError:  # Standalone source recovery / direct module execution.
    def atomic_write_text(text: str, destination: Path) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="." + destination.name + ".", suffix=".tmp",
                                    dir=str(destination.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, destination)
        finally:
            if os.path.exists(name):
                os.unlink(name)


CONTRACTS: dict[str, dict[str, Any]] = {
    "card": {
        "required": ("card_id", "prompt"),
        "strings": ("card_id", "specialty", "prompt", "answer", "learning_objective"),
        "lists": ("competency_ids", "tags", "evidence_claim_ids"),
    },
    "claim": {
        "required": ("claim_id", "claim"),
        "strings": ("claim_id", "claim", "card_id", "status"),
        "lists": ("source_ids", "references"),
    },
    "source": {
        "required": ("source_id", "title"),
        "strings": ("source_id", "title", "publisher", "jurisdiction", "version",
                    "publication_date", "retrieved_at", "status", "url"),
    },
    "curriculum": {
        "required": ("specialty", "competencies"),
        "strings": ("specialty",),
        "lists": ("competencies",),
    },
    "assessment": {
        "required": ("item_id",),
        "strings": ("item_id", "question", "specialty", "learning_objective",
                    "cognitive_level", "status"),
        "lists": ("competency_ids", "evidence_claim_ids", "options"),
    },
    "release_manifest": {
        "required": ("schema_version", "release_status"),
        "strings": ("release_status", "generated_at"),
        "objects": ("inputs", "outputs", "release_policy"),
    },
}


class V17Error(ValueError):
    """An actionable V17 error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def read_json(path: Path) -> Any:
    path = Path(path)
    if not path.is_file():
        raise V17Error("V17-E001", f"Input file does not exist: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise V17Error("V17-E002", f"Input exceeds {MAX_BYTES} bytes: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise V17Error("V17-E003", f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise V17Error("V17-E004", f"Cannot read {path}: {exc}") from exc


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", path)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def timezone_aware(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(clean(value).replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def unwrap_document(payload: Any, expected_type: str | None = None) -> tuple[Any, list[str]]:
    """Unwrap an explicitly versioned V17 envelope without changing its data."""
    warnings: list[str] = []
    if isinstance(payload, dict) and "contract_type" in payload and "data" in payload:
        kind = clean(payload.get("contract_type"))
        if expected_type and kind != expected_type:
            raise V17Error("V17-E010", f"Expected contract '{expected_type}', received '{kind}'")
        version = payload.get("contract_version")
        if type(version) is not int or version != CONTRACT_VERSION:
            raise V17Error("V17-E011", f"Unsupported contract version: {version!r}")
        return payload["data"], warnings
    if isinstance(payload, dict) and "schema_version" in payload:
        version = payload["schema_version"]
        if type(version) is not int or version < 1:
            raise V17Error("V17-E012", "schema_version must be a positive integer")
        if version > CONTRACT_VERSION:
            raise V17Error("V17-E013", f"Document schema version {version} is newer than supported version {CONTRACT_VERSION}")
    else:
        warnings.append("Legacy unversioned input accepted in compatibility mode; meaning was not modified.")
    return payload, warnings


def validate_contract(kind: str, payload: Any) -> dict[str, Any]:
    if kind not in CONTRACTS:
        raise V17Error("V17-E014", f"Unknown contract type '{kind}'. Choose: {', '.join(sorted(CONTRACTS))}")
    data, warnings = unwrap_document(payload, kind)
    spec = CONTRACTS[kind]
    errors: list[str] = []
    records = data
    if kind in {"card", "claim", "source", "assessment"}:
        if isinstance(data, dict):
            key = {"card": "cards", "claim": "claims", "source": "sources",
                   "assessment": "items"}.get(kind)
            if key and isinstance(data.get(key), list):
                records = data[key]
            else:
                records = [data]
        if not isinstance(records, list):
            errors.append("expected an object or list of objects")
            records = []
        seen: set[str] = set()
        id_field = {"card": "card_id", "claim": "claim_id", "source": "source_id",
                    "assessment": "item_id"}[kind]
        for index, row in enumerate(records):
            if not isinstance(row, dict):
                errors.append(f"record[{index}] must be an object")
                continue
            for field in spec["required"]:
                if not clean(row.get(field)):
                    errors.append(f"record[{index}].{field} is required")
            for field in spec.get("strings", ()):
                if field in row and row[field] is not None and not isinstance(row[field], str):
                    errors.append(f"record[{index}].{field} must be a string")
            for field in spec.get("lists", ()):
                if field in row and row[field] is not None and not isinstance(row[field], list):
                    errors.append(f"record[{index}].{field} must be a list")
            identifier = clean(row.get(id_field)).casefold()
            if identifier and identifier in seen:
                errors.append(f"record[{index}].{id_field} duplicates another record")
            if identifier:
                seen.add(identifier)
    elif kind == "curriculum":
        if not isinstance(data, dict):
            errors.append("curriculum must be an object")
        else:
            for field in spec["required"]:
                if field not in data or (field == "specialty" and not clean(data.get(field))):
                    errors.append(f"{field} is required")
            comps = data.get("competencies", [])
            if not isinstance(comps, list):
                errors.append("competencies must be a list")
            else:
                seen = set()
                for index, row in enumerate(comps):
                    if not isinstance(row, dict):
                        errors.append(f"competencies[{index}] must be an object")
                        continue
                    cid = clean(row.get("competency_id") or row.get("id"))
                    if not cid:
                        errors.append(f"competencies[{index}] requires competency_id or id")
                    elif cid.casefold() in seen:
                        errors.append(f"competencies[{index}] duplicates competency ID '{cid}'")
                    seen.add(cid.casefold())
                    if "required" in row and not isinstance(row["required"], bool):
                        errors.append(f"competencies[{index}].required must be boolean")
                    weight = row.get("weight", 1.0)
                    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
                        errors.append(f"competencies[{index}].weight must be finite and positive")
    elif kind == "release_manifest":
        if not isinstance(data, dict):
            errors.append("release manifest must be an object")
        else:
            for field in spec["required"]:
                if field not in data:
                    errors.append(f"{field} is required")
            if "schema_version" in data and type(data["schema_version"]) is not int:
                errors.append("schema_version must be an integer")
            for field in spec["objects"]:
                if field in data and not isinstance(data[field], dict):
                    errors.append(f"{field} must be an object")
    return {"contract_type": kind, "contract_version": CONTRACT_VERSION,
            "valid": not errors, "record_count": len(records) if kind in {"card", "claim", "source", "assessment"} else 1,
            "errors": errors, "warnings": warnings}


def migrate_document(kind: str, payload: Any) -> dict[str, Any]:
    """Wrap a validated legacy document; preserve the original payload verbatim."""
    result = validate_contract(kind, payload)
    if not result["valid"]:
        raise V17Error("V17-E020", "Migration refused: " + "; ".join(result["errors"]))
    data, _ = unwrap_document(payload, kind)
    return {"contract_type": kind, "contract_version": CONTRACT_VERSION,
            "migration": {"from": "legacy-or-native", "method": "non_destructive_envelope",
                          "meaning_changed": False},
            "data": data}


def records(payload: Any, key: str, label: str) -> list[dict[str, Any]]:
    rows = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise V17Error("V17-E030", f"{label} must be a list of objects or contain a '{key}' list")
    return rows


def load_curriculum(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    data, _ = unwrap_document(payload, "curriculum")
    check = validate_contract("curriculum", data)
    if not check["valid"]:
        raise V17Error("V17-E031", "Invalid curriculum: " + "; ".join(check["errors"]))
    comps = []
    for row in data["competencies"]:
        cid = clean(row.get("competency_id") or row.get("id"))
        comps.append({"id": cid, "title": clean(row.get("title") or row.get("name") or cid),
                      "domain": clean(row.get("domain") or "Uncategorized"),
                      "level": clean(row.get("level") or row.get("cognitive_level") or "unspecified"),
                      "required": row.get("required", True), "weight": float(row.get("weight", 1.0)),
                      "objectives": sorted({clean(x) for x in row.get("learning_objectives", []) if clean(x)})})
    return clean(data["specialty"]).casefold(), comps



def find_exact_duplicate_cards(cards: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Find exact normalized prompt duplicates; candidates always require human review."""
    groups: dict[str, list[str]] = defaultdict(list)
    for card_id, card in cards.items():
        prompt = re.sub(r"\s+", " ", clean(card.get("prompt") or card.get("question"))).casefold()
        if prompt:
            groups[prompt].append(card_id)
    return [{"card_ids": sorted(ids), "review_required": True,
             "match_method": "exact-normalized-prompt"}
            for ids in groups.values() if len(ids) > 1]


def summarize_pilot_metrics(path: Path) -> dict[str, Any]:
    """Summarize only rows explicitly consented/approved for aggregate use."""
    durations: list[float] = []
    corrections = traceability = reproducible = reproducibility_responses = friction = 0
    role_counts: dict[str, int] = defaultdict(int)
    excluded = missing = 0
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"participant_role", "started_at", "completed_at", "mapping_corrections",
                    "evidence_traceability_issue", "release_reproducible",
                    "friction_note", "consented_for_aggregate"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise V17Error("V17-E060", "Pilot CSV is missing required columns: " +
                           ", ".join(sorted(required - set(reader.fieldnames or []))))
        for line, row in enumerate(reader, start=2):
            consent = clean(row.get("consented_for_aggregate")).casefold()
            if consent not in {"yes", "true", "1"}:
                excluded += 1
                continue
            missing += sum(not clean(row.get(field)) for field in
                           ("participant_role", "started_at", "completed_at", "mapping_corrections",
                            "evidence_traceability_issue", "release_reproducible"))
            start_text, end_text = clean(row.get("started_at")), clean(row.get("completed_at"))
            if start_text and end_text:
                try:
                    start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
                    end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
                    if start.tzinfo is None or end.tzinfo is None or end < start:
                        raise ValueError
                    durations.append((end - start).total_seconds() / 60.0)
                except ValueError:
                    raise V17Error("V17-E061", f"Pilot CSV line {line} has invalid timezone-aware timestamps")
            try:
                value = clean(row.get("mapping_corrections"))
                if value:
                    parsed = int(value)
                    if parsed < 0:
                        raise ValueError
                    corrections += parsed
            except ValueError:
                raise V17Error("V17-E062", f"Pilot CSV line {line} has invalid mapping_corrections")
            if clean(row.get("evidence_traceability_issue")).casefold() in {"yes", "true", "1"}:
                traceability += 1
            response = clean(row.get("release_reproducible")).casefold()
            if response in {"yes", "true", "1", "no", "false", "0"}:
                reproducibility_responses += 1
                reproducible += int(response in {"yes", "true", "1"})
            if clean(row.get("friction_note")):
                friction += 1
            role = clean(row.get("participant_role")) or "unspecified"
            role_counts[role] += 1
    return {"schema_version": 1, "consented_task_rows": sum(role_counts.values()),
            "excluded_without_aggregate_consent": excluded, "rows_with_missing_fields": missing,
            "median_duration_minutes": round(median(durations), 2) if durations else None,
            "minimum_duration_minutes": round(min(durations), 2) if durations else None,
            "maximum_duration_minutes": round(max(durations), 2) if durations else None,
            "mapping_corrections": corrections, "tasks_with_traceability_issues": traceability,
            "release_reproducibility_rate": round(reproducible / reproducibility_responses, 4)
                if reproducibility_responses else None,
            "tasks_with_friction_notes": friction, "task_rows_by_role": dict(sorted(role_counts.items())),
            "privacy_note": "Only rows explicitly marked consented_for_aggregate=yes are included; no participant identifiers are collected."}


def analyze_curriculum(curriculum_payload: Any, cards_payload: Any, mapping_payload: Any = None,
                       assessments_payload: Any = None, osce_payload: Any = None,
                       previous_curriculum_payload: Any = None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    specialty, competencies = load_curriculum(curriculum_payload)
    card_rows = records(cards_payload, "cards", "Cards")
    cards: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(card_rows):
        cid = clean(row.get("card_id") or row.get("id"))
        if not cid or cid.casefold() in {key.casefold() for key in cards}:
            raise V17Error("V17-E032", f"Card {index} has a missing or duplicate ID")
        if clean(row.get("specialty")).casefold() not in {"", specialty}:
            raise V17Error("V17-E033", f"Card '{cid}' belongs to a different specialty")
        cards[cid] = row

    comp_by_id = {row["id"].casefold(): row for row in competencies}
    mapping_rows: list[dict[str, Any]] = []
    if mapping_payload is not None:
        map_data, _ = unwrap_document(mapping_payload)
        if isinstance(map_data, dict) and not isinstance(map_data.get("candidates"), list):
            raise V17Error("V17-E034", "Mapping report must contain a candidates list")
        mapping_rows = records(map_data, "candidates", "Mapping candidates")
    else:
        for card_id, card in cards.items():
            for comp_id in card.get("competency_ids", []):
                approvals = card.get("faculty_approvals", [])
                approved = next((a for a in approvals if isinstance(a, dict)
                                 and clean(a.get("competency_id")).casefold() == clean(comp_id).casefold()
                                 and clean(a.get("status")).casefold() == "approved"), None)
                mapping_rows.append({"card_id": card_id, "competency_id": comp_id,
                                     "status": "verified" if approved else "suggested",
                                     "method": "faculty-approved" if approved else "card-explicit",
                                     "reviewer": clean((approved or {}).get("reviewer")),
                                     "reviewed_at": clean((approved or {}).get("reviewed_at")),
                                     "rationale": "Imported from card competency_ids and approval metadata"})

    verified: dict[str, set[str]] = defaultdict(set)
    candidate_counts: dict[str, int] = defaultdict(int)
    mapping_seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    queue: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for index, row in enumerate(mapping_rows):
        card_id = clean(row.get("card_id"))
        comp_id = clean(row.get("competency_id"))
        status = clean(row.get("status")).casefold()
        if card_id not in cards or comp_id.casefold() not in comp_by_id:
            findings.append({"code": "V17-M001", "severity": "error", "mapping_index": index,
                             "message": "Mapping references an unknown card or competency"})
            continue
        if status not in {"verified", "suggested", "pending", "invalid", "conflicting"}:
            findings.append({"code": "V17-M002", "severity": "error", "mapping_index": index,
                             "message": f"Unsupported mapping status '{status}'"})
            continue
        pair = (card_id.casefold(), comp_id.casefold())
        mapping_seen[pair].add(status)
        if status == "verified":
            if clean(row.get("method")).casefold() != "faculty-approved" or not clean(row.get("reviewer")) or not timezone_aware(row.get("reviewed_at")):
                findings.append({"code": "V17-M003", "severity": "warning", "card_id": card_id,
                                 "competency_id": comp_id,
                                 "message": "Verified label lacks faculty-approved method, reviewer label or timezone-aware review timestamp; excluded from verified coverage"})
                queue.append({"work_type": "mapping_provenance", "card_id": card_id,
                              "competency_id": comp_id, "reason": "Complete and verify approval provenance"})
                candidate_counts[comp_id.casefold()] += 1
            else:
                verified[comp_id.casefold()].add(card_id)
        elif status in {"suggested", "pending"}:
            candidate_counts[comp_id.casefold()] += 1
            queue.append({"work_type": "mapping_review", "card_id": card_id,
                          "competency_id": comp_id, "status": status,
                          "reason": "Faculty review required; not counted as verified coverage"})
        else:
            findings.append({"code": "V17-M004", "severity": "warning", "card_id": card_id,
                             "competency_id": comp_id, "message": f"Mapping status requires repair: {status}"})
            queue.append({"work_type": "mapping_repair", "card_id": card_id,
                          "competency_id": comp_id, "status": status,
                          "reason": "Resolve invalid or conflicting mapping"})
    for (card_key, comp_key), statuses in mapping_seen.items():
        if "verified" in statuses and len(statuses) > 1:
            findings.append({"code": "V17-M005", "severity": "error", "card_id": next(k for k in cards if k.casefold() == card_key),
                             "competency_id": comp_by_id[comp_key]["id"],
                             "message": "Conflicting mapping records exist for the same card and competency"})
            verified[comp_key].discard(next(k for k in cards if k.casefold() == card_key))

    for comp in competencies:
        if comp["required"] and not verified[comp["id"].casefold()]:
            queue.append({"work_type": "curriculum_gap", "competency_id": comp["id"],
                          "reason": "Required competency has no verified mapped teaching card"})
    for card_id in cards:
        if not any(card_id in values for values in verified.values()):
            queue.append({"work_type": "unmapped_card", "card_id": card_id,
                          "reason": "Card has no verified curriculum mapping"})

    assessments = records(assessments_payload, "items", "Assessments") if assessments_payload is not None else []
    osce_rows = records(osce_payload, "stations", "OSCE stations") if osce_payload is not None else []
    def artifact_links(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
        links: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            item_id = clean(row.get("item_id") or row.get("station_id") or row.get("id"))
            for comp_id in row.get("competency_ids", []) if isinstance(row.get("competency_ids", []), list) else []:
                links[clean(comp_id).casefold()].add(item_id)
        return links
    assessment_links, osce_links = artifact_links(assessments), artifact_links(osce_rows)

    by_domain: dict[str, dict[str, Any]] = {}
    by_level: dict[str, dict[str, Any]] = {}
    by_objective: dict[str, dict[str, Any]] = {}
    comp_report = []
    for comp in competencies:
        cid = comp["id"].casefold()
        covered = bool(verified[cid])
        item = {"competency_id": comp["id"], "title": comp["title"], "domain": comp["domain"],
                "cognitive_level": comp["level"], "required": comp["required"],
                "weight": comp["weight"], "verified_card_ids": sorted(verified[cid]),
                "suggested_mapping_count": candidate_counts[cid], "verified": covered,
                "assessment_item_ids": sorted(assessment_links[cid]),
                "osce_station_ids": sorted(osce_links[cid]),
                "learning_objectives": comp["objectives"]}
        comp_report.append(item)
        for dimension, key in (("domain", comp["domain"]), ("cognitive_level", comp["level"])):
            bucket = by_domain if dimension == "domain" else by_level
            entry = bucket.setdefault(key, {"total": 0, "verified": 0, "required": 0, "required_verified": 0})
            entry["total"] += 1
            entry["verified"] += int(covered)
            entry["required"] += int(comp["required"])
            entry["required_verified"] += int(comp["required"] and covered)
        for objective in comp["objectives"]:
            entry = by_objective.setdefault(objective, {"competency_ids": [], "verified_competency_count": 0})
            entry["competency_ids"].append(comp["id"])
            entry["verified_competency_count"] += int(covered)

    duplicate_groups = find_exact_duplicate_cards(cards)
    for group in duplicate_groups:
        queue.append({"work_type": "duplicate_content_review", "card_ids": group["card_ids"],
                      "reason": "Exact normalized prompt duplicate candidate; human review required"})
    queue_summary: dict[str, int] = defaultdict(int)
    for item in queue:
        queue_summary[item["work_type"]] += 1
    graph_nodes = [{"id": "competency:" + row["competency_id"], "type": "competency",
                    "label": row["title"]} for row in comp_report]
    graph_edges = []
    for row in comp_report:
        comp_node = "competency:" + row["competency_id"]
        for card_id in row["verified_card_ids"]:
            graph_nodes.append({"id": "card:" + card_id, "type": "card", "label": card_id})
            graph_edges.append({"from": comp_node, "to": "card:" + card_id, "relationship": "verified_teaching"})
        for objective in row["learning_objectives"]:
            objective_node = "objective:" + objective
            graph_nodes.append({"id": objective_node, "type": "learning_objective", "label": objective})
            graph_edges.append({"from": comp_node, "to": objective_node, "relationship": "supports"})
        for item_id in row["assessment_item_ids"]:
            graph_nodes.append({"id": "assessment:" + item_id, "type": "assessment", "label": item_id})
            graph_edges.append({"from": comp_node, "to": "assessment:" + item_id, "relationship": "assessed_by"})
        for station_id in row["osce_station_ids"]:
            graph_nodes.append({"id": "osce:" + station_id, "type": "osce_station", "label": station_id})
            graph_edges.append({"from": comp_node, "to": "osce:" + station_id, "relationship": "assessed_by"})
    graph_nodes = list({row["id"]: row for row in graph_nodes}.values())
    required = [row for row in competencies if row["required"]]
    verified_required = sum(bool(verified[row["id"].casefold()]) for row in required)
    total_weight = sum(row["weight"] for row in required)
    verified_weight = sum(row["weight"] for row in required if verified[row["id"].casefold()])
    conflict_count = sum(1 for f in findings if f["code"] == "V17-M005")
    quality = {
        "schema_version": 1, "usiphus_version": VERSION, "specialty": specialty,
        "summary": {"cards_evaluated": len(cards), "competencies_evaluated": len(competencies),
                    "required_competencies": len(required), "verified_required_competencies": verified_required,
                    "missing_required_competencies": len(required) - verified_required,
                    "verified_weighted_coverage": round(verified_weight / total_weight, 4) if total_weight else 0.0,
                    "verified_coverage": round(verified_required / len(required), 4) if required else 0.0,
                    "mapping_candidates": sum(candidate_counts.values()), "mapping_conflicts": conflict_count,
                    "exact_duplicate_groups": len(duplicate_groups),
                    "assessment_items": len(assessments), "osce_stations": len(osce_rows)},
        "competencies": comp_report, "by_domain": by_domain, "by_cognitive_level": by_level,
        "by_learning_objective": by_objective, "dependency_graph": {"nodes": graph_nodes, "edges": graph_edges},
        "review_queue_summary": dict(sorted(queue_summary.items())),
        "possible_duplicate_cards": duplicate_groups, "findings": findings,
        "limitations": ["Coverage is metadata completeness, not clinical correctness or educational effectiveness.",
                        "Only explicit faculty-approved provenance counts as verified; reviewer labels are not authenticated identities.",
                        "Suggested and pending mappings are excluded from verified coverage."]
    }
    impact = build_impact_report(previous_curriculum_payload, specialty, competencies, mapping_rows,
                                 cards, assessments, osce_rows)
    return quality, impact, queue


def build_impact_report(previous_payload: Any, specialty: str, current: list[dict[str, Any]],
                        mapping_rows: list[dict[str, Any]], cards: dict[str, dict[str, Any]],
                        assessments: list[dict[str, Any]], osce_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if previous_payload is None:
        return {"schema_version": 1, "comparison_available": False,
                "message": "Provide --previous-curriculum to calculate curriculum change impact.",
                "changes": [], "affected_artifacts": {}}
    old_specialty, old_rows = load_curriculum(previous_payload)
    if old_specialty != specialty:
        raise V17Error("V17-E040", "Previous and current curriculum specialties differ")
    old = {row["id"].casefold(): row for row in old_rows}
    new = {row["id"].casefold(): row for row in current}
    changes = []
    changed_ids: set[str] = set()
    for cid in sorted(set(old) | set(new)):
        if cid not in old:
            kind, fields = "added", ["competency"]
        elif cid not in new:
            kind, fields = "removed", ["competency"]
        else:
            fields = [field for field in ("title", "domain", "level", "required", "weight", "objectives")
                      if old[cid][field] != new[cid][field]]
            kind = "modified" if fields else ""
        if kind:
            changed_ids.add(cid)
            changes.append({"competency_id": (new.get(cid) or old[cid])["id"],
                            "change_type": kind, "changed_fields": fields})
    card_ids = set()
    for row in mapping_rows:
        if clean(row.get("competency_id")).casefold() in changed_ids:
            card_id = clean(row.get("card_id"))
            if card_id in cards:
                card_ids.add(card_id)
    def linked(rows: list[dict[str, Any]], id_fields: tuple[str, ...]) -> list[str]:
        found = set()
        for row in rows:
            refs = row.get("competency_ids", [])
            if isinstance(refs, list) and any(clean(ref).casefold() in changed_ids for ref in refs):
                found.add(next((clean(row.get(field)) for field in id_fields if clean(row.get(field))), "unknown"))
        return sorted(found)
    return {"schema_version": 1, "comparison_available": True, "specialty": specialty,
            "previous_competency_count": len(old), "current_competency_count": len(new),
            "changes": changes,
            "affected_artifacts": {"cards": sorted(card_ids),
                                   "assessment_items": linked(assessments, ("item_id", "id")),
                                   "osce_stations": linked(osce_rows, ("station_id", "id"))},
            "review_required": bool(changes),
            "limitations": ["Impact is based on explicit competency links; unlinked downstream artifacts cannot be discovered.",
                            "A curriculum change creates review candidates only and never edits clinical or educational content."]}


def compare_release_manifests(previous: Any, current: Any) -> dict[str, Any]:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise V17Error("V17-E050", "Both release manifests must be JSON objects")
    def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        result = {}
        if isinstance(value, dict):
            for key, item in value.items():
                result.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        else:
            result[prefix] = value
        return result
    left, right = flatten(previous), flatten(current)
    changed = [{"path": key, "previous": left.get(key), "current": right.get(key)}
               for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)]
    return {"schema_version": 1, "changed_field_count": len(changed), "changes": changed,
            "note": "Manifest differences describe recorded metadata and hashes; they do not establish content quality."}


def dashboard_html(quality: dict[str, Any], impact: dict[str, Any]) -> str:
    summary = quality["summary"]
    rows = []
    for item in quality["competencies"]:
        state = "Verified" if item["verified"] else "Gap"
        rows.append("<tr><td>" + html.escape(item["competency_id"]) + "</td><td>" +
                    html.escape(item["title"]) + "</td><td>" + html.escape(item["domain"]) +
                    "</td><td>" + state + "</td><td>" +
                    html.escape(", ".join(item["verified_card_ids"])) + "</td></tr>")
    changes = "".join("<li>" + html.escape(row["competency_id"] + " — " + row["change_type"] +
                    " (" + ", ".join(row["changed_fields"]) + ")") + "</li>"
                    for row in impact.get("changes", [])) or "<li>No comparison supplied or no changes detected.</li>"
    percent = round(summary["verified_coverage"] * 100)
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Usiphus V17 Curriculum Quality</title>
<style>
body{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#202938}
h1,h2{line-height:1.2} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.metric{border:1px solid #cbd5e1;border-radius:10px;padding:14px}.metric strong{display:block;font-size:1.8rem}
.track{height:12px;background:#e2e8f0;border-radius:8px;overflow:hidden}.fill{height:100%;background:#16856b;width:""" + str(percent) + """%}
table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{text-align:left;padding:9px;border-bottom:1px solid #cbd5e1}
small,.muted{color:#526174} @media(max-width:600px){table{font-size:13px}}
</style></head><body>
<h1>Usiphus V17 — Curriculum Quality</h1><p class="muted">Specialty: """ + html.escape(quality["specialty"]) + """</p>
<div class="grid">
<div class="metric"><strong>""" + str(summary["verified_required_competencies"]) + "/" + str(summary["required_competencies"]) + """</strong><span>Verified required competencies</span></div>
<div class="metric"><strong>""" + str(summary["missing_required_competencies"]) + """</strong><span>Missing required competencies</span></div>
<div class="metric"><strong>""" + str(summary["mapping_candidates"]) + """</strong><span>Unverified mapping candidates</span></div>
<div class="metric"><strong>""" + str(summary["mapping_conflicts"]) + """</strong><span>Mapping conflicts</span></div></div>
<h2>Verified required coverage</h2><div class="track" role="img" aria-label="Verified coverage """ + str(percent) + """ percent"><div class="fill"></div></div><p>""" + str(percent) + """% — metadata coverage only, not educational effectiveness.</p>
<h2>Competency coverage</h2><table><thead><tr><th>ID</th><th>Competency</th><th>Domain</th><th>Status</th><th>Verified cards</th></tr></thead><tbody>""" + "".join(rows) + """</tbody></table>
<h2>Curriculum changes</h2><ul>""" + changes + """</ul>
<p class="muted">Machine-generated findings are review aids. Faculty retain responsibility for curriculum meaning, clinical accuracy and approval.</p>
</body></html>"""


def write_pilot_template(path: Path) -> None:
    header = ["workflow_id", "participant_role", "task", "started_at", "completed_at",
              "mapping_corrections", "evidence_traceability_issue", "release_reproducible",
              "friction_note", "consented_for_aggregate"]
    from io import StringIO
    stream = StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    atomic_write_text(stream.getvalue(), path)


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    paths = {"curriculum": Path(args.curriculum), "cards": Path(args.cards)}
    for key in ("mapping", "assessments", "osce", "previous_curriculum", "pilot_metrics"):
        value = getattr(args, key, None)
        if value:
            paths[key] = Path(value)
    payloads = {key: read_json(path) for key, path in paths.items() if key != "pilot_metrics"}
    pilot_summary = summarize_pilot_metrics(paths["pilot_metrics"]) if "pilot_metrics" in paths else None
    quality, impact, queue = analyze_curriculum(
        payloads["curriculum"], payloads["cards"], payloads.get("mapping"),
        payloads.get("assessments"), payloads.get("osce"), payloads.get("previous_curriculum"))
    out = Path(args.output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    outputs = {
        "v17_curriculum_quality.json": quality,
        "v17_curriculum_impact.json": impact,
        "v17_faculty_review_queue.json": {"schema_version": 1, "items": queue},
    }
    if pilot_summary is not None:
        outputs["v17_pilot_metrics_summary.json"] = pilot_summary
    for name, payload in outputs.items():
        write_json(out / name, payload)
    atomic_write_text(dashboard_html(quality, impact), out / "v17_dashboard.html")
    write_pilot_template(out / "v17_pilot_metrics_template.csv")
    manifest = {"schema_version": 1, "usiphus_version": VERSION,
                "inputs": {key: {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
                           for key, path in sorted(paths.items())},
                "outputs": {}, "summary": quality["summary"],
                "limitations": ["This is an analysis report, not a governed V16 release.",
                                "Faculty/learner pilot validation must be performed by the institution."]}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name != "v17_manifest.json":
            manifest["outputs"][path.name] = {"sha256": digest(path), "bytes": path.stat().st_size}
    write_json(out / "v17_manifest.json", manifest)
    return {"status": "complete", "output_dir": str(out), "summary": quality["summary"],
            "review_queue_items": len(queue), "impact_comparison_available": impact["comparison_available"]}


def self_test() -> int:
    import unittest
    class V17Tests(unittest.TestCase):
        def setUp(self):
            self.curriculum = {"specialty": "urology", "competencies": [
                {"id": "U1", "title": "Retention", "domain": "Emergency", "level": "apply",
                 "required": True, "weight": 2, "learning_objectives": ["Recognize retention"]},
                {"id": "U2", "title": "Stones", "domain": "Endourology", "level": "understand",
                 "required": True, "weight": 1, "learning_objectives": ["Explain stones"]}]}
            self.cards = [{"card_id": "C1", "specialty": "urology", "prompt": "Acute retention"}]
            self.mapping = {"candidates": [
                {"card_id": "C1", "competency_id": "U1", "status": "verified",
                 "method": "faculty-approved", "reviewer": "reviewer-label",
                 "reviewed_at": "2026-01-01T10:00:00Z"}]}
        def test_contract_rejects_duplicate_ids(self):
            result = validate_contract("card", [{"card_id": "x", "prompt": "a"},
                                                {"card_id": "X", "prompt": "b"}])
            self.assertFalse(result["valid"])
        def test_contract_reports_actionable_errors(self):
            result = validate_contract("assessment", {"item_id": 2})
            self.assertFalse(result["valid"])
            self.assertTrue(any("item_id must be a string" in x for x in result["errors"]))
        def test_non_destructive_migration(self):
            original = {"specialty": "urology", "competencies": []}
            migrated = migrate_document("curriculum", original)
            self.assertEqual(migrated["data"], original)
            self.assertFalse(migrated["migration"]["meaning_changed"])
        def test_unversioned_legacy_warns(self):
            result = validate_contract("card", {"card_id": "c", "prompt": "p"})
            self.assertTrue(result["valid"])
            self.assertTrue(result["warnings"])
        def test_future_version_rejected(self):
            with self.assertRaises(V17Error):
                validate_contract("card", {"schema_version": 99, "card_id": "c", "prompt": "p"})
        def test_only_faculty_provenance_counts(self):
            quality, _, queue = analyze_curriculum(self.curriculum, self.cards, self.mapping)
            self.assertEqual(quality["summary"]["verified_required_competencies"], 1)
            self.assertEqual(quality["summary"]["missing_required_competencies"], 1)
            self.assertTrue(any(x["work_type"] == "curriculum_gap" for x in queue))
        def test_verified_label_without_provenance_is_excluded(self):
            bad = {"candidates": [{"card_id": "C1", "competency_id": "U1", "status": "verified"}]}
            quality, _, _ = analyze_curriculum(self.curriculum, self.cards, bad)
            self.assertEqual(quality["summary"]["verified_required_competencies"], 0)
        def test_mapping_conflict_excludes_coverage(self):
            rows = {"candidates": self.mapping["candidates"] + [
                {"card_id": "C1", "competency_id": "U1", "status": "pending"}]}
            quality, _, _ = analyze_curriculum(self.curriculum, self.cards, rows)
            self.assertEqual(quality["summary"]["verified_required_competencies"], 0)
            self.assertEqual(quality["summary"]["mapping_conflicts"], 1)
        def test_curriculum_impact_links_downstream_artifacts(self):
            old = {"specialty": "urology", "competencies": [{"id": "U1", "title": "Old"}]}
            new = {"specialty": "urology", "competencies": [{"id": "U1", "title": "New"}]}
            assessments = [{"item_id": "Q1", "competency_ids": ["U1"]}]
            osce = [{"station_id": "S1", "competency_ids": ["U1"]}]
            _, impact, _ = analyze_curriculum(new, self.cards, self.mapping, assessments, osce, old)
            self.assertEqual(impact["affected_artifacts"]["cards"], ["C1"])
            self.assertEqual(impact["affected_artifacts"]["assessment_items"], ["Q1"])
            self.assertEqual(impact["affected_artifacts"]["osce_stations"], ["S1"])
        def test_added_and_removed_competencies(self):
            old = {"specialty": "urology", "competencies": [{"id": "OLD"}]}
            new = {"specialty": "urology", "competencies": [{"id": "NEW"}]}
            _, impact, _ = analyze_curriculum(new, self.cards, {"candidates": []},
                                               previous_curriculum_payload=old)
            self.assertEqual({x["change_type"] for x in impact["changes"]}, {"added", "removed"})
        def test_release_manifest_diff(self):
            result = compare_release_manifests({"outputs": {"a": "1"}}, {"outputs": {"a": "2"}})
            self.assertEqual(result["changed_field_count"], 1)
        def test_bad_specialty_is_rejected(self):
            with self.assertRaises(V17Error):
                analyze_curriculum(self.curriculum, [{"card_id": "C2", "specialty": "cardiology"}])
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(V17Tests))
    return 0 if result.wasSuccessful() else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Usiphus V17 stable contracts and curriculum intelligence")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--validate", help="Validate a single JSON document against a versioned contract")
    parser.add_argument("--contract-type", choices=sorted(CONTRACTS))
    parser.add_argument("--migrate", help="Wrap a validated legacy document in a non-destructive V17 contract envelope")
    parser.add_argument("--migration-output")
    parser.add_argument("--curriculum")
    parser.add_argument("--cards")
    parser.add_argument("--mapping")
    parser.add_argument("--assessments")
    parser.add_argument("--osce")
    parser.add_argument("--pilot-metrics", help="Summarize institution-approved aggregate pilot CSV rows")
    parser.add_argument("--previous-curriculum")
    parser.add_argument("--compare-manifests", nargs=2, metavar=("PREVIOUS", "CURRENT"))
    parser.add_argument("--output-dir", default="build-v17")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    if args.self_test:
        return self_test()
    try:
        if args.compare_manifests:
            result = compare_release_manifests(read_json(Path(args.compare_manifests[0])),
                                                read_json(Path(args.compare_manifests[1])))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.validate or args.migrate:
            if not args.contract_type:
                parser.error("--contract-type is required with --validate or --migrate")
            source = args.migrate or args.validate
            payload = read_json(Path(source))
            if args.migrate:
                if not args.migration_output:
                    parser.error("--migration-output is required with --migrate")
                migrated = migrate_document(args.contract_type, payload)
                write_json(Path(args.migration_output), migrated)
                print(json.dumps({"status": "migrated", "output": args.migration_output,
                                  "meaning_changed": False}, indent=2))
                return 0
            result = validate_contract(args.contract_type, payload)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["valid"] else 1
        if not args.curriculum or not args.cards:
            parser.error("--curriculum and --cards are required for curriculum analysis")
        result = run_analysis(args)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except V17Error as exc:
        LOG.error("%s: %s", exc.code, exc)
        return 2
    except (OSError, ValueError, TypeError) as exc:
        LOG.error("V17-E999: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
