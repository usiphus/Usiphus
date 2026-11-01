
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
import hmac
import io
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

VERSION = "15.1.0"
WORKFLOW = ("draft", "in_review", "approved", "published")
TRANSITIONS = {
    "draft": {"in_review"},
    "in_review": {"draft", "approved"},
    "approved": {"in_review", "published"},
    "published": {"in_review"},
}
SOURCE_STATUSES = {"active", "superseded", "withdrawn"}
LOG = logging.getLogger("usiphus.v15")
AUDIT_KEY_ENV = "USIPHUS_AUDIT_HMAC_KEY"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import atomic_write_text, csv_safe, valid_iso_date  # noqa: E402
from usiphus_signing import SigningError, TrustedKeys, load_anchor_file  # noqa: E402

TRUSTED_KEYS_ENV = "USIPHUS_AUDIT_TRUSTED_KEYS"


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
    return valid_iso_date(value)


def audit_key_from_env(name: str = AUDIT_KEY_ENV) -> bytes | None:
    raw = os.environ.get(name, "")
    return raw.encode("utf-8") if raw else None


def audit_key_from_file(path: str) -> bytes:
    """Read an HMAC key from a file (so it need not live in an environment variable)."""
    p = Path(path)
    if not p.is_file():
        raise V15Error(f"HMAC key file not found: {p}")
    if os.name == "posix" and p.stat().st_mode & 0o077:
        raise V15Error(f"HMAC key file {p} is accessible to other users; run: chmod 600 {p}")
    key = p.read_bytes().rstrip(b"\r\n")
    if not key:
        raise V15Error(f"HMAC key file is empty: {p}")
    return key


def verifier_from_path(path: str | None) -> TrustedKeys | None:
    """Load trusted PUBLIC keys (Ed25519). Verifiers cannot forge signatures."""
    if not path:
        return None
    try:
        return TrustedKeys.load(path)
    except SigningError as exc:
        raise V15Error(str(exc)) from exc


def anchors_from_path(path: str | None, verifier: TrustedKeys | None) -> dict[str, str] | None:
    if not path:
        return None
    if verifier is None:
        raise V15Error("--anchors requires --audit-trusted-keys so the signed anchor file can be verified")
    try:
        return load_anchor_file(path, verifier)
    except SigningError as exc:
        raise V15Error(str(exc)) from exc


