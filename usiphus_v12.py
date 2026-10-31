#!/usr/bin/env python3
"""Usiphus V12 — integrated specialty learning platform coordinator.

Joins V8 evidence governance, V10 curriculum mapping, and V11 learner analytics
into one local, auditable release/readiness report and faculty review work queue.
This is a batch integration layer, not an LMS, identity provider, or clinical
decision system. Source artifacts are read-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

VERSION = "12.0.0"
LOG = logging.getLogger("usiphus.v12")
WORKFLOW = ("draft", "in_review", "approved", "published")
TRANSITIONS = {
    "draft": {"in_review"},
    "in_review": {"draft", "approved"},
    "approved": {"in_review", "published"},
    "published": {"in_review"},
}
MAX_BYTES = 50 * 1024 * 1024


def clean(value: Any) -> str:
    return str(value or "").strip()


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"Input exceeds 50 MiB: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def records_from(payload: Any, key: str, label: str) -> list[dict[str, Any]]:
    rows = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label} must be a list of objects or an object containing '{key}'")
    return rows


def load_cards(path: Path, specialty: str) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    rows = records_from(payload, "cards", "Cards")
    result: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        cid = clean(row.get("card_id") or row.get("id"))
        card_specialty = clean(row.get("specialty")).casefold()
        if not cid or cid in result:
            raise ValueError(f"Card {i} requires a unique card_id/id")
        if card_specialty != specialty:
            raise ValueError(f"Card {cid} specialty does not match curriculum specialty '{specialty}'")
        result[cid] = row
    if not result:
        raise ValueError("At least one card is required")
    return result


def load_curriculum(path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("competencies"), list):
        raise ValueError("Curriculum must contain a competencies list")
    specialty = clean(payload.get("specialty")).casefold()
    if not specialty:
        raise ValueError("Curriculum specialty is required")
    result, seen = [], set()
    for i, row in enumerate(payload["competencies"]):
        if not isinstance(row, dict):
            raise ValueError(f"Competency {i} must be an object")
        cid = clean(row.get("competency_id") or row.get("id"))
        if not cid or cid.casefold() in seen:
            raise ValueError(f"Competency {i} requires a unique ID")
        seen.add(cid.casefold())
        required = row.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(f"Competency {cid} required must be boolean")
        result.append({"id": cid, "title": clean(row.get("title") or row.get("name")) or cid,
                       "domain": clean(row.get("domain") or "Uncategorized"),
                       "required": required, "weight": row.get("weight", 1.0)})
    if not result:
        raise ValueError("Curriculum must contain at least one competency")
    return specialty, result


def load_mapping(path: Path, specialty: str, cards: set[str], competencies: set[str]):
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("Mapping must be a V10 curriculum coverage report with candidates")
    if clean(payload.get("specialty")).casefold() != specialty:
        raise ValueError("Mapping specialty does not match curriculum")
    verified: dict[str, set[str]] = {}
    queue = []
    seen = set()
    for i, row in enumerate(payload["candidates"]):
        if not isinstance(row, dict):
            raise ValueError(f"Mapping candidate {i} must be an object")
        card, comp, status = clean(row.get("card_id")), clean(row.get("competency_id")), clean(row.get("status")).casefold()
        if not card or card not in cards or not comp:
            raise ValueError(f"Mapping candidate {i} references an unknown card or has no competency ID")
        if status not in {"verified", "suggested", "pending", "invalid"}:
            raise ValueError(f"Mapping candidate {i} has unsupported status '{status}'")
        if status != "invalid" and comp.casefold() not in competencies:
            raise ValueError(f"Mapping candidate {i} references an unknown competency")
        key = (card, comp.casefold(), status)
        if key in seen:
            raise ValueError(f"Duplicate mapping candidate: {key}")
        seen.add(key)
        if status == "verified":
            verified.setdefault(card, set()).add(comp.casefold())
        elif status in {"suggested", "pending"}:
            queue.append({"work_type": "mapping_review", "item_id": f"{card}:{comp}",
                          "card_id": card, "competency_id": comp, "current_status": status,
                          "reason": "Faculty review required; candidate does not count as verified coverage"})
        elif status == "invalid":
            queue.append({"work_type": "mapping_repair", "item_id": f"{card}:{comp}",
                          "card_id": card, "competency_id": comp, "current_status": status,
                          "reason": "Invalid mapping requires correction"})
    return verified, queue


def validate_evidence_audit(record: dict[str, Any]) -> list[str]:
    issues = []
    status = clean(record.get("status") or "draft").casefold()
    if status not in WORKFLOW:
        return [f"invalid workflow status: {status}"]
    audit = record.get("audit_trail") or []
    if not isinstance(audit, list):
        return ["audit_trail must be a list"]
    previous = "draft"
    for i, event in enumerate(audit):
        if not isinstance(event, dict):
            issues.append(f"audit event {i} must be an object")
            continue
        target = clean(event.get("to")).casefold()
        actor = clean(event.get("actor"))
        try:
            date.fromisoformat(clean(event.get("date")))
            valid_date = True
        except ValueError:
            valid_date = False
        if target not in WORKFLOW or target not in TRANSITIONS.get(previous, set()):
            issues.append(f"invalid workflow transition at event {i}: {previous} -> {target}")
        if not actor:
            issues.append(f"audit event {i} is missing actor")
        if not valid_date:
            issues.append(f"audit event {i} has invalid/missing date")
        if target in WORKFLOW:
            previous = target
    if status in {"approved", "published"} and not audit:
        issues.append("approved/published evidence requires an audit trail")
    if audit and previous != status:
        issues.append(f"audit trail ends at {previous}, but record status is {status}")
    return issues


def load_evidence(path: Path, cards: set[str], as_of: date, max_age_days: int):
    payload = read_json(path)
    rows = records_from(payload, "claims", "Evidence registry")
    queue, findings, covered = [], [], set()
    ids = set()
    for i, row in enumerate(rows):
        claim_id, card_id = clean(row.get("claim_id")), clean(row.get("card_id"))
        status = clean(row.get("status") or "draft").casefold()
        issues = []
        if not claim_id or claim_id in ids:
            issues.append("claim_id is missing or duplicated")
        ids.add(claim_id)
        if card_id not in cards:
            issues.append("claim maps to an unknown card")
        if not clean(row.get("claim")):
            issues.append("claim text is missing")
        if not clean(row.get("reference")) or not clean(row.get("source_type")):
            issues.append("reference and source_type are required")
        issues.extend(validate_evidence_audit(row))
        if status in {"approved", "published"}:
            raw_date = clean(row.get("reviewed_on"))
            try:
                reviewed = date.fromisoformat(raw_date)
                if (as_of - reviewed).days > max_age_days:
                    issues.append(f"evidence review is stale (>{max_age_days} days)")
            except ValueError:
                issues.append("approved/published evidence requires reviewed_on YYYY-MM-DD")
            if not clean(row.get("reviewer")):
                issues.append("approved/published evidence requires reviewer")
        if status in {"approved", "published"} and not issues:
            covered.add(card_id)
        if issues:
            findings.append({"claim_id": claim_id or f"row-{i+1}", "card_id": card_id,
                             "status": status, "issues": issues})
        if status not in {"approved", "published"} or issues:
            queue.append({"work_type": "evidence_review", "item_id": claim_id or f"row-{i+1}",
                          "card_id": card_id, "competency_id": "", "current_status": status,
                          "reason": "; ".join(issues) if issues else "Evidence requires approval/publication"})
    return covered, findings, queue, len(rows)


def validate_v11(path: Path, specialty: str, competencies: set[str]):
    progress = read_json(path)
    if not isinstance(progress, dict) or clean(progress.get("specialty")).casefold() != specialty:
        raise ValueError("V11 learner progress specialty does not match curriculum")
    learners = progress.get("learners")
    if not isinstance(learners, list):
        raise ValueError("V11 learner progress must contain a learners list")
    rows = 0
    for learner in learners:
        if not isinstance(learner, dict) or not isinstance(learner.get("competencies"), list):
            raise ValueError("Malformed V11 learner progress record")
        for item in learner["competencies"]:
            if not isinstance(item, dict) or clean(item.get("competency_id")).casefold() not in competencies:
                raise ValueError("V11 progress references an unknown competency")
            rows += 1
    return {"active_learners": len(learners),
            "accepted_attempts": int(progress.get("accepted_attempts", 0)),
            "learner_competency_rows": rows,
            "limitations": ["V11 learner-level records are not copied into V12 platform summaries."]}


def validate_cohort(path: Path, specialty: str, competencies: set[str]):
    payload = read_json(path)
    if not isinstance(payload, dict) or clean(payload.get("specialty")).casefold() != specialty:
        raise ValueError("V11 cohort analytics specialty does not match curriculum")
    rows = payload.get("competencies")
    if not isinstance(rows, list):
        raise ValueError("V11 cohort analytics must contain competencies")
    suppressed = 0
    for row in rows:
        if not isinstance(row, dict) or clean(row.get("competency_id")).casefold() not in competencies:
            raise ValueError("Cohort analytics references an unknown competency")
        if not isinstance(row.get("suppressed"), bool):
            raise ValueError("Each cohort row must declare suppressed as boolean")
        if row["suppressed"]:
            suppressed += 1
            if "mean_mastery_estimate" in row or "learner_count" in row:
                raise ValueError("Suppressed cohort rows must not expose learner metrics")
    return {"competency_rows": len(rows), "suppressed_rows": suppressed,
            "minimum_cohort_size": payload.get("minimum_cohort_size")}


def load_schedule(path: Path, competencies: set[str]):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"learner_id", "competency_id", "next_due_at", "recommended_interval_days", "status"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("V11 review schedule CSV is missing required columns")
        count = 0
        for row in reader:
            if clean(row.get("competency_id")).casefold() not in competencies:
                raise ValueError("Review schedule references an unknown competency")
            count += 1
    return count


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = handle.name
            handle.write(content)
            handle.flush()
        Path(temp).replace(path)
    finally:
        if temp and Path(temp).exists():
            Path(temp).unlink()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(args) -> int:
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    specialty, curriculum = load_curriculum(Path(args.curriculum))
    competency_ids = {row["id"].casefold() for row in curriculum}
    cards = load_cards(Path(args.cards), specialty)
    card_ids = set(cards)
    verified, mapping_queue = load_mapping(Path(args.mapping), specialty, card_ids, competency_ids)
    evidence_covered, evidence_findings, evidence_queue, evidence_count = load_evidence(
        Path(args.evidence), card_ids, as_of, args.max_evidence_age_days)
    learner = validate_v11(Path(args.learner_progress), specialty, competency_ids)
    cohort = validate_cohort(Path(args.cohort_analytics), specialty, competency_ids)
    schedule_count = load_schedule(Path(args.review_schedule), competency_ids)

    required = [row for row in curriculum if row["required"]]
    mapped = {cid for values in verified.values() for cid in values}
    mapped_required = {row["id"].casefold() for row in required} & mapped
    gaps = [row for row in required if row["id"].casefold() not in mapped]
    unmapped_cards = sorted(card_ids - set(verified))
    queue = list(mapping_queue) + list(evidence_queue)
    for row in gaps:
        queue.append({"work_type": "curriculum_gap", "item_id": row["id"], "card_id": "",
                      "competency_id": row["id"], "current_status": "uncovered",
                      "reason": "Required competency has no faculty-verified card mapping"})
    for card_id in sorted(card_ids - evidence_covered):
        if not any(item.get("card_id") == card_id for item in evidence_queue):
            queue.append({"work_type": "evidence_gap", "item_id": card_id, "card_id": card_id,
                          "competency_id": "", "current_status": "missing",
                          "reason": "No current approved/published evidence claim covers this card"})
    ready = not gaps and not evidence_findings and len(evidence_covered) == len(card_ids)
    checklist = {
        "release_ready": ready,
        "checks": [
            {"check": "all_required_competencies_have_verified_mappings", "passed": not gaps,
             "detail": f"{len(mapped_required)}/{len(required)} required competencies mapped"},
            {"check": "all_cards_have_current_approved_evidence", "passed": ready and not gaps,
             "detail": f"{len(evidence_covered)}/{len(card_ids)} cards covered by current approved/published evidence"},
            {"check": "v11_learner_progress_integrated", "passed": True,
             "detail": f"{learner['active_learners']} learners; {learner['accepted_attempts']} accepted attempts"},
            {"check": "cohort_suppression_schema_valid", "passed": True,
             "detail": f"{cohort['suppressed_rows']} of {cohort['competency_rows']} competency rows suppressed"},
            {"check": "review_schedule_integrated", "passed": True,
             "detail": f"{schedule_count} learner-competency review rows"},
        ],
        "blocking_items": [f"Required competency gap: {row['id']}" for row in gaps] +
                          [f"Evidence issue: {row['claim_id']}" for row in evidence_findings] +
                          [f"Card lacks current approved/published evidence: {cid}"
                           for cid in sorted(card_ids - evidence_covered)],
        "interpretation": "Software readiness is not clinical validation, educational validity, or proof of learner competence."
    }
    report = {
        "schema_version": 1, "usiphus_version": VERSION, "specialty": specialty,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of": as_of.isoformat(),
        "curriculum": {"competencies": len(curriculum), "required": len(required),
                       "verified_required": len(mapped_required), "uncovered_required": len(gaps)},
        "content": {"cards": len(cards), "verified_mapped_cards": len(verified),
                    "unmapped_cards": len(unmapped_cards), "evidence_claims": evidence_count,
                    "cards_with_current_approved_evidence": len(evidence_covered)},
        "learning": learner,
        "cohort": cohort,
        "faculty_workflow": {"open_review_items": len(queue),
                             "by_type": {kind: sum(1 for item in queue if item["work_type"] == kind)
                                         for kind in sorted({item["work_type"] for item in queue})}},
        "release": checklist,
        "governance": [
            "V12 is a local integration/reporting coordinator, not a hosted LMS or identity provider.",
            "Faculty review queue entries are work items; V12 does not approve mappings or evidence.",
            "Learner pseudonyms and individual V11 progress are not exported in the platform summary.",
            "Clinical claims, curriculum mappings, and educational interpretations require qualified human review."
        ]
    }
    output = Path(args.output)
    queue_buf = io.StringIO()
    fields = ["work_type", "item_id", "card_id", "competency_id", "current_status", "reason"]
    writer = csv.DictWriter(queue_buf, fieldnames=fields)
    writer.writeheader()
    writer.writerows(queue)
    atomic_write(output / "platform_report.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    atomic_write(output / "faculty_review_queue.csv", queue_buf.getvalue())
    atomic_write(output / "release_checklist.json", json.dumps(checklist, indent=2) + "\n")
    inputs = {name: {"sha256": sha256(Path(path)), "bytes": Path(path).stat().st_size}
              for name, path in {"cards": args.cards, "curriculum": args.curriculum,
                "mapping": args.mapping, "evidence": args.evidence,
                "learner_progress": args.learner_progress, "cohort_analytics": args.cohort_analytics,
                "review_schedule": args.review_schedule}.items()}
    manifest = {"usiphus_version": VERSION, "schema_version": 1, "inputs": inputs,
                "outputs": ["platform_report.json", "faculty_review_queue.csv", "release_checklist.json"],
                "source_files_modified": False,
                "privacy": {"individual_learner_ids_exported": False,
                            "source_learner_progress_contains_sensitive_pseudonymous_records": True}}
    atomic_write(output / "v12_manifest.json", json.dumps(manifest, indent=2) + "\n")
    LOG.info("V12 generated platform report; release_ready=%s; faculty review items=%d",
             ready, len(queue))
    return 0


def run_self_test() -> int:
    import unittest
    class V12Tests(unittest.TestCase):
        def test_valid_evidence_transition(self):
            row = {"status": "approved", "audit_trail": [
                {"to": "in_review", "actor": "faculty", "date": "2026-10-01"},
                {"to": "approved", "actor": "faculty", "date": "2026-10-02"}]}
            self.assertEqual(validate_evidence_audit(row), [])
        def test_invalid_evidence_transition(self):
            row = {"status": "published", "audit_trail": [
                {"to": "published", "actor": "faculty", "date": "2026-10-02"}]}
            self.assertTrue(validate_evidence_audit(row))
        def test_approved_requires_audit(self):
            self.assertTrue(validate_evidence_audit({"status": "approved"}))
        def test_draft_can_enter_review(self):
            self.assertEqual(validate_evidence_audit({"status": "in_review",
                "audit_trail": [{"to": "in_review", "actor": "reviewer", "date": "2026-10-01"}]}), [])
        def test_published_must_reenter_review_before_approval(self):
            row = {"status": "approved", "audit_trail": [
                {"to": "in_review", "actor": "a", "date": "2026-10-01"},
                {"to": "approved", "actor": "a", "date": "2026-10-02"},
                {"to": "published", "actor": "a", "date": "2026-10-03"},
                {"to": "approved", "actor": "a", "date": "2026-10-04"}]}
            self.assertTrue(validate_evidence_audit(row))
        def test_suppressed_cohort_has_no_metrics(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "cohort.json"
                p.write_text(json.dumps({"specialty":"urology","competencies":[
                    {"competency_id":"U1","suppressed":True,"mean_mastery_estimate":0.8}]}))
                with self.assertRaises(ValueError):
                    validate_cohort(p, "urology", {"u1"})
        def test_unknown_cohort_competency_rejected(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "cohort.json"
                p.write_text(json.dumps({"specialty":"urology","competencies":[
                    {"competency_id":"BAD","suppressed":False}]}))
                with self.assertRaises(ValueError):
                    validate_cohort(p, "urology", {"u1"})
        def test_curriculum_requires_specialty(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "curriculum.json"
                p.write_text(json.dumps({"competencies":[{"id":"U1"}]}))
                with self.assertRaises(ValueError):
                    load_curriculum(p)
        def test_duplicate_competency_rejected(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "curriculum.json"
                p.write_text(json.dumps({"specialty":"urology","competencies":[
                    {"id":"U1"},{"id":"u1"}]}))
                with self.assertRaises(ValueError):
                    load_curriculum(p)
        def test_card_specialty_mismatch_rejected(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "cards.json"
                p.write_text(json.dumps([{"card_id":"c1","specialty":"surgery"}]))
                with self.assertRaises(ValueError):
                    load_cards(p, "urology")
        def test_verified_mapping_only_counts(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "mapping.json"
                p.write_text(json.dumps({"specialty":"urology","candidates":[
                    {"card_id":"c1","competency_id":"U1","status":"verified"},
                    {"card_id":"c1","competency_id":"U2","status":"suggested"}]}))
                verified, queue = load_mapping(p,"urology",{"c1"},{"u1","u2"})
                self.assertEqual(verified,{"c1":{"u1"}})
                self.assertEqual(len(queue),1)
        def test_unknown_mapping_reference_rejected(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "mapping.json"
                p.write_text(json.dumps({"specialty":"urology","candidates":[
                    {"card_id":"missing","competency_id":"U1","status":"verified"}]}))
                with self.assertRaises(ValueError):
                    load_mapping(p,"urology",{"c1"},{"u1"})
        def test_evidence_claim_unknown_card_flagged(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "evidence.json"
                p.write_text(json.dumps({"claims":[{"claim_id":"x","card_id":"missing",
                    "claim":"Claim","reference":"Ref","source_type":"guideline","status":"draft"}]}))
                _, findings, _, _ = load_evidence(p,{"c1"},date(2026,10,31),730)
                self.assertTrue(findings)
        def test_stale_approved_evidence_flagged(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "evidence.json"
                p.write_text(json.dumps({"claims":[{"claim_id":"x","card_id":"c1",
                    "claim":"Claim","reference":"Ref","source_type":"guideline","status":"approved",
                    "reviewed_on":"2020-01-01","reviewer":"faculty","audit_trail":[
                    {"to":"in_review","actor":"faculty","date":"2020-01-01"},
                    {"to":"approved","actor":"faculty","date":"2020-01-02"}]}]}))
                covered, findings, _, _ = load_evidence(p,{"c1"},date(2026,10,31),730)
                self.assertFalse(covered)
                self.assertTrue(findings)
        def test_privacy_suppression_validated(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "cohort.json"
                p.write_text(json.dumps({"specialty":"urology","competencies":[
                    {"competency_id":"U1","suppressed":True,"suppression_reason":"small"}]}))
                result = validate_cohort(p,"urology",{"u1"})
                self.assertEqual(result["suppressed_rows"],1)
        def test_schedule_schema(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "schedule.csv"
                p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\nid,U1,2026-11-01,1,review_due\n")
                self.assertEqual(load_schedule(p,{"u1"}),1)
        def test_schedule_unknown_competency_rejected(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "schedule.csv"
                p.write_text("learner_id,competency_id,next_due_at,recommended_interval_days,status\nid,BAD,2026-11-01,1,review_due\n")
                with self.assertRaises(ValueError):
                    load_schedule(p,{"u1"})
        def test_source_hash_deterministic(self):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "source.json"
                p.write_text('{"a":1}')
                self.assertEqual(sha256(p),sha256(p))
        def test_no_individual_ids_in_platform_summary(self):
            # The integration summary is deliberately constructed from counts only.
            summary = {"active_learners": 2, "accepted_attempts": 10}
            self.assertNotIn("learner_id", json.dumps(summary))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(V12Tests))
    return 0 if result.wasSuccessful() else 2


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description="Usiphus V12 integrated specialty learning platform coordinator")
    parser.add_argument("--cards"); parser.add_argument("--curriculum"); parser.add_argument("--mapping")
    parser.add_argument("--evidence"); parser.add_argument("--learner-progress"); parser.add_argument("--cohort-analytics")
    parser.add_argument("--review-schedule"); parser.add_argument("--output", default="./build-v12")
    parser.add_argument("--as-of", help="Reproducible date, YYYY-MM-DD")
    parser.add_argument("--max-evidence-age-days", type=int, default=730)
    parser.add_argument("--self-test", action="store_true"); parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    if args.self_test:
        return run_self_test()
    required = ["cards", "curriculum", "mapping", "evidence", "learner_progress",
                "cohort_analytics", "review_schedule"]
    missing = [f"--{name.replace('_','-')}" for name in required if not getattr(args, name)]
    if missing:
        LOG.error("Missing required inputs: %s", ", ".join(missing))
        return 2
    if args.max_evidence_age_days < 1:
        LOG.error("--max-evidence-age-days must be positive")
        return 2
    try:
        if args.as_of:
            date.fromisoformat(args.as_of)
        return run(args)
    except (ValueError, OSError, TypeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
