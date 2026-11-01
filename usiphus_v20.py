#!/usr/bin/env python3
"""Usiphus V20 — privacy-aware feedback-to-action and faculty authoring workflows.

Local-first and standard-library-only. Feedback is minimized in reports; human
review and institutional data governance remain mandatory.
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
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "20.0.0"
MAX_BYTES = 25 * 1024 * 1024
ARTIFACT_TYPES = {"card", "case", "objective", "evidence_link", "mapping",
                  "challenge_pack", "osce", "mcq"}
ARTIFACT_STATES = {"draft", "in_review", "approved", "retired"}
ACTION_STATES = {"open", "in_progress", "done", "deferred"}
FEEDBACK_ROLES = {"learner", "faculty"}
VISIBILITY = {"faculty_only", "feedback_team", "submitter_and_faculty"}
PII_PATTERNS = {
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "phone_candidate": re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)"),
    "national_id_candidate": re.compile(r"(?<!\d)\d{9,14}(?!\d)"),
}

class V20Error(ValueError):
    """Invalid V20 input or workflow state."""

def clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())

def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def parse_time(value: Any) -> datetime | None:
    if not clean(value):
        return None
    try:
        parsed = datetime.fromisoformat(clean(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None

def pii_kinds(value: Any) -> list[str]:
    raw = str(value or "")
    return [name for name, pattern in PII_PATTERNS.items() if pattern.search(raw)]

def atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def write_json(value: Any, path: Path) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

def read_input(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise V20Error(f"Input file not found: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise V20Error(f"Input exceeds {MAX_BYTES // (1024 * 1024)} MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V20Error(f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise V20Error("Input must be an object with schema_version=1")
    return payload

def object_list(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    values = payload.get(field, [])
    if not isinstance(values, list) or any(not isinstance(x, dict) for x in values):
        raise V20Error(f"{field} must be a list of objects")
    return values

def issue(kind: str, ident: str, severity: str, message: str) -> dict[str, str]:
    return {"kind": kind, "record_id": ident, "severity": severity, "message": message}

def unique_index(items: list[dict[str, Any]], key: str, kind: str,
                 issues: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    result = {}
    for i, item in enumerate(items):
        ident = clean(item.get(key))
        if not ident:
            issues.append(issue(kind, f"row-{i+1}", "error", f"{key} is required"))
        elif ident in result:
            issues.append(issue(kind, ident, "error", f"duplicate {key}"))
        else:
            result[ident] = item
    return result

def validate_feedback(feedback: list[dict[str, Any]], issues: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    index = unique_index(feedback, "feedback_id", "feedback", issues)
    for item in feedback:
        ident = clean(item.get("feedback_id"))
        if clean(item.get("role")) not in FEEDBACK_ROLES:
            issues.append(issue("feedback", ident, "error", "role must be learner or faculty"))
        if not clean(item.get("purpose")):
            issues.append(issue("feedback", ident, "error", "purpose is required"))
        if item.get("notice_acknowledged") is not True:
            issues.append(issue("feedback", ident, "error", "notice_acknowledged must be true"))
        if clean(item.get("visibility")) not in VISIBILITY:
            issues.append(issue("feedback", ident, "error", "visibility must use an allowed restricted audience"))
        if not parse_time(item.get("submitted_at")):
            issues.append(issue("feedback", ident, "error", "submitted_at must be timezone-aware ISO-8601"))
        retention = parse_time(item.get("retention_until"))
        if not retention:
            issues.append(issue("feedback", ident, "error", "retention_until must be timezone-aware ISO-8601"))
        elif parse_time(item.get("submitted_at")) and retention <= parse_time(item.get("submitted_at")):
            issues.append(issue("feedback", ident, "error", "retention_until must be after submitted_at"))
        body = clean(item.get("text"))
        if not body:
            issues.append(issue("feedback", ident, "error", "feedback text is required"))
        found = pii_kinds(body)
        if found:
            issues.append(issue("feedback", ident, "warning", "possible personal data in free text (" + ", ".join(found) + "); restrict access and redact before use"))
        confidence = item.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, int) or not 1 <= confidence <= 5):
            issues.append(issue("feedback", ident, "error", "confidence must be an integer from 1 to 5"))
    return index

def validate_actions(actions: list[dict[str, Any]], feedback_index: dict[str, Any],
                     issues: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    index = unique_index(actions, "action_id", "action", issues)
    for action in actions:
        ident = clean(action.get("action_id"))
        links = action.get("feedback_ids", [])
        if not isinstance(links, list) or not links:
            issues.append(issue("action", ident, "error", "feedback_ids must be a non-empty list"))
        else:
            for fid in links:
                if clean(fid) not in feedback_index:
                    issues.append(issue("action", ident, "error", f"unknown feedback_id: {fid}"))
        if not clean(action.get("title")) or not clean(action.get("next_step")):
            issues.append(issue("action", ident, "error", "title and next_step are required"))
        if not clean(action.get("owner_label")):
            issues.append(issue("action", ident, "warning", "owner_label is missing"))
        if clean(action.get("status")) not in ACTION_STATES:
            issues.append(issue("action", ident, "error", "unsupported action status"))
        for field in ("created_at", "due_at"):
            if not parse_time(action.get(field)):
                issues.append(issue("action", ident, "error", f"{field} must be timezone-aware ISO-8601"))
        if clean(action.get("status")) == "done":
            if not clean(action.get("completion_evidence")) or not clean(action.get("follow_up_outcome")):
                issues.append(issue("action", ident, "error", "done actions require completion_evidence and follow_up_outcome"))
        if pii_kinds(action.get("completion_evidence")) or pii_kinds(action.get("follow_up_outcome")):
            issues.append(issue("action", ident, "warning", "possible personal data in action evidence/outcome; redact before use"))
    return index

def version_key(item: dict[str, Any]) -> tuple[Any, ...]:
    value = clean(item.get("version"))
    if re.fullmatch(r"v?\d+(?:\.\d+){1,3}", value):
        return (0, tuple(int(part) for part in value.lstrip("v").split(".")))
    return (1, value.casefold())

def validate_authoring(artifacts: list[dict[str, Any]], comments: list[dict[str, Any]],
                       issues: list[dict[str, str]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen_versions = set()
    for i, artifact in enumerate(artifacts):
        aid = clean(artifact.get("artifact_id"))
        version = clean(artifact.get("version"))
        key = (aid, version)
        if not aid or not version:
            issues.append(issue("artifact", aid or f"row-{i+1}", "error", "artifact_id and version are required"))
            continue
        if key in seen_versions:
            issues.append(issue("artifact", aid, "error", f"duplicate artifact version: {version}"))
        seen_versions.add(key)
        kind = clean(artifact.get("artifact_type"))
        if kind not in ARTIFACT_TYPES:
            issues.append(issue("artifact", aid, "error", f"unsupported artifact_type: {kind}"))
        status = clean(artifact.get("status") or "draft")
        if status not in ARTIFACT_STATES:
            issues.append(issue("artifact", aid, "error", f"unsupported status: {status}"))
        if not clean(artifact.get("title")):
            issues.append(issue("artifact", aid, "error", "title is required"))
        if not isinstance(artifact.get("content"), (dict, list, str)):
            issues.append(issue("artifact", aid, "error", "content must be a JSON object, array or string"))
        if status == "approved":
            if not clean(artifact.get("reviewer_label")) or not parse_time(artifact.get("reviewed_at")):
                issues.append(issue("artifact", aid, "error", "approved artifact requires reviewer_label and timezone-aware reviewed_at"))
        if pii_kinds(artifact.get("content")):
            issues.append(issue("artifact", aid, "warning", "possible personal data in authored content; inspect before sharing"))
        grouped.setdefault(aid, []).append(artifact)
    diffs = []
    for aid, versions in grouped.items():
        ordered = sorted(versions, key=version_key)
        for previous, current in zip(ordered, ordered[1:]):
            old = previous.get("content")
            new = current.get("content")
            old_map = old if isinstance(old, dict) else {"content": old}
            new_map = new if isinstance(new, dict) else {"content": new}
            changed = sorted(k for k in set(old_map) | set(new_map) if old_map.get(k) != new_map.get(k))
            diffs.append({"artifact_id": aid, "from_version": clean(previous.get("version")),
                          "to_version": clean(current.get("version")), "changed_fields": changed,
                          "review_required": clean(current.get("status")) != "approved"})
    known = {(clean(a.get("artifact_id")), clean(a.get("version"))) for a in artifacts}
    for comment in comments:
        cid = clean(comment.get("comment_id"))
        if not cid or not clean(comment.get("artifact_id")) or not clean(comment.get("version")):
            issues.append(issue("comment", cid or "unknown", "error", "comment_id, artifact_id and version are required"))
        elif (clean(comment.get("artifact_id")), clean(comment.get("version"))) not in known:
            issues.append(issue("comment", cid, "error", "comment references an unknown artifact version"))
        if not clean(comment.get("text")):
            issues.append(issue("comment", cid, "error", "comment text is required"))
        if pii_kinds(comment.get("text")):
            issues.append(issue("comment", cid, "warning", "possible personal data in comment; redact before use"))
    return grouped, diffs

def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise V20Error("input must be an object with schema_version=1")
    as_of = parse_time(payload.get("as_of")) if payload.get("as_of") else None
    if payload.get("as_of") and not as_of:
        raise V20Error("as_of must be timezone-aware ISO-8601")
    feedback = object_list(payload, "feedback")
    actions = object_list(payload, "actions")
    artifacts = object_list(payload, "artifacts")
    comments = object_list(payload, "comments")
    issues: list[dict[str, str]] = []
    f_index = validate_feedback(feedback, issues)
    a_index = validate_actions(actions, f_index, issues)
    grouped, diffs = validate_authoring(artifacts, comments, issues)
    error_count = sum(x["severity"] == "error" for x in issues)
    warning_count = sum(x["severity"] == "warning" for x in issues)
    action_summary = {state: sum(clean(a.get("status")) == state for a in actions) for state in sorted(ACTION_STATES)}
    artifact_summary = {
        "total_versions": len(artifacts),
        "unique_artifacts": len(grouped),
        "approved_versions": sum(clean(a.get("status")) == "approved" for a in artifacts),
        "awaiting_review": sum(clean(a.get("status") or "draft") in {"draft", "in_review"} for a in artifacts),
    }
    # Never echo feedback text, comments or learner identifiers into the report.
    feedback_summary = {
        "total": len(feedback),
        "by_role": {role: sum(clean(x.get("role")) == role for x in feedback) for role in sorted(FEEDBACK_ROLES)},
        "with_possible_pii": sum(bool(pii_kinds(x.get("text"))) for x in feedback),
        "retention_due_count": (sum(bool(parse_time(x.get("retention_until")) and parse_time(x.get("retention_until")) <= as_of) for x in feedback) if as_of else None),
    }
    summary = {"schema_version": 1, "engine_version": VERSION, "input_sha256": digest(payload),
               "feedback": feedback_summary, "actions": action_summary, "artifacts": artifact_summary,
               "issue_counts": {"error": error_count, "warning": warning_count},
               "version_diffs": len(diffs)}
    queue = sorted(issues, key=lambda x: (0 if x["severity"] == "error" else 1, x["kind"], x["record_id"]))
    preview = []
    for aid, versions in sorted(grouped.items()):
        latest = sorted(versions, key=version_key)[-1]
        preview.append({"artifact_id": aid, "version": clean(latest.get("version")),
                        "artifact_type": clean(latest.get("artifact_type")),
                        "title": clean(latest.get("title")), "status": clean(latest.get("status") or "draft"),
                        "content_sha256": digest(latest.get("content"))})
    return {"schema_version": 1, "engine_version": VERSION, "summary": summary,
            "issues": queue, "version_diffs": diffs, "release_preview": preview,
            "publication_gate": "blocked" if error_count else ("review_required" if warning_count else "ready_for_faculty_review"),
            "governance_note": "Reports exclude raw feedback and comments. A clean structural report is not faculty approval, learner-consent verification, privacy-law compliance, educational effectiveness or proof of competence. Reviewer/owner labels are not authenticated identities."}

def html_preview(report: dict[str, Any]) -> str:
    cards = []
    for item in report["release_preview"]:
        cards.append("<article><h2>" + html.escape(item["title"]) + "</h2><p>" +
                     html.escape(item["artifact_type"] + " · " + item["artifact_id"] + " · " +
                                 item["version"] + " · " + item["status"]) + "</p></article>")
    return ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Usiphus V20 release preview</title><main><h1>V20 Faculty Release Preview</h1>"
            "<p>Preview only — not approved for publication.</p>" + "".join(cards) +
            "<p>" + html.escape(report["governance_note"]) + "</p></main></html>")

def self_test() -> int:
    sample = {"schema_version": 1,
      "feedback":[{"feedback_id":"F1","role":"faculty","purpose":"Improve synthetic teaching case","notice_acknowledged":True,"visibility":"faculty_only","submitted_at":"2026-11-01T10:00:00Z","retention_until":"2027-11-01T10:00:00Z","text":"The synthetic case needs a clearer explanation."}],
      "actions":[{"action_id":"A1","feedback_ids":["F1"],"title":"Revise explanation","next_step":"Draft a clearer rationale","owner_label":"faculty-editor","status":"open","created_at":"2026-11-01T11:00:00Z","due_at":"2026-11-08T11:00:00Z"}],
      "artifacts":[{"artifact_id":"CARD-1","version":"1.0.0","artifact_type":"card","title":"Synthetic teaching card","status":"draft","content":{"body":"Synthetic educational content"}}],
      "comments":[{"comment_id":"C1","artifact_id":"CARD-1","version":"1.0.0","text":"Please add a source reference."}]}
    report = build_report(sample)
    assert report["publication_gate"] == "ready_for_faculty_review", report["issues"]
    assert "The synthetic case" not in canonical(report)
    sample["feedback"][0]["text"] = "Contact me at student@example.org"
    assert any("possible personal data" in x["message"] for x in build_report(sample)["issues"])
    sample["actions"][0]["feedback_ids"] = ["UNKNOWN"]
    assert build_report(sample)["publication_gate"] == "blocked"
    sample["actions"][0]["feedback_ids"] = ["F1"]
    sample["artifacts"].append({"artifact_id":"CARD-1","version":"1.1.0","artifact_type":"card","title":"Synthetic teaching card","status":"in_review","content":{"body":"Updated content"}})
    assert build_report(sample)["version_diffs"][0]["changed_fields"] == ["body"]
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build-v20"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        if not args.input:
            parser.error("--input is required unless --self-test is used")
        payload = read_input(args.input)
        report = build_report(payload)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(report, args.output_dir / "v20_feedback_authoring_report.json")
        # Structured authoring export deliberately excludes feedback/comment text.
        authored = {"schema_version": 1, "artifacts": payload.get("artifacts", [])}
        write_json(authored, args.output_dir / "v20_authoring_export.json")
        atomic_write(args.output_dir / "v20_release_preview.html", html_preview(report))
        with (args.output_dir / "v20_action_queue.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["action_id","title","owner_label","status","due_at","feedback_count"])
            writer.writeheader()
            for action in object_list(payload, "actions"):
                writer.writerow({"action_id":clean(action.get("action_id")), "title":clean(action.get("title")),
                                 "owner_label":clean(action.get("owner_label")), "status":clean(action.get("status")),
                                 "due_at":clean(action.get("due_at")), "feedback_count":len(action.get("feedback_ids",[])) if isinstance(action.get("feedback_ids"),list) else 0})
        print(json.dumps(report["summary"], indent=2))
        return 2 if report["publication_gate"] == "blocked" else 0
    except (V20Error, OSError, ValueError) as exc:
        print(f"V20 error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