def event_signature(key: bytes, event_hash: str) -> str:
    return hmac.new(key, event_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def valid_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def verify_hash_chain(events: Any, hmac_key: bytes | None = None, expected_head: str = "",
                      verifier: TrustedKeys | None = None) -> list[str]:
    """Verify linkage and hashes. Without a key this only detects NAIVE edits
    (anyone can recompute plain hashes).

    * hmac_key  - events must carry a valid 64-hex HMAC-SHA256 signature (shared secret).
    * verifier  - events must carry an 'ed25519:<key_id>:<sig>' signature that verifies
                  against a trusted PUBLIC key for the event's actor (cannot be forged by verifiers).
    * expected_head - the final hash is anchored; also detects truncation or appended events."""
    issues: list[str] = []
    if not isinstance(events, list):
        return ["events must be a list"]
    previous = "0" * 64
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(f"event {index} must be an object")
            continue
        supplied = clean(event.get("event_hash"))
        payload = {k: v for k, v in event.items() if k not in ("event_hash", "signature")}
        expected = digest(payload)
        if clean(event.get("previous_hash")) != previous:
            issues.append(f"event {index} previous_hash does not match the prior event")
        if supplied != expected:
            issues.append(f"event {index} event_hash is invalid")
        if hmac_key is not None or verifier is not None:
            signature = clean(event.get("signature"))
            if signature.startswith("ed25519:"):
                ok = verifier is not None and verifier.verify(expected, signature, clean(event.get("actor")))
            else:
                ok = bool(signature) and hmac_key is not None and hmac.compare_digest(signature, event_signature(hmac_key, expected))
            if not ok:
                issues.append(f"event {index} signature is missing, invalid, or not from a trusted key for actor '{clean(event.get('actor'))}'")
        if not clean(event.get("actor")) or not clean(event.get("action")):
            issues.append(f"event {index} requires actor and action")
        if not valid_timestamp(clean(event.get("timestamp"))):
            issues.append(f"event {index} requires a timezone-aware timestamp")
        previous = supplied or expected
    if expected_head and previous != expected_head:
        issues.append("audit chain head does not match the anchored head hash (events removed, added or altered)")
    return issues


def audit_workflow_issues(events: list[Any], status: str) -> list[str]:
    """Workflow transitions are the events whose details.to names a workflow state."""
    issues: list[str] = []
    previous, seen = "draft", False
    for index, event in enumerate(events):
        details = event.get("details") if isinstance(event, dict) and isinstance(event.get("details"), dict) else {}
        target = clean(details.get("to")).casefold()
        if not target:
            continue
        seen = True
        if target not in WORKFLOW or target not in TRANSITIONS.get(previous, set()):
            issues.append(f"invalid workflow transition at audit event {index}: {previous} -> {target}")
        if target in WORKFLOW:
            previous = target
    if status in {"approved", "published"} and not seen:
        issues.append("approved/published claim requires hash-chained audit events recording workflow transitions (details.to)")
    elif seen and previous != status:
        issues.append(f"audit events end at {previous}, but claim status is {status}")
    return issues


def build_event(previous_hash: str, actor: str, action: str,
                timestamp: str, details: Mapping[str, Any] | None = None,
                hmac_key: bytes | None = None, signer: Any = None) -> dict[str, Any]:
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
    if signer is not None:
        event["signature"] = signer.sign(event["event_hash"])
    elif hmac_key is not None:
        event["signature"] = event_signature(hmac_key, event["event_hash"])
    return event


def analyze(sources: list[dict[str, Any]], claims: list[dict[str, Any]], *,
            as_of: date, max_age_days: int, minimum_reviewers: int,
            require_registered_sources: bool,
            audit_hmac_key: bytes | None = None,
            require_signed_audit: bool = False,
            require_audit_anchor: bool = False,
            audit_verifier: TrustedKeys | None = None,
            external_anchors: Mapping[str, str] | None = None) -> dict[str, Any]:
    """require_signed_audit: HMAC key must be configured whenever any claim is approved/published.
    require_audit_anchor: approved/published claims must carry an anchor (expected final event hash).
    audit_verifier: trusted PUBLIC keys for Ed25519-signed events (preferred over a shared HMAC key).
    external_anchors: claim_id -> head hash from a SIGNED anchor file kept outside the claims file;
    when given, the inline `audit_anchor` field is ignored because anyone who can edit claims can edit it."""
    if require_signed_audit and audit_hmac_key is None and audit_verifier is None and any(
            isinstance(c, dict) and clean(c.get("status")).casefold() in {"approved", "published"} for c in claims):
        raise V15Error(f"Signed audit is required but no signing configuration was provided (trusted keys via --audit-trusted-keys / {TRUSTED_KEYS_ENV}, or an HMAC key via {AUDIT_KEY_ENV}).")
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
            if external_anchors is not None:
                head = clean(external_anchors.get(cid))
                if not head and status in {"approved", "published"}:
                    issues.append("approved/published claim has no entry in the signed external anchor file")
            else:
                head = clean(claim.get("audit_anchor"))
            issues.extend(verify_hash_chain(audit, audit_hmac_key, head, audit_verifier))
            if isinstance(audit, list):
                issues.extend(audit_workflow_issues(audit, status))
        else:
            if status in {"approved", "published"}:
                issues.append("approved/published claim requires hash-chained audit_events")
            # Existing date-based V8/V9/V12 audit trails are preserved, but
            # cannot be described as tamper-evident.
            legacy_audit = claim.get("audit_trail")
            if legacy_audit:
                issues.append("legacy audit_trail is not hash-chained; migrate before relying on tamper evidence")
        decisions = claim.get("review_decisions", [])
        if not isinstance(decisions, list):
            issues.append("review_decisions must be a list")
            decisions = []
        latest: dict[str, str] = {}  # latest decision per reviewer wins
        for d in decisions:
            if isinstance(d, dict) and clean(d.get("reviewer_id")):
                latest[clean(d.get("reviewer_id"))] = clean(d.get("decision")).casefold()
        approved_reviewers = {r for r, dec in latest.items() if dec == "approve"}
        blocking_reviewers = sorted(r for r, dec in latest.items() if dec in {"reject", "request_changes"})
        author = clean(claim.get("author_id"))
        if author and author in approved_reviewers and status in {"approved", "published"}:
            approved_reviewers.discard(author)
            issues.append("author_id appears among approving reviewers; self-approval is not counted")
        if status in {"approved", "published"}:
            if require_audit_anchor and external_anchors is None and not clean(claim.get("audit_anchor")):
                issues.append("approved/published claim requires audit_anchor (expected final audit event hash)")
            if len(approved_reviewers) < minimum_reviewers:
                issues.append(f"requires at least {minimum_reviewers} distinct approving reviewer(s)")
            if blocking_reviewers:
                issues.append("has unresolved reject/request_changes decision(s) from: " + ", ".join(blocking_reviewers))
            if not valid_date(clean(claim.get("reviewed_on"))):
                issues.append("approved/published claim requires reviewed_on YYYY-MM-DD")
            else:
                reviewed = date.fromisoformat(clean(claim.get("reviewed_on")))
                if reviewed > as_of:
                    issues.append("reviewed_on is in the future")
                elif (as_of - reviewed).days > max_age_days:
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
            "reviewer_identity_assurance": "Reviewer IDs are user-supplied labels; quorum counts distinct labels only and does not authenticate people or establish independent human review.",
            "require_registered_sources": require_registered_sources,
            "require_signed_audit": require_signed_audit,
            "require_audit_anchor": require_audit_anchor,
            "audit_signing": {"hmac": audit_hmac_key is not None, "ed25519": audit_verifier is not None,
                              "external_anchors": external_anchors is not None},
            "max_review_age_days": max_age_days,
        },
        "governance_status": "blocked" if blocked or source_errors else "checks_passed",
        "boundary": "Metadata/workflow checks only; not clinical truth, source-quality appraisal, guideline currency certification, identity verification, privacy/legal compliance, or educational accreditation.",
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      output_dir / "v15_governance_report.json")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["kind", "id", "card_id", "status", "issues"])
    for row in result["findings"]:
        writer.writerow([csv_safe(row.get("kind", "")), csv_safe(row.get("id", "")), csv_safe(row.get("card_id", "")),
                         csv_safe(row.get("status", "")), csv_safe(" | ".join(row["issues"]))])
    atomic_write_text(buffer.getvalue(), output_dir / "v15_findings.csv")
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
    atomic_write_text("\n".join(lines), output_dir / "v15_governance_report.md")


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

        SRC = {"source_id": "S1", "title": "G", "source_type": "guideline", "authority": "pub",
               "jurisdiction": "XX", "version": "1"}

        def _run(self, claim, **kw):
            return analyze([self.SRC], [claim], as_of=date(2026, 10, 31), max_age_days=730,
                           minimum_reviewers=kw.pop("minimum_reviewers", 1),
                           require_registered_sources=True, **kw)

        def _approved(self, **extra):
            e1 = build_event("0" * 64, "r1", "submit", "2026-10-30T10:00:00Z", {"to": "in_review"})
            e2 = build_event(e1["event_hash"], "r1", "approve", "2026-10-31T10:00:00Z", {"to": "approved"})
            claim = {"claim_id": "C1", "card_id": "K1", "claim": "x", "references": [{"source_id": "S1"}],
                     "status": "approved", "reviewed_on": "2026-10-31", "audit_events": [e1, e2],
                     "review_decisions": [{"reviewer_id": "R1", "decision": "approve"}]}
            claim.update(extra)
            return claim

        def test_valid_approved_claim_passes(self):
            self.assertEqual(self._run(self._approved())["governance_status"], "checks_passed")

        def test_published_without_audit_is_blocked(self):
            claim = self._approved(status="published")
            claim.pop("audit_events")
            self.assertEqual(self._run(claim)["governance_status"], "blocked")

        def test_empty_audit_events_blocked_for_approved(self):
            self.assertEqual(self._run(self._approved(audit_events=[]))["governance_status"], "blocked")

        def test_invalid_transition_blocked(self):
            e = build_event("0" * 64, "r1", "approve", "2026-10-31T10:00:00Z", {"to": "published"})
            self.assertEqual(self._run(self._approved(audit_events=[e], status="published"))["governance_status"], "blocked")

        def test_reject_decision_blocks_approval(self):
            claim = self._approved(review_decisions=[{"reviewer_id": "R1", "decision": "approve"},
                                                     {"reviewer_id": "R2", "decision": "reject"}])
            self.assertEqual(self._run(claim)["governance_status"], "blocked")

        def test_later_approval_supersedes_earlier_reject(self):
            claim = self._approved(review_decisions=[{"reviewer_id": "R1", "decision": "reject"},
                                                     {"reviewer_id": "R1", "decision": "approve"}])
            self.assertEqual(self._run(claim)["governance_status"], "checks_passed")

        def test_future_review_date_blocked(self):
            self.assertEqual(self._run(self._approved(reviewed_on="2027-06-01"))["governance_status"], "blocked")

        def test_self_approval_not_counted(self):
            claim = self._approved(author_id="R1")
            self.assertEqual(self._run(claim)["governance_status"], "blocked")

        def test_hmac_signature_required_and_verified(self):
            key = b"test-key"
            e1 = build_event("0" * 64, "r", "a", "2026-10-31T10:00:00Z", {}, hmac_key=key)
            self.assertEqual(verify_hash_chain([e1], key), [])
            self.assertTrue(verify_hash_chain([e1], b"wrong-key"))
            unsigned = build_event("0" * 64, "r", "a", "2026-10-31T10:00:00Z", {})
            self.assertTrue(verify_hash_chain([unsigned], key))

        def test_anchor_detects_truncation(self):
            e1 = build_event("0" * 64, "r", "a", "2026-10-31T10:00:00Z", {})
            e2 = build_event(e1["event_hash"], "r", "b", "2026-10-31T11:00:00Z", {})
            self.assertEqual(verify_hash_chain([e1, e2], expected_head=e2["event_hash"]), [])
            self.assertTrue(verify_hash_chain([e1], expected_head=e2["event_hash"]))

        def test_require_signed_audit_fails_without_key(self):
            with self.assertRaises(V15Error):
                self._run(self._approved(), require_signed_audit=True)

        def test_signed_chain_passes_and_unsigned_rejected_with_key(self):
            key = b"k"
            e1 = build_event("0" * 64, "r1", "submit", "2026-10-30T10:00:00Z", {"to": "in_review"}, hmac_key=key)
            e2 = build_event(e1["event_hash"], "r1", "approve", "2026-10-31T10:00:00Z", {"to": "approved"}, hmac_key=key)
            ok = self._run(self._approved(audit_events=[e1, e2]), audit_hmac_key=key, require_signed_audit=True)
            self.assertEqual(ok["governance_status"], "checks_passed")
            self.assertEqual(self._run(self._approved(), audit_hmac_key=key)["governance_status"], "blocked")

        def test_require_audit_anchor_blocks_unanchored(self):
            self.assertEqual(self._run(self._approved(), require_audit_anchor=True)["governance_status"], "blocked")
            claim = self._approved()
            claim["audit_anchor"] = claim["audit_events"][-1]["event_hash"]
            self.assertEqual(self._run(claim, require_audit_anchor=True)["governance_status"], "checks_passed")

        def test_csv_formula_injection_neutralised(self):
            import tempfile
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                res = analyze([], [{"claim_id": "=HYPERLINK(1)", "card_id": "K", "claim": "x", "status": "draft"}],
                              as_of=date(2026, 10, 31), max_age_days=730, minimum_reviewers=1,
                              require_registered_sources=False)
                write_outputs(res, Path(d))
                text = (Path(d) / "v15_findings.csv").read_text(encoding="utf-8")
                self.assertNotIn("\n=HYPERLINK", text)
                self.assertIn("'=HYPERLINK", text)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(V15Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 2


run_self_test = self_test  # name used by the V14 regression gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Usiphus V15 evidence governance hardening")
    parser.add_argument("--sources", help="JSON source registry (list or {'sources': [...]})")
    parser.add_argument("--claims", help="JSON claim registry (list or {'claims': [...]})")
    parser.add_argument("--output-dir", default="./build-v15")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--max-review-age-days", type=int, default=730)
    parser.add_argument("--minimum-reviewers", type=int, default=1)
    parser.add_argument("--require-registered-sources", action="store_true")
    parser.add_argument("--require-signed-audit", action="store_true", help="Fail unless an HMAC key is configured when claims are approved/published")
    parser.add_argument("--require-audit-anchor", action="store_true", help="Approved/published claims must carry audit_anchor")
    parser.add_argument("--audit-hmac-key-env", default=AUDIT_KEY_ENV,
                        help="Environment variable holding the HMAC key; when set, audit events must be signed")
    parser.add_argument("--audit-hmac-key-file", help="File containing the HMAC key (alternative to the environment variable)")
    parser.add_argument("--audit-trusted-keys", default=os.environ.get(TRUSTED_KEYS_ENV),
                        help=f"JSON file of trusted Ed25519 PUBLIC keys per actor (env: {TRUSTED_KEYS_ENV})")
    parser.add_argument("--anchors", help="Signed anchor file (from usiphus_audit.py anchors); overrides inline audit_anchor")
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
        verifier = verifier_from_path(args.audit_trusted_keys)
        result = analyze(source_rows, claim_rows, as_of=date.fromisoformat(args.as_of),
                         max_age_days=args.max_review_age_days,
                         minimum_reviewers=args.minimum_reviewers,
                         require_registered_sources=args.require_registered_sources,
                         audit_hmac_key=(audit_key_from_file(args.audit_hmac_key_file) if args.audit_hmac_key_file
                                         else audit_key_from_env(args.audit_hmac_key_env)),
                         require_signed_audit=args.require_signed_audit,
                         require_audit_anchor=args.require_audit_anchor,
                         audit_verifier=verifier,
                         external_anchors=anchors_from_path(args.anchors, verifier))
        write_outputs(result, Path(args.output_dir))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["governance_status"] == "checks_passed" else 1
    except (OSError, V15Error, TypeError) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
