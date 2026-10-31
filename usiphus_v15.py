#!/usr/bin/env python3
"""Usiphus V15 — governed evidence registry hardening and impact analysis.

V15 is a deterministic metadata/governance layer over the V8/V9 evidence
registry and V12 release checks. It does not appraise clinical truth or source
quality. Human review remains mandatory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

VERSION = "15.0.0"
WORKFLOW = ("draft", "in_review", "approved", "published")
TRANSITIONS = {
    "draft": {"in_review"},
    "in_review": {"draft", "approved"},
    "approved": {"in_review", "published"},
    "published": {"in_review"},
}
SOURCE_STATUSES = {"active", "superseded", "withdrawn"}
LOG = logging.getLogger("usiphus.v15")


class V15Error(ValueError):
    """Invalid V15 input or policy."""


def clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise V15Error(f"Input file not found: {path}")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise V15Error(f"Input exceeds 50 MiB: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V15Error(f"Invalid JSON in {path}: {exc}") from exc


def records(payload: Any, key: str, label: str) -> list[dict[str, Any]]:
    rows = payload.get(key, []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        raise V15Error(f"{label} must be a list of objects or an object containing '{key}'")
    return rows


def valid_date(value: str) -> bool:
    try:
        return bool(value) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def valid_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def verify_hash_chain(events: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(events, list):
        return ["events must be a list"]
    previous = "0" * 64
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(f"event {index} must be an object")
            continue
        supplied = clean(event.get("event_hash"))
        payload = {k: v for k, v in event.items() if k != "event_hash"}
        expected = digest(payload)
        if clean(event.get("previous_hash")) != previous:
            issues.append(f"event {index} previous_hash does not match the prior event")
        if supplied != expected:
            issues.append(f"event {index} event_hash is invalid")
        if not clean(event.get("actor")) or not clean(event.get("action")):
            issues.append(f"event {index} requires actor and action")
        if not valid_timestamp(clean(event.get("timestamp"))):
            issues.append(f"event {index} requires a timezone-aware timestamp")
        previous = supplied or expected
    return issues


def build_event(previous_hash: str, actor: str, action: str,
                timestamp: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not actor.strip() or not action.strip() or not valid_timestamp(timestamp):
        raise V15Error("actor, action and timezone-aware timestamp are required")
    event = {
        "previous_hash": previous_hash,
        "actor": actor.strip(),
        "action": action.strip(),
        "timestamp": timestamp,
        "details": dict(details or {}),
    }
    event["event_hash"] = digest(event)
    return event


def analyze(sources: list[dict[str, Any]], claims: list[dict[str, Any]], *,
            as_of: date, max_age_days: int, minimum_reviewers: int,
            require_registered_sources: bool) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    source_by_id: dict[str, dict[str, Any]] = {}
    for i, src in enumerate(sources):
        sid = clean(src.get("source_id"))
        issues: list[str] = []
        if not sid:
            issues.append("source_id is required")
        elif sid in source_by_id:
            issues.append("duplicate source_id")
        else:
            source_by_id[sid] = src
        if not clean(src.get("title")):
            issues.append("title is required")
        if not clean(src.get("source_type")):
            issues.append("source_type is required")
        if not clean(src.get("authority")):
            issues.append("authority is required (metadata only; not an automated quality grade)")
        if not clean(src.get("jurisdiction")):
            issues.append("jurisdiction is required")
        status = clean(src.get("status") or "active").casefold()
        if status not in SOURCE_STATUSES:
            issues.append(f"unsupported source status: {status}")
        if src.get("published_on") and not valid_date(clean(src.get("published_on"))):
            issues.append("published_on must be YYYY-MM-DD")
        if status == "superseded" and not clean(src.get("superseded_by")):
            issues.append("superseded source requires superseded_by")
        if issues:
            findings.append({"kind": "source", "id": sid or f"row-{i+1}", "issues": issues})

    # Validate supersession references and detect cycles.
    for sid, src in source_by_id.items():
        successor = clean(src.get("superseded_by"))
        if successor and successor not in source_by_id:
            findings.append({"kind": "source", "id": sid,
                             "issues": [f"superseded_by references unknown source: {successor}"]})
        seen: set[str] = set()
        current = sid
        while current in source_by_id:
            if current in seen:
                findings.append({"kind": "source", "id": sid,
                                 "issues": ["supersession cycle detected"]})
                break
            seen.add(current)
            current = clean(source_by_id[current].get("superseded_by"))

    claim_ids: set[str] = set()
    impacted: dict[str, set[str]] = {}
    legacy_claims = 0
    for i, claim in enumerate(claims):
        cid, card_id = clean(claim.get("claim_id")), clean(claim.get("card_id"))
        issues: list[str] = []
        if not cid:
            issues.append("claim_id is required")
        elif cid in claim_ids:
            issues.append("duplicate claim_id")
        claim_ids.add(cid)
        if not card_id:
            issues.append("card_id is required")
        if not clean(claim.get("claim")):
            issues.append("claim text is required")
        status = clean(claim.get("status") or "draft").casefold()
        if status not in WORKFLOW:
            issues.append(f"invalid workflow status: {status}")
        refs = claim.get("references")
        if refs is None:
            # V8/V9/V12 compatibility: retain the old single-reference shape.
            legacy_ref = clean(claim.get("reference"))
            refs = []
            if legacy_ref:
                legacy_claims += 1
                refs = [{"legacy_reference": legacy_ref}]
        if not isinstance(refs, list):
            issues.append("references must be a list")
            refs = []
        if not refs:
            issues.append("claim has no linked evidence source")
        for ref in refs:
            if not isinstance(ref, dict):
                issues.append("each reference must be an object")
                continue
            source_id = clean(ref.get("source_id"))
            if not source_id:
                if ref.get("legacy_reference"):
                    if require_registered_sources:
                        issues.append("legacy reference is not linked to a registered source")
                    continue
                issues.append("reference source_id is required")
                continue
            src = source_by_id.get(source_id)
            if src is None:
                issues.append(f"reference points to unknown source_id: {source_id}")
                continue
            if clean(ref.get("version")) and clean(ref.get("version")) != clean(src.get("version")):
                issues.append(f"source version mismatch for {source_id}")
            if clean(ref.get("jurisdiction")) and clean(ref.get("jurisdiction")) != clean(src.get("jurisdiction")):
                issues.append(f"source jurisdiction mismatch for {source_id}")
            if clean(src.get("status") or "active").casefold() != "active":
                issues.append(f"referenced source {source_id} is not active")
            impacted.setdefault(source_id, set()).add(card_id)
        audit = claim.get("audit_events")
        if audit is not None:
            issues.extend(verify_hash_chain(audit))
        else:
            # Existing date-based V8/V9/V12 audit trails are preserved, but
            # cannot be described as tamper-evident.
            legacy_audit = claim.get("audit_trail")
            if legacy_audit:
                issues.append("legacy audit_trail is not hash-chained; migrate before relying on tamper evidence")
        decisions = claim.get("review_decisions", [])
        if not isinstance(decisions, list):
            issues.append("review_decisions must be a list")
            decisions = []
        approved_reviewers = {
            clean(d.get("reviewer_id")) for d in decisions
            if isinstance(d, dict) and clean(d.get("decision")).casefold() == "approve"
            and clean(d.get("reviewer_id"))
        }
        if status in {"approved", "published"}:
            if len(approved_reviewers) < minimum_reviewers:
                issues.append(f"requires at least {minimum_reviewers} distinct approving reviewer(s)")
            if not valid_date(clean(claim.get("reviewed_on"))):
                issues.append("approved/published claim requires reviewed_on YYYY-MM-DD")
            elif (as_of - date.fromisoformat(clean(claim.get("reviewed_on")))).days > max_age_days:
                issues.append(f"claim review is stale (>{max_age_days} days)")
        if issues:
            findings.append({"kind": "claim", "id": cid or f"row-{i+1}",
                             "card_id": card_id, "status": status, "issues": issues})

    blocked = sum(1 for f in findings if f["kind"] == "claim" and f["issues"])
    source_errors = sum(1 for f in findings if f["kind"] == "source" and f["issues"])
    return {
        "schema_version": 1, "usiphus_version": VERSION,
        "as_of": as_of.isoformat(), "source_count": len(sources),
        "claim_count": len(claims), "legacy_reference_claims": legacy_claims,
        "findings": findings,
        "impact_index": {sid: sorted(cards) for sid, cards in sorted(impacted.items())},
        "release_policy": {
            "minimum_approving_reviewers": minimum_reviewers,
            "require_registered_sources": require_registered_sources,
            "max_review_age_days": max_age_days,
        },
        "governance_status": "blocked" if blocked or source_errors else "checks_passed",
        "boundary": "Metadata/workflow checks only; not clinical truth, source-quality appraisal, guideline currency certification, identity verification, privacy/legal compliance, or educational accreditation.",
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v15_governance_report.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    with (output_dir / "v15_findings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "id", "card_id", "status", "issues"])
        for row in result["findings"]:
            writer.writerow([row.get("kind", ""), row.get("id", ""), row.get("card_id", ""),
                             row.get("status", ""), " | ".join(row["issues"])])
    lines = ["# Usiphus V15 governance report", "",
             f"- Status: **{result['governance_status']}**",
             f"- Sources: {result['source_count']}",
             f"- Claims: {result['claim_count']}",
             f"- Legacy reference records: {result['legacy_reference_claims']}", "",
             "## Findings"]
    if result["findings"]:
        lines.extend([f"- **{x['kind']} {x['id']}**: {'; '.join(x['issues'])}" for x in result["findings"]])
    else:
        lines.append("- No metadata/workflow findings.")
    lines += ["", "## Source impact index"]
    if result["impact_index"]:
        lines.extend([f"- {sid}: {', '.join(cards)}" for sid, cards in result["impact_index"].items()])
    else:
        lines.append("- No registered source links.")
    lines += ["", result["boundary"], ""]
    (output_dir / "v15_governance_report.md").write_text("\n".join(lines), encoding="utf-8")


def self_test() -> int:
    import unittest

    class V15Tests(unittest.TestCase):
        def test_hash_chain_and_tampering(self):
            event = build_event("0" * 64, "reviewer-1", "approve",
                                "2026-10-31T12:00:00Z", {"claim_id": "C1"})
            self.assertEqual(verify_hash_chain([event]), [])
            changed = dict(event, action="reject")
            self.assertTrue(verify_hash_chain([changed]))

        def test_legacy_reference_is_reported_not_promoted(self):
            result = analyze([], [{"claim_id": "C1", "card_id": "CARD1",
                                   "claim": "Example", "reference": "Old ref",
                                   "status": "draft"}], as_of=date(2026, 10, 31),
                             max_age_days=730, minimum_reviewers=1,
                             require_registered_sources=True)
            self.assertEqual(result["legacy_reference_claims"], 1)
            self.assertIn("legacy reference is not linked to a registered source",
                          " ".join(result["findings"][0]["issues"]))

        def test_source_supersession_and_impact(self):
            src = {"source_id": "S1", "title": "Guideline", "source_type": "guideline",
                   "authority": "publisher", "jurisdiction": "XX", "version": "1",
                   "status": "active"}
            claim = {"claim_id": "C1", "card_id": "CARD1", "claim": "Example",
                     "references": [{"source_id": "S1", "version": "1", "jurisdiction": "XX"}],
                     "status": "draft"}
            result = analyze([src], [claim], as_of=date(2026, 10, 31),
                             max_age_days=730, minimum_reviewers=1,
                             require_registered_sources=True)
            self.assertEqual(result["impact_index"], {"S1": ["CARD1"]})
            self.assertEqual(result["governance_status"], "checks_passed")

        def test_approved_requires_distinct_reviewer_decision(self):
            src = {"source_id": "S1", "title": "Guideline", "source_type": "guideline",
                   "authority": "publisher", "jurisdiction": "XX", "version": "1"}
            claim = {"claim_id": "C1", "card_id": "CARD1", "claim": "Example",
                     "references": [{"source_id": "S1"}], "status": "approved",
                     "reviewed_on": "2026-10-31",
                     "review_decisions": [{"reviewer_id": "R1", "decision": "approve"}]}
            result = analyze([src], [claim], as_of=date(2026, 10, 31),
                             max_age_days=730, minimum_reviewers=2,
                             require_registered_sources=True)
            self.assertIn("requires at least 2 distinct approving reviewer(s)",
                          " ".join(result["findings"][0]["issues"]))

        def test_supersession_cycle_flagged(self):
            base = {"title": "Source", "source_type": "guideline",
                    "authority": "publisher", "jurisdiction": "XX"}
            sources = [dict(base, source_id="A", status="superseded", superseded_by="B"),
                       dict(base, source_id="B", status="superseded", superseded_by="A")]
            result = analyze(sources, [], as_of=date(2026, 10, 31),
                             max_age_days=730, minimum_reviewers=1,
                             require_registered_sources=True)
            self.assertTrue(any("cycle" in " ".join(x["issues"]) for x in result["findings"]))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(V15Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Usiphus V15 evidence governance hardening")
    parser.add_argument("--sources", help="JSON source registry (list or {'sources': [...]})")
    parser.add_argument("--claims", help="JSON claim registry (list or {'claims': [...]})")
    parser.add_argument("--output-dir", default="./build-v15")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--max-review-age-days", type=int, default=730)
    parser.add_argument("--minimum-reviewers", type=int, default=1)
    parser.add_argument("--require-registered-sources", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    if args.self_test:
        return self_test()
    if not args.sources or not args.claims:
        parser.error("--sources and --claims are required unless --self-test is used")
    try:
        if not valid_date(args.as_of):
            raise V15Error("--as-of must be YYYY-MM-DD")
        if args.max_review_age_days < 1 or args.minimum_reviewers < 1:
            raise V15Error("review age and minimum reviewers must be positive")
        source_rows = records(read_json(Path(args.sources)), "sources", "Source registry")
        claim_rows = records(read_json(Path(args.claims)), "claims", "Claim registry")
        result = analyze(source_rows, claim_rows, as_of=date.fromisoformat(args.as_of),
                         max_age_days=args.max_review_age_days,
                         minimum_reviewers=args.minimum_reviewers,
                         require_registered_sources=args.require_registered_sources)
        write_outputs(result, Path(args.output_dir))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["governance_status"] == "checks_passed" else 1
    except (OSError, V15Error, TypeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
