#!/usr/bin/env python3
"""Usiphus V18 — local-first evidence lifecycle operations.

V18 observes and routes evidence lifecycle events. It never fetches URLs by
default, grades clinical authority, changes source status, or edits claims.
All change and impact outputs are review candidates for qualified humans.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

VERSION = "18.0.0"
MAX_BYTES = 50 * 1024 * 1024
CHANGE_WEIGHTS = {
    "withdrawn_source": 1000,
    "superseded_source": 900,
    "jurisdiction_mismatch": 850,
    "source_content_changed": 800,
    "source_version_changed": 750,
    "source_status_changed": 700,
    "source_metadata_changed": 500,
    "review_overdue": 400,
    "review_date_missing": 350,
    "new_source": 100,
}
VALID_DECISIONS = {"accepted", "rejected", "deferred"}


class V18Error(ValueError):
    """Invalid V18 input or policy."""


def clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return digest_bytes(canonical_json(value).encode("utf-8"))


def read_json(path: Path) -> Any:
    path = Path(path)
    if not path.is_file():
        raise V18Error(f"Input file not found: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise V18Error(f"Input exceeds {MAX_BYTES // (1024 * 1024)} MiB: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V18Error(f"Invalid JSON in {path}: {exc}") from exc


def write_text(text: str, destination: Path) -> None:
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


def write_json(value: Any, destination: Path) -> None:
    write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", destination)


def records(payload: Any, key: str, label: str) -> list[dict[str, Any]]:
    rows = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise V18Error(f"{label} must be a list of objects or contain a '{key}' list")
    return rows


def parse_timestamp(value: Any, label: str, *, optional: bool = True) -> datetime | None:
    raw = clean(value)
    if not raw and optional:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise V18Error(f"{label} must be a timezone-aware ISO-8601 timestamp")


def canonical_url(value: str) -> str:
    raw = clean(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
            return ""
        host = parts.hostname.casefold()
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        port = parts.port
        netloc = host + (f":{port}" if port and not (
            parts.scheme.casefold() == "http" and port == 80 or
            parts.scheme.casefold() == "https" and port == 443) else "")
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parts.scheme.casefold(), netloc, path, parts.query, ""))
    except ValueError:
        return ""


def contained_file(root: Path, relative: str) -> Path:
    base = Path(root).resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise V18Error(f"Source content path escapes the approved source directory: {relative}") from exc
    if not candidate.is_file():
        raise V18Error(f"Source content file not found: {relative}")
    if candidate.stat().st_size > MAX_BYTES:
        raise V18Error(f"Source content exceeds {MAX_BYTES // (1024 * 1024)} MiB: {relative}")
    return candidate


def source_fingerprint(source: dict[str, Any], source_root: Path) -> dict[str, Any]:
    content_hash = clean(source.get("content_sha256")).casefold()
    content_path = clean(source.get("content_path"))
    if content_path:
        path = contained_file(source_root, content_path)
        actual = digest_bytes(path.read_bytes())
        if content_hash and content_hash != actual:
            raise V18Error(f"Provided content_sha256 does not match content_path for {source.get('source_id')}")
        content_hash = actual
    if content_hash and not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise V18Error(f"Invalid SHA-256 content hash for {source.get('source_id')}")
    url = canonical_url(clean(source.get("url") or source.get("canonical_url")))
    return {
        "source_id": clean(source.get("source_id")),
        "title": clean(source.get("title")),
        "publisher": clean(source.get("publisher")),
        "authority": clean(source.get("authority")),
        "retrieved_at": clean(source.get("retrieved_at")),
        "jurisdiction": clean(source.get("jurisdiction")),
        "source_type": clean(source.get("source_type")),
        "version": clean(source.get("version")),
        "status": clean(source.get("status") or "active").casefold(),
        "canonical_url": url,
        "content_sha256": content_hash,
        "published_on": clean(source.get("published_on")),
        "last_reviewed_at": clean(source.get("last_reviewed_at") or source.get("reviewed_at")),
        "review_due_days": source.get("review_due_days", 365),
    }


def validate_source(source: dict[str, Any], index: int) -> list[str]:
    issues = []
    sid = clean(source.get("source_id"))
    if not sid:
        issues.append("source_id is required")
    for field in ("title", "jurisdiction", "source_type"):
        if not clean(source.get(field)):
            issues.append(f"{field} is required")
    status = clean(source.get("status") or "active").casefold()
    if status not in {"active", "superseded", "withdrawn"}:
        issues.append(f"unsupported lifecycle status: {status}")
    if status == "superseded" and not clean(source.get("superseded_by")):
        issues.append("superseded source requires superseded_by")
    if source.get("published_on"):
        try:
            date.fromisoformat(clean(source["published_on"]))
        except ValueError:
            issues.append("published_on must be YYYY-MM-DD")
    due = source.get("review_due_days", 365)
    if isinstance(due, bool) or not isinstance(due, int) or due < 1:
        issues.append("review_due_days must be a positive integer")
    if source.get("retrieved_at"):
        try:
            parse_timestamp(source.get("retrieved_at"), f"source row {index + 1} retrieved_at", optional=False)
        except V18Error as exc:
            issues.append(str(exc))
    if source.get("last_reviewed_at") or source.get("reviewed_at"):
        try:
            parse_timestamp(source.get("last_reviewed_at") or source.get("reviewed_at"),
                            f"source row {index + 1} last_reviewed_at", optional=False)
        except V18Error as exc:
            issues.append(str(exc))
    return issues


def load_previous_state(payload: Any) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise V18Error("previous V18 state must have schema_version=1")
    sources = payload.get("sources", {})
    if not isinstance(sources, dict):
        raise V18Error("previous V18 state sources must be an object keyed by source_id")
    return {clean(key): value for key, value in sources.items() if isinstance(value, dict)}


def analyze(sources: list[dict[str, Any]], claims: list[dict[str, Any]], *,
            source_root: Path, as_of: datetime, previous_state: Any = None,
            cards: list[dict[str, Any]] | None = None,
            assessments: list[dict[str, Any]] | None = None,
            osce: list[dict[str, Any]] | None = None,
            decisions: list[dict[str, Any]] | None = None,
            max_queue_items: int = 5000) -> dict[str, Any]:
    previous = load_previous_state(previous_state)
    source_by_id: dict[str, dict[str, Any]] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    duplicate_index: dict[str, list[str]] = defaultdict(list)
    supersession: dict[str, str] = {}

    for index, source in enumerate(sources):
        issues = validate_source(source, index)
        sid = clean(source.get("source_id"))
        if sid and sid in source_by_id:
            issues.append("duplicate source_id")
        elif sid:
            source_by_id[sid] = source
        if sid:
            fingerprints[sid] = source_fingerprint(source, source_root)
            if fingerprints[sid]["canonical_url"]:
                duplicate_index["url:" + fingerprints[sid]["canonical_url"]].append(sid)
            if fingerprints[sid]["content_sha256"]:
                duplicate_index["sha256:" + fingerprints[sid]["content_sha256"]].append(sid)
            successor = clean(source.get("superseded_by"))
            if successor:
                supersession[sid] = successor
        if issues:
            findings.append({"kind": "source", "id": sid or f"row-{index + 1}", "issues": issues})

    for sid, successor in supersession.items():
        if successor not in source_by_id:
            findings.append({"kind": "source", "id": sid,
                             "issues": [f"superseded_by references unknown source: {successor}"]})
        visited: set[str] = set()
        current = sid
        while current in supersession:
            if current in visited:
                findings.append({"kind": "source", "id": sid, "issues": ["supersession cycle detected"]})
                break
            visited.add(current)
            current = supersession[current]

    duplicate_candidates = []
    for key, ids in sorted(duplicate_index.items()):
        unique = sorted(set(ids))
        if len(unique) > 1:
            duplicate_candidates.append({
                "candidate_type": "canonical_url" if key.startswith("url:") else "identical_content_hash",
                "source_ids": unique, "review_required": True,
                "note": "Candidate only; do not merge source records or infer equivalence automatically.",
            })

    card_by_id = {clean(row.get("card_id") or row.get("id")): row
                  for row in (cards or []) if clean(row.get("card_id") or row.get("id"))}
    assessment_by_id = {clean(row.get("item_id") or row.get("assessment_id")): row
                        for row in (assessments or []) if clean(row.get("item_id") or row.get("assessment_id"))}
    osce_by_id = {clean(row.get("station_id") or row.get("osce_id")): row
                  for row in (osce or []) if clean(row.get("station_id") or row.get("osce_id"))}
    source_claims: dict[str, set[str]] = defaultdict(set)
    claim_card: dict[str, str] = {}
    claim_source_ids: dict[str, set[str]] = defaultdict(set)
    jurisdiction_mismatches: list[dict[str, Any]] = []

    for index, claim in enumerate(claims):
        cid, card_id = clean(claim.get("claim_id")), clean(claim.get("card_id"))
        if not cid:
            findings.append({"kind": "claim", "id": f"row-{index + 1}", "issues": ["claim_id is required"]})
            continue
        claim_card[cid] = card_id
        refs = claim.get("references")
        if refs is None:
            legacy = clean(claim.get("reference"))
            refs = [{"legacy_reference": legacy}] if legacy else []
        if not isinstance(refs, list):
            findings.append({"kind": "claim", "id": cid, "issues": ["references must be a list"]})
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                findings.append({"kind": "claim", "id": cid, "issues": ["reference must be an object"]})
                continue
            sid = clean(ref.get("source_id"))
            if not sid:
                if ref.get("legacy_reference"):
                    findings.append({"kind": "claim", "id": cid,
                                     "issues": ["legacy free-text reference needs manual source registration"]})
                continue
            claim_source_ids[cid].add(sid)
            source_claims[sid].add(cid)
            source = source_by_id.get(sid)
            if source is None:
                findings.append({"kind": "claim", "id": cid,
                                 "issues": [f"unknown source_id: {sid}"]})
                continue
            claim_jurisdiction = clean(ref.get("jurisdiction"))
            source_jurisdiction = clean(source.get("jurisdiction"))
            if claim_jurisdiction and source_jurisdiction and claim_jurisdiction.casefold() != source_jurisdiction.casefold():
                mismatch = {"claim_id": cid, "source_id": sid,
                            "claim_jurisdiction": claim_jurisdiction,
                            "source_jurisdiction": source_jurisdiction}
                jurisdiction_mismatches.append(mismatch)

    downstream: dict[str, dict[str, list[str]]] = {}
    for sid, claim_ids in source_claims.items():
        cards_hit = sorted({claim_card[cid] for cid in claim_ids if claim_card.get(cid)})
        competencies = sorted({clean(value) for card_id in cards_hit
                               for value in card_by_id.get(card_id, {}).get("competency_ids", []) if clean(value)})
        assessment_ids = sorted({item_id for item_id, row in assessment_by_id.items()
                                  if set(competencies).intersection(
                                      clean(value) for value in row.get("competency_ids", []))})
        osce_ids = sorted({station_id for station_id, row in osce_by_id.items()
                           if set(competencies).intersection(
                               clean(value) for value in row.get("competency_ids", []))})
        downstream[sid] = {"claims": sorted(claim_ids), "cards": cards_hit,
                           "competencies": competencies, "assessment_items": assessment_ids,
                           "osce_stations": osce_ids}

    change_events: list[dict[str, Any]] = []
    current_state: dict[str, dict[str, Any]] = {}
    for sid, fingerprint in sorted(fingerprints.items()):
        current_state[sid] = fingerprint
        old = previous.get(sid)
        source = source_by_id[sid]
        impact = downstream.get(sid, {"claims": [], "cards": [], "competencies": [],
                                       "assessment_items": [], "osce_stations": []})
        if old is None:
            change_events.append({"source_id": sid, "change_type": "new_source",
                                  "fields": ["source_registered"], "before": {}, "after": fingerprint})
            continue
        changed = []
        for field in ("title", "publisher", "authority", "jurisdiction", "source_type", "version",
                      "status", "canonical_url", "content_sha256", "published_on", "retrieved_at"):
            if clean(old.get(field)) != clean(fingerprint.get(field)):
                changed.append(field)
        if changed:
            event = {"source_id": sid, "change_type": "source_metadata_changed",
                     "fields": changed, "before": old, "after": fingerprint,
                     "affected_artifacts": impact}
            if "content_sha256" in changed:
                event["change_type"] = "source_content_changed"
            elif "version" in changed:
                event["change_type"] = "source_version_changed"
            elif "status" in changed:
                event["change_type"] = "source_status_changed"
            change_events.append(event)

    for sid, source in source_by_id.items():
        status = clean(source.get("status") or "active").casefold()
        impact = downstream.get(sid, {"claims": [], "cards": [], "competencies": [],
                                      "assessment_items": [], "osce_stations": []})
        if status == "withdrawn":
            queue.append(make_queue_item("withdrawn_source", sid, impact,
                                          "Source is marked withdrawn; faculty must assess affected claims and teaching artifacts."))
        elif status == "superseded":
            queue.append(make_queue_item("superseded_source", sid, impact,
                                          "Source is marked superseded; confirm successor applicability and update decisions manually."))
        review_raw = clean(source.get("last_reviewed_at") or source.get("reviewed_at"))
        if not review_raw:
            queue.append(make_queue_item("review_date_missing", sid, impact,
                                          "No source review timestamp is recorded."))
        else:
            reviewed = parse_timestamp(review_raw, f"source {sid} last_reviewed_at", optional=False)
            due_days = source.get("review_due_days", 365)
            age = (as_of - reviewed).days
            if age > due_days:
                queue.append(make_queue_item("review_overdue", sid, impact,
                                              f"Recorded review is {age} days old; configured interval is {due_days} days.",
                                              age_days=age, review_due_days=due_days))
        for event in (e for e in change_events if e["source_id"] == sid and e["change_type"] != "new_source"):
            kind = event["change_type"]
            queue.append(make_queue_item(kind, sid, impact,
                                          "Source fingerprint changed in: " + ", ".join(event["fields"]) + ".",
                                          changed_fields=event["fields"]))
    for mismatch in jurisdiction_mismatches:
        impact = downstream.get(mismatch["source_id"], {})
        queue.append(make_queue_item("jurisdiction_mismatch", mismatch["source_id"], impact,
                                      "Claim and source jurisdiction metadata differ; applicability requires human review.",
                                      claim_id=mismatch["claim_id"],
                                      claim_jurisdiction=mismatch["claim_jurisdiction"],
                                      source_jurisdiction=mismatch["source_jurisdiction"]))

    decision_map: dict[str, dict[str, Any]] = {}
    decision_rows = decisions or []
    for index, decision in enumerate(decision_rows):
        target = clean(decision.get("target_id"))
        decision_type = clean(decision.get("target_type")).casefold()
        state = clean(decision.get("decision")).casefold()
        reviewer = clean(decision.get("reviewer_label"))
        rationale = clean(decision.get("rationale"))
        timestamp = clean(decision.get("decided_at"))
        if decision_type not in {"source", "claim", "change"} or not target:
            raise V18Error(f"decision row {index + 1} requires target_type source/claim/change and target_id")
        if state not in VALID_DECISIONS or not reviewer or not rationale:
            raise V18Error(f"decision row {index + 1} requires accepted/rejected/deferred, reviewer_label and rationale")
        parse_timestamp(timestamp, f"decision row {index + 1} decided_at", optional=False)
        key = decision_type + ":" + target
        decision_map[key] = decision
    for item in queue:
        decision = decision_map.get("source:" + item["source_id"]) or decision_map.get("change:" + item["queue_id"])
        if decision:
            item["faculty_decision"] = {
                "decision": clean(decision["decision"]).casefold(),
                "reviewer_label": clean(decision["reviewer_label"]),
                "rationale": clean(decision["rationale"]),
                "decided_at": clean(decision["decided_at"]),
                "identity_assurance": "Reviewer label is user-supplied and not authenticated by V18.",
            }
        else:
            item["faculty_decision"] = None

    queue.sort(key=lambda row: (-row["priority_score"], row["source_id"], row["queue_id"]))
    if len(queue) > max_queue_items:
        raise V18Error(f"Review queue exceeds configured safety limit ({max_queue_items})")

    graph_nodes: dict[str, dict[str, Any]] = {}
    graph_edges: list[dict[str, str]] = []
    def add_node(node_id: str, kind: str, label: str) -> None:
        graph_nodes[node_id] = {"id": node_id, "type": kind, "label": label}
    def add_edge(left: str, right: str, relation: str) -> None:
        graph_edges.append({"from": left, "to": right, "relationship": relation})
    for sid, claim_ids in sorted(source_claims.items()):
        add_node("source:" + sid, "source", clean(source_by_id.get(sid, {}).get("title") or sid))
        for cid in sorted(claim_ids):
            add_node("claim:" + cid, "claim", cid)
            claim_decision = decision_map.get("claim:" + cid)
            if claim_decision:
                graph_nodes["claim:" + cid]["faculty_decision"] = {
                    "decision": clean(claim_decision.get("decision")).casefold(),
                    "reviewer_label": clean(claim_decision.get("reviewer_label")),
                    "rationale": clean(claim_decision.get("rationale")),
                    "decided_at": clean(claim_decision.get("decided_at")),
                    "identity_assurance": "Reviewer label is user-supplied and not authenticated by V18.",
                }
            add_edge("source:" + sid, "claim:" + cid, "supports")
            card_id = claim_card.get(cid, "")
            if card_id:
                add_node("card:" + card_id, "card", card_id)
                add_edge("claim:" + cid, "card:" + card_id, "appears_in")
                card = card_by_id.get(card_id, {})
                for competency in card.get("competency_ids", []):
                    competency = clean(competency)
                    if not competency:
                        continue
                    add_node("competency:" + competency, "competency", competency)
                    add_edge("card:" + card_id, "competency:" + competency, "mapped_to")
                    for item_id, row in assessment_by_id.items():
                        if competency in {clean(x) for x in row.get("competency_ids", [])}:
                            add_node("assessment:" + item_id, "assessment_item", item_id)
                            add_edge("competency:" + competency, "assessment:" + item_id, "assessed_by")
                    for station_id, row in osce_by_id.items():
                        if competency in {clean(x) for x in row.get("competency_ids", [])}:
                            add_node("osce:" + station_id, "osce_station", station_id)
                            add_edge("competency:" + competency, "osce:" + station_id, "assessed_by")

    audit_events = []
    previous_hash = "0" * 64
    for index, decision in enumerate(decision_rows):
        event = {"sequence": index + 1, "previous_hash": previous_hash,
                 "target_type": clean(decision.get("target_type")).casefold(),
                 "target_id": clean(decision.get("target_id")),
                 "decision": clean(decision.get("decision")).casefold(),
                 "reviewer_label": clean(decision.get("reviewer_label")),
                 "rationale": clean(decision.get("rationale")),
                 "decided_at": clean(decision.get("decided_at"))}
        event["event_hash"] = digest(event)
        previous_hash = event["event_hash"]
        audit_events.append(event)

    return {
        "schema_version": 1, "usiphus_version": VERSION, "generated_at": as_of.isoformat(),
        "summary": {
            "sources": len(sources), "claims": len(claims), "source_changes": len(change_events),
            "review_queue_items": len(queue), "duplicate_candidates": len(duplicate_candidates),
            "jurisdiction_mismatches": len(jurisdiction_mismatches),
            "sources_with_downstream_impact": sum(bool(value["claims"]) for value in downstream.values()),
        },
        "source_changes": change_events, "duplicate_source_candidates": duplicate_candidates,
        "review_queue": queue, "source_impact": downstream,
        "impact_graph": {"nodes": list(graph_nodes.values()), "edges": graph_edges},
        "faculty_decisions": decision_rows,
        "claim_decisions": {key.split(":", 1)[1]: value for key, value in decision_map.items()
                            if key.startswith("claim:")},
        "decision_audit_events": audit_events,
        "next_state": {"schema_version": 1, "sources": current_state},
        "findings": findings,
        "policy": {
            "review_priorities": CHANGE_WEIGHTS,
            "queue_score": "Base priority by event type plus 10 points per affected artifact, capped at 100 additional points; deterministic triage only, not clinical risk.",
            "retrieval": "Offline by default; only user-supplied local content_path files are hashed. URL content is never fetched automatically.",
            "human_control": "Source status, claim text and clinical content are never changed by V18. Faculty decisions are recorded as input metadata only.",
            "reviewer_identity": "Reviewer labels are user-supplied and are not authenticated identities or proof of independent review.",
            "hash_chain": "Decision event hashes detect accidental edits when recomputed, but without external signatures/anchors they are not tamper-proof.",
            "clinical_boundary": "Metadata and workflow triage only; not a source-authority score, guideline-currentness certification, clinical interpretation or clinical approval.",
        },
    }


def make_queue_item(kind: str, source_id: str, impact: dict[str, list[str]],
                    rationale: str, **details: Any) -> dict[str, Any]:
    count = sum(len(values) for values in impact.values())
    base = CHANGE_WEIGHTS.get(kind, 100)
    score = base + min(100, count * 10)
    return {"queue_id": digest({"kind": kind, "source_id": source_id, **details})[:16],
            "event_type": kind, "source_id": source_id, "priority_score": score,
            "affected_artifacts": impact, "rationale": rationale, **details}


def write_outputs(report: dict[str, Any], output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "v18_evidence_lifecycle_report.json": report,
        "v18_review_queue.json": {"schema_version": 1, "items": report["review_queue"]},
        "v18_source_impact_graph.json": report["impact_graph"],
        "v18_source_changes.json": {"schema_version": 1, "changes": report["source_changes"],
                                    "duplicates": report["duplicate_source_candidates"]},
        "v18_state.json": report["next_state"],
        "v18_decision_audit.json": {"schema_version": 1, "events": report["decision_audit_events"]},
    }
    for name, payload in files.items():
        write_json(payload, output_dir / name)
    lines = ["# Usiphus V18 evidence lifecycle report", "",
             f"- Generated: {report['generated_at']}",
             f"- Sources: {report['summary']['sources']}",
             f"- Claims: {report['summary']['claims']}",
             f"- Source changes: {report['summary']['source_changes']}",
             f"- Review queue items: {report['summary']['review_queue_items']}",
             f"- Duplicate candidates: {report['summary']['duplicate_candidates']}", "",
             "## Prioritized review queue"]
    if report["review_queue"]:
        lines.extend([f"- **{item['priority_score']} — {item['event_type']} — {item['source_id']}**: "
                      f"{item['rationale']} Affected: {sum(len(v) for v in item['affected_artifacts'].values())} artifacts."
                      for item in report["review_queue"]])
    else:
        lines.append("- No lifecycle review candidates.")
    lines += ["", "## Boundaries", report["policy"]["clinical_boundary"],
              report["policy"]["human_control"], report["policy"]["reviewer_identity"],
              report["policy"]["hash_chain"], ""]
    write_text("\n".join(lines), output_dir / "v18_evidence_lifecycle_report.md")
    buffer = []
    for item in report["review_queue"]:
        buffer.append({"priority_score": item["priority_score"], "event_type": item["event_type"],
                       "source_id": item["source_id"], "queue_id": item["queue_id"],
                       "rationale": item["rationale"],
                       "affected_claims": ";".join(item["affected_artifacts"].get("claims", [])),
                       "affected_cards": ";".join(item["affected_artifacts"].get("cards", [])),
                       "affected_competencies": ";".join(item["affected_artifacts"].get("competencies", [])),
                       "affected_assessments": ";".join(item["affected_artifacts"].get("assessment_items", [])),
                       "affected_osce": ";".join(item["affected_artifacts"].get("osce_stations", []))})
    import io
    stream = io.StringIO()
    fields = ["priority_score", "event_type", "source_id", "queue_id", "rationale",
              "affected_claims", "affected_cards", "affected_competencies", "affected_assessments", "affected_osce"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(buffer)
    write_text(stream.getvalue(), output_dir / "v18_review_queue.csv")
    input_hashes = report.get("input_hashes", {})
    output_hashes = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "v18_manifest.json":
            output_hashes[path.name] = digest_bytes(path.read_bytes())
    write_json({"schema_version": 1, "usiphus_version": VERSION,
                "input_sha256": input_hashes, "output_sha256": output_hashes},
               output_dir / "v18_manifest.json")


def run(args: argparse.Namespace) -> dict[str, Any]:
    sources_path, claims_path = Path(args.sources), Path(args.claims)
    sources_payload, claims_payload = read_json(sources_path), read_json(claims_path)
    sources = records(sources_payload, "sources", "sources")
    claims = records(claims_payload, "claims", "claims")
    def optional_records(value: str | None, key: str, label: str) -> list[dict[str, Any]]:
        return records(read_json(Path(value)), key, label) if value else []
    previous = read_json(Path(args.previous_state)) if args.previous_state else None
    decisions = optional_records(args.decisions, "decisions", "decisions")
    report = analyze(
        sources, claims, source_root=sources_path.resolve().parent,
        as_of=parse_timestamp(args.as_of, "--as-of", optional=False),
        previous_state=previous,
        cards=optional_records(args.cards, "cards", "cards"),
        assessments=optional_records(args.assessments, "items", "assessments"),
        osce=optional_records(args.osce, "stations", "OSCE stations"),
        decisions=decisions,
    )
    inputs = [sources_path, claims_path] + [Path(p) for p in
             (args.previous_state, args.cards, args.assessments, args.osce, args.decisions) if p]
    report["input_hashes"] = {str(path): digest_bytes(path.read_bytes()) for path in inputs}
    write_outputs(report, Path(args.output_dir))
    return report


def self_test() -> int:
    import unittest
    class V18Tests(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            self.root = Path(self.tmp.name)
            self.sources = [{"source_id": "S1", "title": "Guideline", "source_type": "guideline",
                             "publisher": "College", "jurisdiction": "GB", "version": "1",
                             "status": "active", "last_reviewed_at": "2026-01-01T00:00:00Z",
                             "review_due_days": 365, "url": "https://example.org/guideline#section"}]
            self.claims = [{"claim_id": "C1", "card_id": "CARD1", "claim": "Synthetic claim",
                            "references": [{"source_id": "S1", "jurisdiction": "GB"}]}]
        def tearDown(self):
            self.tmp.cleanup()
        def analyze_now(self, **kwargs):
            return analyze(self.sources, self.claims, source_root=self.root,
                           as_of=datetime(2026, 11, 1, tzinfo=timezone.utc), **kwargs)
        def test_canonical_url_and_fragment(self):
            self.assertEqual(canonical_url("HTTPS://Example.org:443/a/#x"), "https://example.org/a")
        def test_withdrawal_creates_high_priority_review(self):
            self.sources[0]["status"] = "withdrawn"
            result = self.analyze_now()
            self.assertEqual(result["review_queue"][0]["event_type"], "withdrawn_source")
            self.assertEqual(result["summary"]["review_queue_items"], 1)
        def test_version_and_content_change_are_candidates(self):
            old = {"schema_version": 1, "sources": {"S1": {
                "source_id": "S1", "title": "Guideline", "publisher": "College",
                "jurisdiction": "GB", "source_type": "guideline", "version": "0",
                "status": "active", "canonical_url": "https://example.org/guideline",
                "content_sha256": "a" * 64, "published_on": ""}}}
            self.sources[0]["content_sha256"] = "b" * 64
            result = self.analyze_now(previous_state=old)
            self.assertTrue(any(x["change_type"] == "source_content_changed" for x in result["source_changes"]))
            self.assertTrue(any(x["event_type"] == "source_content_changed" for x in result["review_queue"]))
        def test_impact_graph_reaches_assessment_and_osce(self):
            result = self.analyze_now(
                cards=[{"card_id": "CARD1", "competency_ids": ["U1"]}],
                assessments=[{"item_id": "Q1", "competency_ids": ["U1"]}],
                osce=[{"station_id": "S1", "competency_ids": ["U1"]}],
            )
            self.assertEqual(result["source_impact"]["S1"]["assessment_items"], ["Q1"])
            self.assertEqual(result["source_impact"]["S1"]["osce_stations"], ["S1"])
        def test_jurisdiction_mismatch_is_queued(self):
            self.claims[0]["references"][0]["jurisdiction"] = "US"
            result = self.analyze_now()
            self.assertTrue(any(x["event_type"] == "jurisdiction_mismatch" for x in result["review_queue"]))
        def test_duplicate_sources_are_candidates_not_merged(self):
            self.sources.append(dict(self.sources[0], source_id="S2"))
            result = self.analyze_now()
            self.assertTrue(result["duplicate_source_candidates"])
            self.assertEqual(len(result["next_state"]["sources"]), 2)
        def test_content_path_escape_rejected(self):
            self.sources[0]["content_path"] = "../outside.txt"
            with self.assertRaises(V18Error):
                self.analyze_now()
        def test_decision_requires_rationale_and_timestamp(self):
            with self.assertRaises(V18Error):
                self.analyze_now(decisions=[{"target_type": "source", "target_id": "S1",
                                            "decision": "accepted", "reviewer_label": "R1",
                                            "rationale": "Reviewed"}])
        def test_decision_audit_chain(self):
            decisions = [{"target_type": "source", "target_id": "S1", "decision": "deferred",
                          "reviewer_label": "faculty-label", "rationale": "Await updated guidance",
                          "decided_at": "2026-10-31T10:00:00Z"}]
            result = self.analyze_now(decisions=decisions)
            self.assertEqual(result["decision_audit_events"][0]["previous_hash"], "0" * 64)
            self.assertEqual(len(result["decision_audit_events"][0]["event_hash"]), 64)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(V18Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", help="V15-style source registry JSON")
    parser.add_argument("--claims", help="V15-style claim registry JSON")
    parser.add_argument("--previous-state", help="Previous v18_state.json for change detection")
    parser.add_argument("--cards", help="V17 cards JSON for downstream impact")
    parser.add_argument("--assessments", help="V17 assessment items JSON")
    parser.add_argument("--osce", help="V17 OSCE stations JSON")
    parser.add_argument("--decisions", help="Faculty decision JSON (accepted/rejected/deferred)")
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).isoformat(),
                        help="Timezone-aware ISO timestamp; defaults to current UTC time")
    parser.add_argument("--output-dir", default="build-v18")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.sources or not args.claims:
        parser.error("--sources and --claims are required unless --self-test is used")
    try:
        report = run(args)
        print(f"V18 complete: {report['summary']['review_queue_items']} review candidates; "
              f"{report['summary']['source_changes']} source changes.")
        return 0
    except (V18Error, OSError, ValueError) as exc:
        print(f"V18 error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
